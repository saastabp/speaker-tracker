"""Organizations repository tests against a seeded MySQL — CRUD, uniqueness, soft-delete, scope.

Skip without ``TEST_DATABASE_URL`` (see conftest). Covers slice-2 acceptance #5 (list carries
``why_it_fits``) and #6 (soft delete hides everywhere), plus the active-name uniqueness guard.
"""

from __future__ import annotations

import pytest

from common import errors
from models.contacts import AffiliationInput
from models.organizations import OrganizationInput
from repositories import contacts
from repositories import organizations as orgs


def _org(org_type: str, name: str = "PWN", **kw) -> OrganizationInput:
    return OrganizationInput(organization_type=org_type, name=name, **kw)


def test_create_and_get(seeded_db) -> None:
    conn, user_id, org_type, _ = seeded_db
    org_id = orgs.create_organization(conn, user_id, _org(org_type, why_it_fits="great fit"))
    row = orgs.get_organization(conn, user_id, org_id)
    assert row["name"] == "PWN"
    assert row["why_it_fits"] == "great fit"
    assert row["contact_count"] == 0


def test_list_is_name_ordered_and_carries_why_it_fits(seeded_db) -> None:
    conn, user_id, org_type, _ = seeded_db
    orgs.create_organization(conn, user_id, _org(org_type, "Bravo", why_it_fits="fits B"))
    orgs.create_organization(conn, user_id, _org(org_type, "Alpha", why_it_fits="fits A"))
    rows = orgs.list_organizations(conn, user_id)
    assert [r["name"] for r in rows] == ["Alpha", "Bravo"]
    assert rows[0]["why_it_fits"] == "fits A"  # acceptance #5


def test_duplicate_live_name_conflicts(seeded_db) -> None:
    conn, user_id, org_type, _ = seeded_db
    orgs.create_organization(conn, user_id, _org(org_type, "PWN"))
    with pytest.raises(errors.Conflict):
        orgs.create_organization(conn, user_id, _org(org_type, "PWN"))


def test_name_reusable_after_soft_delete(seeded_db) -> None:
    conn, user_id, org_type, _ = seeded_db
    org_id = orgs.create_organization(conn, user_id, _org(org_type, "PWN"))
    assert orgs.soft_delete_organization(conn, user_id, org_id) is True
    orgs.create_organization(conn, user_id, _org(org_type, "PWN"))  # no Conflict


def test_unknown_type_is_invalid_input(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    with pytest.raises(errors.InvalidInput):
        orgs.create_organization(conn, user_id, _org("no_such_type", "X"))


def test_update_replaces_fields(seeded_db) -> None:
    conn, user_id, org_type, _ = seeded_db
    org_id = orgs.create_organization(conn, user_id, _org(org_type, "PWN", location="Kauai"))
    assert (
        orgs.update_organization(conn, user_id, org_id, _org(org_type, "PWN", location="Oahu"))
        is True
    )
    assert orgs.get_organization(conn, user_id, org_id)["location"] == "Oahu"


def test_update_missing_returns_false(seeded_db) -> None:
    conn, user_id, org_type, _ = seeded_db
    assert orgs.update_organization(conn, user_id, 999, _org(org_type, "X")) is False


def test_update_to_duplicate_name_conflicts(seeded_db) -> None:
    conn, user_id, org_type, _ = seeded_db
    orgs.create_organization(conn, user_id, _org(org_type, "Alpha"))
    bravo = orgs.create_organization(conn, user_id, _org(org_type, "Bravo"))
    with pytest.raises(errors.Conflict):
        orgs.update_organization(conn, user_id, bravo, _org(org_type, "Alpha"))


def test_soft_delete_hides_and_is_idempotent(seeded_db) -> None:
    conn, user_id, org_type, _ = seeded_db
    org_id = orgs.create_organization(conn, user_id, _org(org_type, "PWN"))
    assert orgs.soft_delete_organization(conn, user_id, org_id) is True
    assert orgs.get_organization(conn, user_id, org_id) is None
    assert orgs.list_organizations(conn, user_id) == []
    assert orgs.soft_delete_organization(conn, user_id, org_id) is False  # already gone


def test_get_is_scoped_to_owner(seeded_db, db_connection) -> None:
    conn, user_id, org_type, _ = seeded_db
    org_id = orgs.create_organization(conn, user_id, _org(org_type, "PWN"))
    with db_connection.cursor() as cur:
        cur.execute("INSERT INTO users (cognito_sub, email) VALUES ('u2', 'u2@x')")
        other_user = cur.lastrowid
    assert orgs.get_organization(conn, other_user, org_id) is None


# --- research-ready stamping (slice 10 follow-up) -------------------------------------------------


def _kindling(org_type: str, name: str) -> OrganizationInput:
    """A venue with all three Kindling fields filled — ready as soon as it has a contact."""
    return _org(org_type, name, what_it_is="what", why_it_fits="why", how_to_approach="how")


def _contact_at(conn, user_id: int, org_id: int, name: str = "C") -> None:
    """Attach a contact through the real affiliation path, which is what stamps."""
    with conn.cursor() as cur:
        cur.execute("INSERT INTO contacts (user_id, name) VALUES (%s, %s)", (user_id, name))
        contact_id = cur.lastrowid
    contacts.add_affiliation(conn, user_id, contact_id, AffiliationInput(organization_id=org_id))


def _stamp(conn, org_id: int):
    with conn.cursor() as cur:
        cur.execute("SELECT research_ready_at FROM organizations WHERE id = %s", (org_id,))
        return cur.fetchone()["research_ready_at"]


def test_attaching_the_first_contact_stamps_a_researched_venue(seeded_db) -> None:
    conn, user_id, org_type, _ = seeded_db
    org_id = orgs.create_organization(conn, user_id, _kindling(org_type, "Ready"))
    assert _stamp(conn, org_id) is None  # Kindling alone is not researched
    _contact_at(conn, user_id, org_id)
    assert _stamp(conn, org_id) is not None


def test_filling_the_last_kindling_field_stamps_a_venue_that_already_has_a_contact(
    seeded_db,
) -> None:
    conn, user_id, org_type, _ = seeded_db
    org_id = orgs.create_organization(conn, user_id, _org(org_type, "Bare"))
    _contact_at(conn, user_id, org_id)
    assert _stamp(conn, org_id) is None  # a contact alone is not researched either
    orgs.update_organization(conn, user_id, org_id, _kindling(org_type, "Bare"))
    assert _stamp(conn, org_id) is not None


def test_a_venue_is_never_stamped_twice(seeded_db) -> None:
    """Losing the last contact and regaining one is not researching the venue again."""
    conn, user_id, org_type, _ = seeded_db
    org_id = orgs.create_organization(conn, user_id, _kindling(org_type, "Ready"))
    _contact_at(conn, user_id, org_id, "First")
    first = _stamp(conn, org_id)
    # Any later write that would satisfy the predicate again must leave the date alone, or the
    # venue would migrate to a later month and the earlier month's number would change after
    # the fact.
    orgs.update_organization(conn, user_id, org_id, _kindling(org_type, "Ready"))
    _contact_at(conn, user_id, org_id, "Second")
    assert _stamp(conn, org_id) == first


def test_an_unresearched_venue_is_not_stamped(seeded_db) -> None:
    conn, user_id, org_type, _ = seeded_db
    org_id = orgs.create_organization(conn, user_id, _org(org_type, "Bare"))
    orgs.update_organization(conn, user_id, org_id, _org(org_type, "Bare", why_it_fits="only one"))
    assert _stamp(conn, org_id) is None


def test_stamping_is_scoped_to_the_owner(seeded_db, db_connection) -> None:
    conn, user_id, org_type, _ = seeded_db
    org_id = orgs.create_organization(conn, user_id, _kindling(org_type, "Ready"))
    _contact_at(conn, user_id, org_id)
    before = _stamp(conn, org_id)
    assert orgs.stamp_research_ready(conn, user_id + 999, org_id) is False
    assert _stamp(conn, org_id) == before
