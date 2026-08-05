"""Opportunity-response counter tests against a seeded MySQL (slice 12).

Skip without ``TEST_DATABASE_URL`` (see conftest). These pin the three things that make this a
counter rather than a journal: the write is an upsert on (opportunity, type) so repeated ``+``
clicks cannot fan out into duplicate rows, the value is a set rather than a delta, and zero is a
legitimate stored state rather than a deletion.
"""

from __future__ import annotations

import pymysql
import pytest

from common import errors
from repositories import dashboard
from repositories import opportunities as opps_repo
from repositories import opportunity_responses as responses_repo

CHAT = "legacy_spark_chat"
DISCOVERY = "discovery"
BOOKLET = "booklet"


@pytest.fixture
def responses_db(seeded_db):
    """A migrated DB with a venue, two gigs, and a second tenant's gig for scoping checks.

    Returns ``(conn, user_id, ids)`` with ``expo`` / ``retreat`` (opportunities), ``other_user`` and
    ``foreign_opp``.
    """
    conn, user_id, _, _ = seeded_db
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO organizations (user_id, organization_type_id, name) "
            "SELECT %s, id, 'Expo' FROM organization_types WHERE short_name = 'expo'",
            (user_id,),
        )
        org = cur.lastrowid

        def make_opp(owner: int, org_id: int, title: str) -> int:
            cur.execute(
                "INSERT INTO opportunities "
                "(user_id, organization_id, opportunity_format_id, current_status_id, "
                " comp_type_id, payment_status_id, title) "
                "SELECT %s, %s, fmt.id, st.id, ct.id, pay.id, %s "
                "FROM opportunity_formats fmt, opportunity_statuses st, comp_types ct, "
                "     payment_statuses pay "
                "WHERE fmt.short_name = 'workshop' AND st.short_name = 'researching' "
                "  AND ct.short_name = 'paid' AND pay.short_name = 'unbilled'",
                (owner, org_id, title),
            )
            return cur.lastrowid

        expo = make_opp(user_id, org, "Expo Talk")
        retreat = make_opp(user_id, org, "Retreat Talk")
        cur.execute("INSERT INTO users (cognito_sub, email) VALUES ('user2', 'user2@example.com')")
        other_user = cur.lastrowid
        cur.execute(
            "INSERT INTO organizations (user_id, organization_type_id, name) "
            "SELECT %s, id, 'Theirs' FROM organization_types WHERE short_name = 'expo'",
            (other_user,),
        )
        foreign_opp = make_opp(other_user, cur.lastrowid, "Their Talk")
    return (
        conn,
        user_id,
        {
            "expo": expo,
            "retreat": retreat,
            "other_user": other_user,
            "foreign_opp": foreign_opp,
        },
    )


def _counts(conn, user_id, opp_id) -> dict[str, int]:
    return {
        r["response_type"]: r["count"]
        for r in responses_repo.get_response_counts(conn, user_id, opp_id)
    }


def _row_count(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM opportunity_responses")
        return cur.fetchone()["n"]


def test_setting_a_count_creates_then_updates_one_row(responses_db) -> None:
    """The upsert is the whole point: leaning on `+` must not fan out into duplicate counters."""
    conn, user_id, ids = responses_db
    responses_repo.set_response_count(conn, user_id, ids["expo"], CHAT, 1)
    assert _row_count(conn) == 1

    responses_repo.set_response_count(conn, user_id, ids["expo"], CHAT, 5)
    assert _row_count(conn) == 1  # updated, not appended
    assert _counts(conn, user_id, ids["expo"]) == {CHAT: 5}


def test_counts_are_per_type_and_per_opportunity(responses_db) -> None:
    conn, user_id, ids = responses_db
    responses_repo.set_response_count(conn, user_id, ids["expo"], CHAT, 3)
    responses_repo.set_response_count(conn, user_id, ids["expo"], BOOKLET, 2)
    responses_repo.set_response_count(conn, user_id, ids["retreat"], CHAT, 7)

    assert _counts(conn, user_id, ids["expo"]) == {CHAT: 3, BOOKLET: 2}
    assert _counts(conn, user_id, ids["retreat"]) == {CHAT: 7}


def test_zero_is_stored_not_deleted(responses_db) -> None:
    """`-` down to zero keeps the row: zero IS the empty state, which is why there is no delete."""
    conn, user_id, ids = responses_db
    responses_repo.set_response_count(conn, user_id, ids["expo"], DISCOVERY, 2)
    responses_repo.set_response_count(conn, user_id, ids["expo"], DISCOVERY, 0)

    assert _row_count(conn) == 1
    assert _counts(conn, user_id, ids["expo"]) == {DISCOVERY: 0}


def test_reads_come_back_in_catalog_order(responses_db) -> None:
    """Written back-to-front, read in catalog order — the grid must not reshuffle itself."""
    conn, user_id, ids = responses_db
    for short_name in (BOOKLET, DISCOVERY, CHAT):
        responses_repo.set_response_count(conn, user_id, ids["expo"], short_name, 1)

    rows = responses_repo.get_response_counts(conn, user_id, ids["expo"])
    assert [r["response_type"] for r in rows] == [CHAT, DISCOVERY, BOOKLET]


@pytest.mark.parametrize("opp_key", ["foreign_opp", "missing"])
def test_a_foreign_or_missing_opportunity_is_not_found(responses_db, opp_key) -> None:
    conn, user_id, ids = responses_db
    opp_id = 999999 if opp_key == "missing" else ids[opp_key]
    with pytest.raises(errors.NotFound):
        responses_repo.set_response_count(conn, user_id, opp_id, CHAT, 1)
    assert _row_count(conn) == 0  # nothing orphaned


def test_an_unknown_response_type_is_rejected(responses_db) -> None:
    conn, user_id, ids = responses_db
    with pytest.raises(errors.InvalidInput):
        responses_repo.set_response_count(conn, user_id, ids["expo"], "smoke_signal", 1)


def test_reads_are_owner_scoped(responses_db) -> None:
    conn, user_id, ids = responses_db
    responses_repo.set_response_count(conn, user_id, ids["expo"], CHAT, 4)
    assert responses_repo.get_response_counts(conn, ids["other_user"], ids["expo"]) == []


def test_the_has_responses_filter_returns_only_gigs_that_produced_one(responses_db) -> None:
    conn, user_id, ids = responses_db
    responses_repo.set_response_count(conn, user_id, ids["expo"], CHAT, 2)

    rows = opps_repo.list_opportunities(conn, user_id, has_responses=True)
    assert [r["title"] for r in rows] == ["Expo Talk"]  # the retreat produced nothing


def test_the_filter_ignores_a_counter_that_was_zeroed(responses_db) -> None:
    """A row raised and then taken back is not a gig that produced a response."""
    conn, user_id, ids = responses_db
    responses_repo.set_response_count(conn, user_id, ids["expo"], CHAT, 1)
    responses_repo.set_response_count(conn, user_id, ids["expo"], CHAT, 0)

    assert opps_repo.list_opportunities(conn, user_id, has_responses=True) == []


def test_the_filter_and_the_funnel_number_cannot_drift(responses_db) -> None:
    """The Dashboard row and the list it opens must agree — slice 8's rule, mechanized.

    Both sides run ``response_count > 0``; this asserts they stay the same query. Seeded so the
    answer is not trivially zero or everything: one gig counting, one zeroed, one untouched.
    """
    conn, user_id, ids = responses_db
    responses_repo.set_response_count(conn, user_id, ids["expo"], CHAT, 3)
    responses_repo.set_response_count(conn, user_id, ids["expo"], BOOKLET, 1)
    responses_repo.set_response_count(conn, user_id, ids["retreat"], DISCOVERY, 0)

    number_on_the_row = dashboard.gigs_with_responses(conn, user_id)
    rows_in_the_list = opps_repo.list_opportunities(conn, user_id, has_responses=True)

    assert number_on_the_row == 1
    assert len(rows_in_the_list) == number_on_the_row


def test_the_database_refuses_a_negative_count(responses_db) -> None:
    """The CHECK is the real guarantee; the model's ``ge=0`` is only the polite layer in front.

    Written as a raw INSERT on purpose — going through the repository would be testing Pydantic.
    """
    conn, user_id, ids = responses_db
    # pymysql has no mapping for MySQL 3819, so a CHECK violation arrives as OperationalError
    # rather than IntegrityError. Catching the base class and asserting on the constraint *name*
    # says what this test means — "the database rejected it, by this rule" — and does not depend on
    # which subclass pymysql happens to pick.
    with pytest.raises(pymysql.MySQLError) as excinfo, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO opportunity_responses "
            "(user_id, opportunity_id, opportunity_response_type_id, response_count) "
            "SELECT %s, %s, t.id, -1 FROM opportunity_response_types t "
            "WHERE t.short_name = %s",
            (user_id, ids["expo"], CHAT),
        )
    assert "ck_opportunity_responses_count" in str(excinfo.value)
