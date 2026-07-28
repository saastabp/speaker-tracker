"""IMAP cursor repository tests — the poll watermark and the ``UIDVALIDITY`` reset.

Skip without ``TEST_DATABASE_URL`` (see conftest).

These run against real MySQL rather than a fake because the behaviour under test *is* the SQL: the
watermark's no-rewind guarantee and its reset are both expressed inside one ``ON DUPLICATE KEY
UPDATE``, and the reset depends on MySQL evaluating those assignments left to right. Nothing about
that is observable from Python.
"""

from __future__ import annotations

from repositories import imap_cursors


def _second_user(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO users (cognito_sub, email) VALUES ('other', 'other@example.com')")
        return cur.lastrowid


def test_a_folder_never_polled_has_no_cursor(seeded_db) -> None:
    """``None`` is the first-poll signal, and must stay distinguishable from a cursor at UID 0.

    Conflating them would make the first poll read the whole folder — years of personal mail.
    """
    conn, user_id, _, _ = seeded_db
    assert imap_cursors.get_cursor(conn, user_id, "INBOX") is None


def test_a_cursor_stored_at_zero_is_not_the_same_as_no_cursor(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    imap_cursors.save_cursor(conn, user_id, "INBOX", uid_validity=42, last_seen_uid=0)
    row = imap_cursors.get_cursor(conn, user_id, "INBOX")
    assert row is not None
    assert row["last_seen_uid"] == 0


def test_the_first_save_creates_the_row(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    imap_cursors.save_cursor(conn, user_id, "INBOX", uid_validity=42, last_seen_uid=900)
    row = imap_cursors.get_cursor(conn, user_id, "INBOX")
    assert (row["uid_validity"], row["last_seen_uid"]) == (42, 900)


def test_the_watermark_advances(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    imap_cursors.save_cursor(conn, user_id, "INBOX", uid_validity=42, last_seen_uid=900)
    imap_cursors.save_cursor(conn, user_id, "INBOX", uid_validity=42, last_seen_uid=905)
    assert imap_cursors.get_cursor(conn, user_id, "INBOX")["last_seen_uid"] == 905


def test_the_watermark_cannot_rewind_within_one_uid_generation(seeded_db) -> None:
    """A stale or retried write must not re-open ground already covered.

    Re-reading is harmless in itself — ingest is idempotent — but a rewinding watermark means the
    poller re-fetches the same mail every minute for as long as the stale writer keeps running.
    """
    conn, user_id, _, _ = seeded_db
    imap_cursors.save_cursor(conn, user_id, "INBOX", uid_validity=42, last_seen_uid=905)
    imap_cursors.save_cursor(conn, user_id, "INBOX", uid_validity=42, last_seen_uid=901)
    assert imap_cursors.get_cursor(conn, user_id, "INBOX")["last_seen_uid"] == 905


def test_a_changed_uid_validity_resets_the_watermark_downwards(seeded_db) -> None:
    """The one case where the watermark is allowed to drop — and the trap in the statement.

    The assignments are evaluated left to right, so ``last_seen_uid`` is computed *before*
    ``uid_validity`` is overwritten. Reorder them and the comparison reads the new generation
    against itself, always matches, and ``GREATEST`` keeps the old watermark in a UID generation
    where it means nothing — silently skipping every message below it. Mutation-checked on MySQL
    8.4 by reordering the two lines and watching this expectation break.
    """
    conn, user_id, _, _ = seeded_db
    imap_cursors.save_cursor(conn, user_id, "INBOX", uid_validity=42, last_seen_uid=905)
    imap_cursors.save_cursor(conn, user_id, "INBOX", uid_validity=43, last_seen_uid=0)
    row = imap_cursors.get_cursor(conn, user_id, "INBOX")
    assert (row["uid_validity"], row["last_seen_uid"]) == (43, 0)


def test_after_a_reset_the_no_rewind_guard_applies_to_the_new_generation(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    imap_cursors.save_cursor(conn, user_id, "INBOX", uid_validity=42, last_seen_uid=905)
    imap_cursors.save_cursor(conn, user_id, "INBOX", uid_validity=43, last_seen_uid=0)
    imap_cursors.save_cursor(conn, user_id, "INBOX", uid_validity=43, last_seen_uid=10)
    imap_cursors.save_cursor(conn, user_id, "INBOX", uid_validity=43, last_seen_uid=5)
    assert imap_cursors.get_cursor(conn, user_id, "INBOX")["last_seen_uid"] == 10


def test_every_save_stamps_last_polled_at_even_when_nothing_moved(seeded_db) -> None:
    """``last_polled_at`` is the liveness signal: a folder that stops being polled shows as a stale
    timestamp rather than only as an absence of mail."""
    conn, user_id, _, _ = seeded_db
    imap_cursors.save_cursor(conn, user_id, "INBOX", uid_validity=42, last_seen_uid=900)
    first = imap_cursors.get_cursor(conn, user_id, "INBOX")["last_polled_at"]
    assert first is not None

    with conn.cursor() as cur:
        # Backdate it so a same-second re-save is still observable.
        cur.execute(
            "UPDATE imap_folder_cursors SET last_polled_at = '2020-01-01 00:00:00' "
            "WHERE user_id = %s AND folder_name = 'INBOX'",
            (user_id,),
        )
    imap_cursors.save_cursor(conn, user_id, "INBOX", uid_validity=42, last_seen_uid=900)
    assert imap_cursors.get_cursor(conn, user_id, "INBOX")["last_polled_at"].year > 2020


def test_folders_keep_independent_cursors(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    imap_cursors.save_cursor(conn, user_id, "INBOX", uid_validity=42, last_seen_uid=900)
    imap_cursors.save_cursor(conn, user_id, "Sent Items", uid_validity=7, last_seen_uid=12)
    assert imap_cursors.get_cursor(conn, user_id, "INBOX")["last_seen_uid"] == 900
    assert imap_cursors.get_cursor(conn, user_id, "Sent Items")["last_seen_uid"] == 12


def test_a_renamed_folder_gets_its_own_cursor_rather_than_inheriting_one(seeded_db) -> None:
    """Folder names are stored verbatim: to IMAP a renamed folder is a different folder, and its
    predecessor's watermark means nothing there."""
    conn, user_id, _, _ = seeded_db
    imap_cursors.save_cursor(
        conn, user_id, "Speaker Tracker/Import", uid_validity=1, last_seen_uid=5
    )
    assert imap_cursors.get_cursor(conn, user_id, "Speaker Tracker/Imported") is None


def test_cursors_are_owner_scoped(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    other_user_id = _second_user(conn)
    imap_cursors.save_cursor(conn, user_id, "INBOX", uid_validity=42, last_seen_uid=900)
    assert imap_cursors.get_cursor(conn, other_user_id, "INBOX") is None


def test_two_users_can_hold_the_same_folder_name_independently(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    other_user_id = _second_user(conn)
    imap_cursors.save_cursor(conn, user_id, "INBOX", uid_validity=42, last_seen_uid=900)
    imap_cursors.save_cursor(conn, other_user_id, "INBOX", uid_validity=99, last_seen_uid=1)
    assert imap_cursors.get_cursor(conn, user_id, "INBOX")["last_seen_uid"] == 900
    assert imap_cursors.get_cursor(conn, other_user_id, "INBOX")["last_seen_uid"] == 1
