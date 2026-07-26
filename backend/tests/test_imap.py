"""IMAP tests against a fake server — no network, no AWS, no database.

``common.imap`` is replaced at two seams: ``_connect`` (the socket) and the credentials fetch, so
these exercise the real protocol handling against scripted LIST/CREATE/SUBSCRIBE/APPEND responses.

What they pin, and why each earns its place:

- **discovery, not guessing** — Sent is found by the ``\\Sent`` SPECIAL-USE flag and the hierarchy
  delimiter is read from the server, because a hardcoded name is localized-away and a hardcoded
  ``/`` silently creates one flat folder named ``Speaker Tracker/Import``;
- **idempotent folder setup** (acceptance #4 and #5) — including the server that answers ``NO`` to
  CREATE because the folder already exists;
- **the best-effort contract** (decision #2) — after SES has accepted a message, no mailbox
  problem may raise, and the WARNING must say the mail was still sent;
- **the credential never reaches a log line**, even when login is rejected.
"""

from __future__ import annotations

import imaplib
import logging

import pytest

from common import imap, secrets

HOST = "imap.mail.us-east-1.awsapps.com"
USERNAME = "donna.king@360balancedliving.com"
PASSWORD = "hunter2-do-not-leak"

#: A WorkMail-shaped LIST response: '/' delimiter, Sent carrying the SPECIAL-USE flag.
WORKMAIL_LIST = [
    rb'(\HasNoChildren) "/" "INBOX"',
    rb'(\HasNoChildren \Sent) "/" "Sent Items"',
    rb'(\HasNoChildren \Drafts) "/" "Drafts"',
]


class FakeImap:
    """Scripted stand-in for ``imaplib.IMAP4_SSL``."""

    def __init__(
        self,
        *,
        listing: list[bytes] | None = None,
        login_error: Exception | None = None,
        create_status: str = "OK",
        subscribe_status: str = "OK",
        append_status: str = "OK",
        creates_actually_work: bool = True,
    ) -> None:
        self.listing = list(WORKMAIL_LIST if listing is None else listing)
        self.login_error = login_error
        self.create_status = create_status
        self.subscribe_status = subscribe_status
        self.append_status = append_status
        self.creates_actually_work = creates_actually_work
        self.logins: list[tuple[str, str]] = []
        self.created: list[str] = []
        self.subscribed: list[str] = []
        self.appends: list[tuple[str, str, bytes]] = []
        self.logged_out = False

    def login(self, username: str, password: str):
        self.logins.append((username, password))
        if self.login_error is not None:
            raise self.login_error
        return ("OK", [b"logged in"])

    def list(self, *args, **kwargs):
        return ("OK", list(self.listing))

    def create(self, name: str):
        unquoted = name.strip('"')
        self.created.append(unquoted)
        if self.creates_actually_work:
            self.listing.append(f'(\\HasNoChildren) "/" "{unquoted}"'.encode())
        return (self.create_status, [b"created"])

    def subscribe(self, name: str):
        self.subscribed.append(name.strip('"'))
        return (self.subscribe_status, [b"subscribed"])

    def append(self, folder: str, flags: str, date_time, message: bytes):
        self.appends.append((folder.strip('"'), flags, message))
        return (self.append_status, [b"appended"])

    def logout(self):
        self.logged_out = True
        return ("BYE", [b"bye"])


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
    install(monkeypatch, FakeImap(login_error=imaplib.IMAP4.error("AUTHENTICATIONFAILED")))

    with pytest.raises(imap.ImapAuthError):
        with imap.connection():
            pass


def test_rejected_login_never_logs_the_password(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    install(monkeypatch, FakeImap(login_error=imaplib.IMAP4.error("AUTHENTICATIONFAILED")))

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


def test_special_use_wins_over_a_conventionally_named_folder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A localized Sent folder carrying the flag must beat an unrelated folder literally named
    # "Sent" — the flag is the authority.
    server = install(
        monkeypatch,
        FakeImap(
            listing=[
                rb'(\HasNoChildren) "/" "Sent"',
                rb'(\HasNoChildren \Sent) "/" "Elementos enviados"',
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
        FakeImap(listing=[rb'(\HasNoChildren) "/" "INBOX"', rb'(\HasNoChildren) "/" "Sent Items"']),
    )

    with caplog.at_level(logging.WARNING):
        assert imap.find_sent_folder(server) == "Sent Items"

    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_no_identifiable_sent_folder_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    server = install(monkeypatch, FakeImap(listing=[rb'(\HasNoChildren) "/" "INBOX"']))
    with pytest.raises(imap.ImapError, match="Sent folder"):
        imap.find_sent_folder(server)


def test_delimiter_is_read_from_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    # A '.'-delimited server must not get '/'-joined folder names.
    server = install(monkeypatch, FakeImap(listing=[rb'(\HasNoChildren) "." "INBOX"']))
    assert imap.folder_delimiter(server) == "."


def test_delimiter_defaults_when_the_server_reports_none(monkeypatch: pytest.MonkeyPatch) -> None:
    server = install(monkeypatch, FakeImap(listing=[rb'(\HasNoChildren) "" "INBOX"']))
    assert imap.folder_delimiter(server) == "/"


def test_failed_list_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    class NoList(FakeImap):
        def list(self, *args, **kwargs):
            return ("NO", [b"denied"])

    server = install(monkeypatch, NoList())
    with pytest.raises(imap.ImapError, match="LIST failed"):
        imap.folder_delimiter(server)


# --- APPEND -------------------------------------------------------------------------------------


def test_append_targets_the_sent_folder_and_marks_it_read(monkeypatch: pytest.MonkeyPatch) -> None:
    server = install(monkeypatch, FakeImap())

    folder = imap.append_to_sent(server, b"raw mime")

    assert folder == "Sent Items"
    appended_folder, flags, message = server.appends[0]
    assert appended_folder == "Sent Items"
    assert flags == r"(\Seen)", "Donna sent it; it is not unread mail"
    assert message == b"raw mime"


def test_append_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    server = install(monkeypatch, FakeImap(append_status="NO"))
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
    server = install(monkeypatch, FakeImap(listing=[rb'(\HasNoChildren) "." "INBOX"']))

    assert imap.ensure_folder(server, imap.IMPORT_FOLDER_PATH) == "Speaker Tracker.Import"


def test_ensure_folder_does_not_recreate_an_existing_folder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = install(
        monkeypatch,
        FakeImap(listing=[*WORKMAIL_LIST, rb'(\HasNoChildren) "/" "Speaker Tracker/Import"']),
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


def test_create_reporting_no_is_tolerated_when_the_folder_exists(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Some servers answer NO for "already exists"; existence is re-checked rather than trusted.
    server = install(monkeypatch, FakeImap(create_status="NO"))

    with caplog.at_level(logging.WARNING):
        assert imap.ensure_folder(server, imap.IMPORT_FOLDER_PATH) == "Speaker Tracker/Import"

    assert server.subscribed == ["Speaker Tracker/Import"]
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_create_that_genuinely_fails_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    server = install(monkeypatch, FakeImap(create_status="NO", creates_actually_work=False))
    with pytest.raises(imap.ImapError, match="CREATE"):
        imap.ensure_folder(server, imap.IMPORT_FOLDER_PATH)


def test_failed_subscribe_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    server = install(monkeypatch, FakeImap(subscribe_status="NO"))
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
    install(monkeypatch, FakeImap(append_status="NO"))

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
    servers = [
        FakeImap(login_error=imaplib.IMAP4.error("AUTHENTICATIONFAILED")),
        FakeImap(),
    ]
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
        lambda host: FakeImap(login_error=imaplib.IMAP4.error("AUTHENTICATIONFAILED")),
    )

    with caplog.at_level(logging.WARNING):
        assert imap.append_to_sent_best_effort(b"raw mime") is False

    assert "WAS sent" in caplog.text
