"""A fake IMAP server for the poller's tests — deliberately protocol-accurate, not convenient.

Shared by ``test_imap_poll.py`` (the seam) and ``test_imap_poll_handler.py`` (the loop). It exists
because the interesting bugs in an IMAP client are all in places where the protocol behaves
unlike the obvious mental model, and a fake written to the mental model cannot catch any of them.

Three behaviours it reproduces on purpose:

- **``UID n:*`` returns the boundary UID even when ``n`` exceeds it.** ``*`` means "the highest UID
  in use", and a backwards range normalizes, so ``901:*`` on a folder topping out at 900 is the
  same set as ``900:901`` and matches UID 900. A fake that simply returned "UIDs above n" would
  pass whether or not :data:`common.imap_poll.MAX_UID` bounds the range — and would keep passing
  if someone replaced it with ``*``, reintroducing a phantom result on every quiet poll.
- **``FETCH`` refuses anything but ``BODY.PEEK[]``.** Asking for ``BODY[]`` or ``RFC822`` sets
  ``\\Seen`` on a real server, which would mark Donna's unread mail as read in Outlook. The fake
  records such a request in :attr:`FakeIMAP.marked_seen` so a test can assert it never happened.
- **``MOVE`` and UID ``EXPUNGE`` are capability-gated.** Both can be switched off
  (``supports_move``, ``supports_uidplus``) to exercise the RFC 3501 fallback, which no real
  WorkMail connection would ever take.

It is not a general IMAP implementation: it knows only the operations ``common.imap`` and
``common.imap_poll`` actually issue, and asserts loudly when given something it does not model.
"""

from __future__ import annotations

import datetime as dt

from imapclient.exceptions import CapabilityError, IMAPClientError

#: Folder names as they exist on the live mailbox — the Sent folder really is ``Sent Items`` and no
#: folder called ``Sent`` exists, which is why discovery goes through the ``\Sent`` flag.
SENT_FOLDER = "Sent Items"
IMPORT_FOLDER = "Speaker Tracker/Import"
PROCESSED_FOLDER = "Speaker Tracker/Processed"

#: Default UID generation. Bump a folder's entry to simulate the server recreating it.
DEFAULT_UID_VALIDITY = 42


def build_message(
    *,
    message_id: str,
    from_addr: str,
    to_addr: str = "donna@360balancedliving.com",
    subject: str = "Speaking inquiry",
    date: str = "Mon, 27 Jul 2026 10:00:00 -0400",
    in_reply_to: str | None = None,
    references: str | None = None,
    cc_addr: str | None = None,
    body: str = "Hello.",
) -> bytes:
    """Build a small RFC 5322 message. Headers are written as a real client would send them."""
    lines = [
        f"Message-ID: {message_id}",
        f"From: {from_addr}",
        f"To: {to_addr}",
        f"Subject: {subject}",
        f"Date: {date}",
    ]
    if cc_addr:
        lines.append(f"Cc: {cc_addr}")
    if in_reply_to:
        lines.append(f"In-Reply-To: {in_reply_to}")
    if references:
        lines.append(f"References: {references}")
    lines += ["Content-Type: text/plain; charset=utf-8", "", body]
    return "\r\n".join(lines).encode()


class FakeIMAP:
    """An in-memory IMAP server covering exactly the operations the app issues.

    Attributes
    ----------
    folders : dict
        ``{folder_name: {uid: (raw_bytes, internaldate)}}``.
    uid_validity : dict
        ``{folder_name: int}``. Change an entry to simulate a folder being recreated.
    searches : list
        Every criteria list issued, so a test can assert the poller never used a ``*`` range.
    marked_seen : list
        UIDs the caller would have flagged ``\\Seen`` by fetching without ``PEEK``. Must stay empty.
    supports_move, supports_uidplus : bool
        Capability switches for exercising the ``COPY`` + ``\\Deleted`` + ``EXPUNGE`` fallback.
    """

    def __init__(self, *, supports_move: bool = True, supports_uidplus: bool = True) -> None:
        self.folders: dict[str, dict[int, tuple[bytes, dt.datetime | None]]] = {
            "INBOX": {},
            SENT_FOLDER: {},
            IMPORT_FOLDER: {},
            PROCESSED_FOLDER: {},
        }
        self.uid_validity: dict[str, int] = dict.fromkeys(self.folders, DEFAULT_UID_VALIDITY)
        self.selected: str | None = None
        self.readonly: bool | None = None
        self.searches: list[list] = []
        self.marked_seen: list[int] = []
        self.deleted_flagged: dict[str, set[int]] = {name: set() for name in self.folders}
        self.subscribed: set[str] = set()
        self.logged_out = False
        self.supports_move = supports_move
        self.supports_uidplus = supports_uidplus

    # -- test helpers -------------------------------------------------------------------------

    def add(
        self,
        folder: str,
        uid: int,
        raw: bytes,
        internaldate: dt.datetime | None = None,
    ) -> int:
        """Place a message in a folder at a specific UID and return that UID."""
        self.folders[folder][uid] = (raw, internaldate)
        return uid

    def uids_in(self, folder: str) -> list[int]:
        """Return the UIDs currently present in `folder`, ascending."""
        return sorted(self.folders[folder])

    # -- connection ---------------------------------------------------------------------------

    def login(self, username: str, password: str) -> bytes:
        return b"OK"

    def logout(self) -> bytes:
        self.logged_out = True
        return b"BYE"

    def has_capability(self, name: str) -> bool:
        if name.upper() == "MOVE":
            return self.supports_move
        if name.upper() == "UIDPLUS":
            return self.supports_uidplus
        return False

    # -- folder topology ----------------------------------------------------------------------

    def list_folders(self, directory: str = "", pattern: str = "*") -> list:
        """Return ``(flags, delimiter, name)`` with flags and delimiter as **bytes**, as the real
        library does — the mismatch that made SPECIAL-USE discovery silently degrade once."""
        listing = []
        for name in self.folders:
            flags = [b"\\HasNoChildren"]
            if name == SENT_FOLDER:
                flags.append(b"\\Sent")
            listing.append((flags, b"/", name))
        return listing

    def create_folder(self, name: str) -> bytes:
        self.folders.setdefault(name, {})
        self.uid_validity.setdefault(name, DEFAULT_UID_VALIDITY)
        self.deleted_flagged.setdefault(name, set())
        return b"OK"

    def subscribe_folder(self, name: str) -> bytes:
        if name not in self.folders:
            raise IMAPClientError(f"cannot subscribe to missing folder {name}")
        self.subscribed.add(name)
        return b"OK"

    # -- the operations under test --------------------------------------------------------------

    def select_folder(self, folder: str, readonly: bool = True) -> dict:
        if folder not in self.folders:
            raise IMAPClientError(f"no such folder: {folder}")
        self.selected = folder
        self.readonly = readonly
        uids = self.folders[folder]
        return {
            b"EXISTS": len(uids),
            b"FLAGS": (b"\\Seen", b"\\Deleted"),
            b"UIDVALIDITY": self.uid_validity[folder],
            b"UIDNEXT": (max(uids) + 1) if uids else 1,
        }

    def search(self, criteria: list) -> list[int]:
        """Search by UID range, honouring the ``*`` normalization that trips real clients."""
        self.searches.append(list(criteria))
        assert criteria[0] == "UID", f"fake only models UID searches, got {criteria!r}"
        low_text, high_text = str(criteria[1]).split(":")
        low = int(low_text)
        present = self.uids_in(self.selected)

        if high_text == "*":
            if not present:
                return []
            # `*` resolves to the highest UID; `low:high` with low > high normalizes to `high:low`.
            star = present[-1]
            lower, upper = (low, star) if low <= star else (star, low)
            return [uid for uid in present if lower <= uid <= upper]

        high = int(high_text)
        return [uid for uid in present if low <= uid <= high]

    def fetch(self, uids: list[int], items: list[str]) -> dict:
        if "BODY[]" in items or "RFC822" in items:
            # A real server sets \Seen for these. Record it so a test can prove we never do.
            self.marked_seen.extend(uids)
        assert "BODY.PEEK[]" in items, f"fetch must PEEK to avoid setting \\Seen, got {items!r}"
        response = {}
        for uid in uids:
            stored = self.folders[self.selected].get(uid)
            if stored is None:
                continue  # vanished between SEARCH and FETCH; the caller must tolerate this
            raw, internaldate = stored
            # The server answers under BODY[] whichever form was requested.
            response[uid] = {b"BODY[]": raw, b"INTERNALDATE": internaldate}
        return response

    def move(self, uids: list[int], destination: str) -> bytes:
        if not self.supports_move:
            raise CapabilityError("server does not support MOVE")
        self._copy(uids, destination)
        for uid in list(uids):
            self.folders[self.selected].pop(uid, None)
        return b"OK"

    def copy(self, uids: list[int], destination: str) -> bytes:
        self._copy(uids, destination)
        return b"OK"

    def _copy(self, uids: list[int], destination: str) -> None:
        if destination not in self.folders:
            raise IMAPClientError(f"no such destination: {destination}")
        for uid in uids:
            stored = self.folders[self.selected].get(uid)
            if stored is None:
                continue
            next_uid = (max(self.folders[destination]) + 1) if self.folders[destination] else 1
            self.folders[destination][next_uid] = stored

    def add_flags(self, uids: list[int], flags: list) -> bytes:
        for flag in flags:
            name = flag.decode() if isinstance(flag, bytes) else str(flag)
            if name == "\\Deleted":
                self.deleted_flagged[self.selected].update(uids)
        return b"OK"

    def expunge(self, messages: list[int] | None = None) -> bytes:
        if messages is not None and not self.supports_uidplus:
            raise CapabilityError("server does not support UIDPLUS")
        folder = self.folders[self.selected]
        flagged = self.deleted_flagged[self.selected]
        # A plain EXPUNGE purges every \Deleted message; a UID EXPUNGE only the named ones. The
        # difference is the whole reason move_uids checks the capability.
        targets = flagged if messages is None else (flagged & set(messages))
        for uid in list(targets):
            folder.pop(uid, None)
            flagged.discard(uid)
        return b"OK"

    def append(self, folder: str, raw: bytes, flags: list | None = None) -> bytes:
        if folder not in self.folders:
            raise IMAPClientError(f"no such folder: {folder}")
        next_uid = (max(self.folders[folder]) + 1) if self.folders[folder] else 1
        self.folders[folder][next_uid] = (raw, dt.datetime(2026, 7, 27, 12, 0))
        return b"OK"
