"""Organizations repository tests against a seeded MySQL — CRUD, uniqueness, soft-delete, scope.

Skip without ``TEST_DATABASE_URL`` (see conftest). Covers slice-2 acceptance #5 (list carries
``why_it_fits``) and #6 (soft delete hides everywhere), plus the active-name uniqueness guard.
"""

from __future__ import annotations

import pytest

from common import errors
from models.organizations import OrganizationInput
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
