"""IMAP tests against a fake server — no network, no AWS, no database.

``common.imap`` is replaced at two seams: ``_connect`` (the socket) and the credentials fetch, so
these exercise the real protocol handling against a scripted ``IMAPClient``.

What they pin, and why each earns its place:

- **discovery, not guessing** — Sent is found by the ``\\Sent`` SPECIAL-USE flag and the hierarchy
  delimiter is read from the server, because a hardcoded name is localized-away and a hardcoded
  ``/`` silently creates one flat folder named ``Speaker Tracker/Import``;
- **idempotent folder setup** (acceptance #4 and #5) — including the server that rejects CREATE
  because the folder already exists;
- **the best-effort contract** (decision #2) — after SES has accepted a message, no mailbox
  problem may raise, and the WARNING must say the mail was still sent;
- **the credential never reaches a log line**, even when login is rejected;
- **the two type hazards of the ``imapclient`` port** — LIST returns flags and delimiters as
  *bytes* while names are ``str``, and folder names must reach the library *unquoted* because it
  applies quoting and modified UTF-7 itself.
"""

from __future__ import annotations

import logging

import pytest
from imapclient import SEEN
from imapclient.exceptions import IMAPClientError, LoginError

from common import imap, secrets

HOST = "imap.mail.us-east-1.awsapps.com"
USERNAME = "donna.king@360balancedliving.com"
PASSWORD = "hunter2-do-not-leak"

#: A WorkMail-shaped LIST response as ``IMAPClient.list_folders`` actually returns it: flags and
#: delimiter as bytes, name already decoded to str, Sent carrying the SPECIAL-USE flag.
WORKMAIL_LIST = [
    ((b"\\HasNoChildren",), b"/", "INBOX"),
    ((b"\\HasNoChildren", b"\\Sent"), b"/", "Sent Items"),
    ((b"\\HasNoChildren", b"\\Drafts"), b"/", "Drafts"),
]


class FakeImap:
    """Scripted stand-in for ``imapclient.IMAPClient``."""

    def __init__(
        self,
        *,
        listing: list[tuple] | None = None,
        login_error: Exception | None = None,
        list_error: Exception | None = None,
        create_error: Exception | None = None,
        subscribe_error: Exception | None = None,
        append_error: Exception | None = None,
        creates_actually_work: bool = True,
    ) -> None:
        self.listing = list(WORKMAIL_LIST if listing is None else listing)
        self.login_error = login_error
        self.list_error = list_error
        self.create_error = create_error
        self.subscribe_error = subscribe_error
        self.append_error = append_error
        self.creates_actually_work = creates_actually_work
        self.logins: list[tuple[str, str]] = []
        self.created: list[str] = []
        self.subscribed: list[str] = []
        self.appends: list[tuple[str, list, bytes]] = []
        self.logged_out = False

    def login(self, username: str, password: str):
        self.logins.append((username, password))
        if self.login_error is not None:
            raise self.login_error
        return b"logged in"

    def list_folders(self, directory: str = "", pattern: str = "*"):
        if self.list_error is not None:
            raise self.list_error
        return list(self.listing)

    def create_folder(self, name: str):
        self.created.append(name)
        if self.creates_actually_work:
            self.listing.append(((b"\\HasNoChildren",), b"/", name))
        if self.create_error is not None:
            raise self.create_error
        return b"created"

    def subscribe_folder(self, name: str):
        self.subscribed.append(name)
        if self.subscribe_error is not None:
            raise self.subscribe_error
        return b"subscribed"

    def append(self, folder: str, msg: bytes, flags=(), msg_time=None):
        self.appends.append((folder, list(flags), msg))
        if self.append_error is not None:
            raise self.append_error
        return b"appended"

    def logout(self):
        self.logged_out = True
        return b"bye"


@pytest.fixture(autouse=True)
def configured(monkeypatch: pytest.MonkeyPatch):
    """Point the module at a fake host and short-circuit the secret fetch."""
    monkeypatch.setenv(imap.IMAP_HOST_ENV, HOST)
    monkeypatch.setattr(
        imap,
        "get_imap_credentials",
        lambda refresh=False: secrets.ImapCredentials(username=USERNAME, password=PASSWORD),
    )
    yield
    secrets.reset_cache()


def install(monkeypatch: pytest.MonkeyPatch, server: FakeImap) -> FakeImap:
    """Install a fake server at the socket seam."""
    monkeypatch.setattr(imap, "_connect", lambda host: server)
    return server


# --- configuration ------------------------------------------------------------------------------


def test_missing_host_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(imap.IMAP_HOST_ENV, raising=False)
    with pytest.raises(RuntimeError, match=imap.IMAP_HOST_ENV):
        imap.imap_host()


def test_timeout_stays_inside_the_lambda_budget() -> None:
    # The API Lambda has 15s; a hung mailbox must degrade to a WARNING, not eat the request.
    assert imap.IMAP_TIMEOUT_S < 15


# --- connection ---------------------------------------------------------------------------------


def test_connection_logs_in_and_always_logs_out(monkeypatch: pytest.MonkeyPatch) -> None:
    server = install(monkeypatch, FakeImap())

    with imap.connection() as conn:
        assert conn is server

    assert server.logins == [(USERNAME, PASSWORD)]
    assert server.logged_out is True


def test_connection_logs_out_even_when_the_body_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    server = install(monkeypatch, FakeImap())

    with pytest.raises(ZeroDivisionError), imap.connection():
        raise ZeroDivisionError

    assert server.logged_out is True


def test_rejected_login_raises_imap_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # LoginError is a distinct type, so auth failure is told apart from generic mailbox noise by
    # the exception class rather than by which call happened to raise (acceptance #11).
    install(monkeypatch, FakeImap(login_error=LoginError("AUTHENTICATIONFAILED")))

    with pytest.raises(imap.ImapAuthError):
        with imap.connection():
            pass


def test_non_auth_login_failure_is_not_reported_as_an_auth_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A protocol error during login must NOT alarm as a rotated password: #11's alarm has to stay
    # specific or it becomes noise that gets muted.
    install(monkeypatch, FakeImap(login_error=IMAPClientError("server confused")))

    with pytest.raises(imap.ImapError) as excinfo:
        with imap.connection():
            pass

    assert not isinstance(excinfo.value, imap.ImapAuthError)


#: The exact rejection WorkMail sent on 2026-08-02, including the bytes repr `imapclient` produces
#: and the trailing timestamp that a naive bracket match would read as a response code.
WORKMAIL_TRANSIENT = "b'[UNAVAILABLE] Temporary authentication failure. [2026-08-03 06:00:06]'"


def test_a_transient_rejection_is_not_an_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """The false alarm of 2026-08-02: WorkMail was busy, not the password wrong.

    Raised as a plain ImapError so the poller skips the cycle and lets the next minute retry,
    rather than failing the invocation and firing an alarm whose advice was to check a rotated
    password — for a secret that does not rotate.
    """
    install(monkeypatch, FakeImap(login_error=LoginError(WORKMAIL_TRANSIENT)))

    with pytest.raises(imap.ImapError) as excinfo:
        with imap.connection():
            pass

    assert not isinstance(excinfo.value, imap.ImapAuthError)


@pytest.mark.parametrize("code", sorted(imap.TRANSIENT_LOGIN_CODES))
def test_every_transient_code_is_treated_as_transient(
    monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    install(monkeypatch, FakeImap(login_error=LoginError(f"[{code}] server says wait")))

    with pytest.raises(imap.ImapError) as excinfo:
        with imap.connection():
            pass

    assert not isinstance(excinfo.value, imap.ImapAuthError)


@pytest.mark.parametrize(
    "message",
    [
        "[AUTHENTICATIONFAILED] Invalid credentials",
        "[EXPIRED] That password is no longer accepted",
        "Login failed.",  # no response code at all
        "[2026-08-03 06:00:06] no letters, so not a code",
    ],
)
def test_anything_not_recognisably_transient_still_alarms(
    monkeypatch: pytest.MonkeyPatch, message: str
) -> None:
    """Fails toward alarming. A false alarm is recoverable; a silenced one stops inbound mail."""
    install(monkeypatch, FakeImap(login_error=LoginError(message)))

    with pytest.raises(imap.ImapAuthError):
        with imap.connection():
            pass


def test_a_transient_rejection_never_logs_the_password(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    install(monkeypatch, FakeImap(login_error=LoginError(WORKMAIL_TRANSIENT)))

    with caplog.at_level(logging.DEBUG), pytest.raises(imap.ImapError) as excinfo:
        with imap.connection():
            pass

    assert PASSWORD not in caplog.text
    assert PASSWORD not in str(excinfo.value)


def test_rejected_login_never_logs_the_password(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    install(monkeypatch, FakeImap(login_error=LoginError("AUTHENTICATIONFAILED")))

    with caplog.at_level(logging.DEBUG), pytest.raises(imap.ImapAuthError) as excinfo:
        with imap.connection():
            pass

    assert PASSWORD not in caplog.text
    assert PASSWORD not in str(excinfo.value)


def test_socket_failure_raises_imap_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(host: str):
        raise TimeoutError("timed out")

    monkeypatch.setattr(imap, "_connect", boom)
    with pytest.raises(imap.ImapError, match="could not connect"):
        with imap.connection():
            pass


# --- discovery ----------------------------------------------------------------------------------


def test_sent_folder_is_found_by_special_use_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    server = install(monkeypatch, FakeImap())
    assert imap.find_sent_folder(server) == "Sent Items"


def test_special_use_flag_is_matched_despite_arriving_as_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The library returns flags as bytes while SENT_FLAG is str, and `b"\\Sent" in {"\\Sent"}` is
    # always False. Without normalization this would silently fall through to name-guessing.
    server = install(
        monkeypatch,
        FakeImap(listing=[((b"\\HasNoChildren", b"\\Sent"), b"/", "Archivo enviado")]),
    )
    assert imap.find_sent_folder(server) == "Archivo enviado"


def test_special_use_wins_over_a_conventionally_named_folder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A localized Sent folder carrying the flag must beat an unrelated folder literally named
    # "Sent" — the flag is the authority.
    server = install(
        monkeypatch,
        FakeImap(
            listing=[
                ((b"\\HasNoChildren",), b"/", "Sent"),
                ((b"\\HasNoChildren", b"\\Sent"), b"/", "Elementos enviados"),
            ]
        ),
    )
    assert imap.find_sent_folder(server) == "Elementos enviados"


def test_fallback_to_a_known_name_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Silent degradation here means sent mail quietly stops appearing in Outlook.
    server = install(
        monkeypatch,
        FakeImap(
            listing=[
                ((b"\\HasNoChildren",), b"/", "INBOX"),
                ((b"\\HasNoChildren",), b"/", "Sent Items"),
            ]
        ),
    )

    with caplog.at_level(logging.WARNING):
        assert imap.find_sent_folder(server) == "Sent Items"

    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_no_identifiable_sent_folder_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    server = install(monkeypatch, FakeImap(listing=[((b"\\HasNoChildren",), b"/", "INBOX")]))
    with pytest.raises(imap.ImapError, match="Sent folder"):
        imap.find_sent_folder(server)


def test_delimiter_is_read_from_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    # A '.'-delimited server must not get '/'-joined folder names. Also guards the bytes decode:
    # an undecoded b'.' would stringify into folder names as "b'.'".
    server = install(monkeypatch, FakeImap(listing=[((b"\\HasNoChildren",), b".", "INBOX")]))
    assert imap.folder_delimiter(server) == "."


def test_delimiter_defaults_when_the_server_reports_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # NIL delimiter — a flat namespace.
    server = install(monkeypatch, FakeImap(listing=[((b"\\HasNoChildren",), None, "INBOX")]))
    assert imap.folder_delimiter(server) == "/"


def test_failed_list_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    server = install(monkeypatch, FakeImap(list_error=IMAPClientError("denied")))
    with pytest.raises(imap.ImapError, match="LIST failed"):
        imap.folder_delimiter(server)


# --- APPEND -------------------------------------------------------------------------------------


def test_append_targets_the_sent_folder_and_marks_it_read(monkeypatch: pytest.MonkeyPatch) -> None:
    server = install(monkeypatch, FakeImap())

    folder = imap.append_to_sent(server, b"raw mime")

    assert folder == "Sent Items"
    appended_folder, flags, message = server.appends[0]
    assert appended_folder == "Sent Items"
    assert flags == [SEEN], "Donna sent it; it is not unread mail"
    assert message == b"raw mime"


def test_folder_name_reaches_the_library_unquoted(monkeypatch: pytest.MonkeyPatch) -> None:
    # IMAPClient applies quoting and modified UTF-7 in _normalise_folder. Pre-quoting the name —
    # as the imaplib version had to — would append into a folder whose name contains literal
    # quote characters, i.e. somewhere Donna will never look.
    server = install(monkeypatch, FakeImap())

    imap.append_to_sent(server, b"raw mime")

    assert server.appends[0][0] == "Sent Items"
    assert '"' not in server.appends[0][0]


def test_append_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    server = install(monkeypatch, FakeImap(append_error=IMAPClientError("over quota")))
    with pytest.raises(imap.ImapError, match="APPEND"):
        imap.append_to_sent(server, b"raw mime")


# --- folder setup (acceptance #4 / #5) ------------------------------------------------------------


def test_ensure_folder_creates_and_subscribes(monkeypatch: pytest.MonkeyPatch) -> None:
    server = install(monkeypatch, FakeImap())

    name = imap.ensure_folder(server, imap.IMPORT_FOLDER_PATH)

    assert name == "Speaker Tracker/Import"
    assert server.created == ["Speaker Tracker/Import"]
    # SUBSCRIBE is what makes it visible in Outlook — creation alone is not enough.
    assert server.subscribed == ["Speaker Tracker/Import"]


def test_ensure_folder_joins_with_the_server_delimiter(monkeypatch: pytest.MonkeyPatch) -> None:
    server = install(monkeypatch, FakeImap(listing=[((b"\\HasNoChildren",), b".", "INBOX")]))

    assert imap.ensure_folder(server, imap.IMPORT_FOLDER_PATH) == "Speaker Tracker.Import"


def test_ensure_folder_does_not_recreate_an_existing_folder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = install(
        monkeypatch,
        FakeImap(listing=[*WORKMAIL_LIST, ((b"\\HasNoChildren",), b"/", "Speaker Tracker/Import")]),
    )

    imap.ensure_folder(server, imap.IMPORT_FOLDER_PATH)

    assert server.created == []
    # Still subscribed: an existing but unsubscribed folder is invisible in Outlook.
    assert server.subscribed == ["Speaker Tracker/Import"]


def test_deleted_folder_is_recreated(monkeypatch: pytest.MonkeyPatch) -> None:
    # Acceptance #5: deleting the Import folder and re-polling recreates it.
    server = install(monkeypatch, FakeImap())
    imap.ensure_folder(server, imap.IMPORT_FOLDER_PATH)

    server.listing = list(WORKMAIL_LIST)  # someone deleted it in Outlook
    imap.ensure_folder(server, imap.IMPORT_FOLDER_PATH)

    assert server.created == ["Speaker Tracker/Import"] * 2


def test_create_rejected_is_tolerated_when_the_folder_exists(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Some servers reject CREATE for "already exists"; existence is re-checked, never trusted.
    server = install(monkeypatch, FakeImap(create_error=IMAPClientError("already exists")))

    with caplog.at_level(logging.WARNING):
        assert imap.ensure_folder(server, imap.IMPORT_FOLDER_PATH) == "Speaker Tracker/Import"

    assert server.subscribed == ["Speaker Tracker/Import"]
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_create_that_genuinely_fails_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    server = install(
        monkeypatch,
        FakeImap(create_error=IMAPClientError("denied"), creates_actually_work=False),
    )
    with pytest.raises(imap.ImapError, match="CREATE"):
        imap.ensure_folder(server, imap.IMPORT_FOLDER_PATH)


def test_failed_subscribe_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    server = install(monkeypatch, FakeImap(subscribe_error=IMAPClientError("denied")))
    with pytest.raises(imap.ImapError, match="SUBSCRIBE"):
        imap.ensure_folder(server, imap.IMPORT_FOLDER_PATH)


def test_ensure_app_folders_sets_up_both(monkeypatch: pytest.MonkeyPatch) -> None:
    server = install(monkeypatch, FakeImap())

    import_folder, processed_folder = imap.ensure_app_folders(server)

    assert import_folder == "Speaker Tracker/Import"
    assert processed_folder == "Speaker Tracker/Processed"
    assert server.subscribed == ["Speaker Tracker/Import", "Speaker Tracker/Processed"]


# --- best-effort wrapper (decision #2) ------------------------------------------------------------


def test_best_effort_appends_and_reports_success(monkeypatch: pytest.MonkeyPatch) -> None:
    server = install(monkeypatch, FakeImap())

    assert imap.append_to_sent_best_effort(b"raw mime") is True
    assert server.appends[0][2] == b"raw mime"


def test_best_effort_swallows_failure_into_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # SES has already accepted the message; nothing here may raise or roll anything back.
    install(monkeypatch, FakeImap(append_error=IMAPClientError("over quota")))

    with caplog.at_level(logging.WARNING):
        assert imap.append_to_sent_best_effort(b"raw mime") is False

    assert "WAS sent" in caplog.text, "the WARNING must say the mail still went out"


def test_best_effort_survives_a_connection_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(host: str):
        raise TimeoutError("timed out")

    monkeypatch.setattr(imap, "_connect", boom)
    assert imap.append_to_sent_best_effort(b"raw mime") is False


def test_best_effort_retries_once_with_refreshed_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The rotation case: the cached password is stale, the refreshed one works.
    servers = [FakeImap(login_error=LoginError("AUTHENTICATIONFAILED")), FakeImap()]
    monkeypatch.setattr(imap, "_connect", lambda host: servers.pop(0))
    refreshes: list[bool] = []
    monkeypatch.setattr(
        imap,
        "get_imap_credentials",
        lambda refresh=False: (
            refreshes.append(refresh),
            secrets.ImapCredentials(username=USERNAME, password=PASSWORD),
        )[1],
    )

    assert imap.append_to_sent_best_effort(b"raw mime") is True
    assert refreshes == [False, True], "the retry must bypass the cached secret"


def test_best_effort_gives_up_after_a_refreshed_auth_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        imap,
        "_connect",
        lambda host: FakeImap(login_error=LoginError("AUTHENTICATIONFAILED")),
    )

    with caplog.at_level(logging.WARNING):
        assert imap.append_to_sent_best_effort(b"raw mime") is False

    assert "WAS sent" in caplog.text
