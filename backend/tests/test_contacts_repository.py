"""Contacts repository tests against a seeded MySQL — CRUD, dedupe search, affiliations.

Skip without ``TEST_DATABASE_URL`` (see conftest). Covers slice-2 acceptance #1 (a contact under
two orgs, appearing under both), #2 (dedupe search), #3 (duplicate affiliation rejected), and #6
(soft delete hides the contact from an org's count), plus cross-user ownership and the per-venue
power-partner flag (scoped to a contact↔venue edge, rolled up to the person in the contacts list).
"""

from __future__ import annotations

import pytest

from common import errors
from models.contacts import AffiliationInput, AffiliationUpdate, ContactInput
from models.organizations import OrganizationInput
from repositories import contacts as contacts_repo
from repositories import organizations as orgs_repo


def _org(org_type: str, name: str) -> OrganizationInput:
    return OrganizationInput(organization_type=org_type, name=name)


def test_create_and_get_contact(seeded_db) -> None:
    conn, user_id, _, warmth = seeded_db
    contact_id = contacts_repo.create_contact(
        conn, user_id, ContactInput(name="Jane", email="jane@x.com", warmth_tier=warmth)
    )
    row = contacts_repo.get_contact(conn, user_id, contact_id)
    assert row["name"] == "Jane"
    assert row["email"] == "jane@x.com"


def test_unknown_warmth_tier_is_invalid_input(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    with pytest.raises(errors.InvalidInput):
        contacts_repo.create_contact(
            conn, user_id, ContactInput(name="X", warmth_tier="no_such_tier")
        )


def test_dedupe_search_matches_name_and_email(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    contacts_repo.create_contact(
        conn, user_id, ContactInput(name="Jane Doe", email="jane@venue.com")
    )
    contacts_repo.create_contact(conn, user_id, ContactInput(name="Bob Smith", email="bob@x.com"))
    assert [r["name"] for r in contacts_repo.list_contacts(conn, user_id, "jane")] == ["Jane Doe"]
    assert [r["name"] for r in contacts_repo.list_contacts(conn, user_id, "venue.com")] == [
        "Jane Doe"
    ]
    assert len(contacts_repo.list_contacts(conn, user_id)) == 2  # no query → all


def test_contact_affiliated_with_two_orgs_appears_under_both(seeded_db) -> None:
    conn, user_id, org_type, _ = seeded_db
    a = orgs_repo.create_organization(conn, user_id, _org(org_type, "Alpha"))
    b = orgs_repo.create_organization(conn, user_id, _org(org_type, "Bravo"))
    jane = contacts_repo.create_contact(conn, user_id, ContactInput(name="Jane"))
    contacts_repo.add_affiliation(
        conn, user_id, jane, AffiliationInput(organization_id=a, title="Chair", is_primary=True)
    )
    contacts_repo.add_affiliation(
        conn, user_id, jane, AffiliationInput(organization_id=b, title="Member")
    )

    affiliations = contacts_repo.get_affiliations(conn, user_id, jane)
    assert {x["organization_name"] for x in affiliations} == {"Alpha", "Bravo"}
    assert [x["name"] for x in orgs_repo.get_affiliated_contacts(conn, user_id, a)] == ["Jane"]
    assert [x["name"] for x in orgs_repo.get_affiliated_contacts(conn, user_id, b)] == ["Jane"]
    assert contacts_repo.list_contacts(conn, user_id)[0]["organization_count"] == 2


def test_duplicate_affiliation_conflicts(seeded_db) -> None:
    conn, user_id, org_type, _ = seeded_db
    a = orgs_repo.create_organization(conn, user_id, _org(org_type, "Alpha"))
    jane = contacts_repo.create_contact(conn, user_id, ContactInput(name="Jane"))
    contacts_repo.add_affiliation(conn, user_id, jane, AffiliationInput(organization_id=a))
    with pytest.raises(errors.Conflict):
        contacts_repo.add_affiliation(conn, user_id, jane, AffiliationInput(organization_id=a))


def test_affiliation_requires_owned_org(seeded_db, db_connection) -> None:
    conn, user_id, org_type, _ = seeded_db
    jane = contacts_repo.create_contact(conn, user_id, ContactInput(name="Jane"))
    with db_connection.cursor() as cur:
        cur.execute("INSERT INTO users (cognito_sub, email) VALUES ('u2', 'u2@x')")
        other_user = cur.lastrowid
    other_org = orgs_repo.create_organization(conn, other_user, _org(org_type, "Other"))
    with pytest.raises(errors.NotFound):
        contacts_repo.add_affiliation(
            conn, user_id, jane, AffiliationInput(organization_id=other_org)
        )


def test_add_affiliation_missing_contact_is_not_found(seeded_db) -> None:
    conn, user_id, org_type, _ = seeded_db
    a = orgs_repo.create_organization(conn, user_id, _org(org_type, "Alpha"))
    with pytest.raises(errors.NotFound):
        contacts_repo.add_affiliation(conn, user_id, 999, AffiliationInput(organization_id=a))


def test_update_and_remove_affiliation(seeded_db) -> None:
    conn, user_id, org_type, _ = seeded_db
    a = orgs_repo.create_organization(conn, user_id, _org(org_type, "Alpha"))
    jane = contacts_repo.create_contact(conn, user_id, ContactInput(name="Jane"))
    contacts_repo.add_affiliation(
        conn, user_id, jane, AffiliationInput(organization_id=a, title="Chair", is_primary=True)
    )
    assert (
        contacts_repo.update_affiliation(
            conn, user_id, jane, a, AffiliationUpdate(title="Past Chair", is_primary=False)
        )
        is True
    )
    affiliation = contacts_repo.get_affiliations(conn, user_id, jane)[0]
    assert affiliation["title"] == "Past Chair"
    assert not affiliation["is_primary"]

    assert contacts_repo.remove_affiliation(conn, user_id, jane, a) is True
    assert contacts_repo.get_affiliations(conn, user_id, jane) == []
    assert contacts_repo.remove_affiliation(conn, user_id, jane, a) is False  # already gone


def test_soft_deleted_contact_drops_from_org_count(seeded_db) -> None:
    conn, user_id, org_type, _ = seeded_db
    a = orgs_repo.create_organization(conn, user_id, _org(org_type, "Alpha"))
    jane = contacts_repo.create_contact(conn, user_id, ContactInput(name="Jane"))
    contacts_repo.add_affiliation(conn, user_id, jane, AffiliationInput(organization_id=a))
    assert orgs_repo.get_organization(conn, user_id, a)["contact_count"] == 1
    contacts_repo.soft_delete_contact(conn, user_id, jane)
    assert orgs_repo.get_organization(conn, user_id, a)["contact_count"] == 0
    assert orgs_repo.get_affiliated_contacts(conn, user_id, a) == []


def test_power_partner_is_scoped_to_the_venue_and_rolls_up(seeded_db) -> None:
    conn, user_id, org_type, _ = seeded_db
    a = orgs_repo.create_organization(conn, user_id, _org(org_type, "Alpha"))
    b = orgs_repo.create_organization(conn, user_id, _org(org_type, "Bravo"))
    jane = contacts_repo.create_contact(conn, user_id, ContactInput(name="Jane"))
    contacts_repo.add_affiliation(
        conn, user_id, jane, AffiliationInput(organization_id=a, is_power_partner=True)
    )
    contacts_repo.add_affiliation(conn, user_id, jane, AffiliationInput(organization_id=b))

    # Same person, per-edge flag: power partner at Alpha, not at Bravo.
    assert orgs_repo.get_affiliated_contacts(conn, user_id, a)[0]["is_power_partner"]
    assert not orgs_repo.get_affiliated_contacts(conn, user_id, b)[0]["is_power_partner"]
    by_org = {x["organization_name"]: x for x in contacts_repo.get_affiliations(conn, user_id, jane)}
    assert by_org["Alpha"]["is_power_partner"]
    assert not by_org["Bravo"]["is_power_partner"]
    # The contacts list rolls it up: power partner at *any* live venue.
    assert contacts_repo.list_contacts(conn, user_id)[0]["is_power_partner"]


def test_power_partner_rollup_false_without_any(seeded_db) -> None:
    conn, user_id, org_type, _ = seeded_db
    a = orgs_repo.create_organization(conn, user_id, _org(org_type, "Alpha"))
    contacts_repo.create_contact(conn, user_id, ContactInput(name="Bob"))  # unaffiliated
    carol = contacts_repo.create_contact(conn, user_id, ContactInput(name="Carol"))
    contacts_repo.add_affiliation(conn, user_id, carol, AffiliationInput(organization_id=a))
    rollup = {r["name"]: r["is_power_partner"] for r in contacts_repo.list_contacts(conn, user_id)}
    assert not rollup["Bob"]
    assert not rollup["Carol"]


def test_power_partner_rollup_ignores_soft_deleted_venue(seeded_db) -> None:
    conn, user_id, org_type, _ = seeded_db
    a = orgs_repo.create_organization(conn, user_id, _org(org_type, "Alpha"))
    jane = contacts_repo.create_contact(conn, user_id, ContactInput(name="Jane"))
    contacts_repo.add_affiliation(
        conn, user_id, jane, AffiliationInput(organization_id=a, is_power_partner=True)
    )
    assert contacts_repo.list_contacts(conn, user_id)[0]["is_power_partner"]
    orgs_repo.soft_delete_organization(conn, user_id, a)
    # Its only power-partner affiliation is at a hidden venue → rollup goes false.
    assert not contacts_repo.list_contacts(conn, user_id)[0]["is_power_partner"]


def test_update_affiliation_toggles_power_partner(seeded_db) -> None:
    conn, user_id, org_type, _ = seeded_db
    a = orgs_repo.create_organization(conn, user_id, _org(org_type, "Alpha"))
    jane = contacts_repo.create_contact(conn, user_id, ContactInput(name="Jane"))
    contacts_repo.add_affiliation(conn, user_id, jane, AffiliationInput(organization_id=a))
    assert not contacts_repo.get_affiliations(conn, user_id, jane)[0]["is_power_partner"]
    contacts_repo.update_affiliation(conn, user_id, jane, a, AffiliationUpdate(is_power_partner=True))
    assert contacts_repo.get_affiliations(conn, user_id, jane)[0]["is_power_partner"]


def test_setting_primary_demotes_the_previous_primary(seeded_db) -> None:
    conn, user_id, org_type, _ = seeded_db
    venue = orgs_repo.create_organization(conn, user_id, _org(org_type, "Alpha"))
    ann = contacts_repo.create_contact(conn, user_id, ContactInput(name="Ann"))
    bob = contacts_repo.create_contact(conn, user_id, ContactInput(name="Bob"))

    def primaries() -> list[str]:
        return [
            c["name"]
            for c in orgs_repo.get_affiliated_contacts(conn, user_id, venue)
            if c["is_primary"]
        ]

    contacts_repo.add_affiliation(
        conn, user_id, ann, AffiliationInput(organization_id=venue, is_primary=True)
    )
    # A second primary via add_affiliation moves it off Ann.
    contacts_repo.add_affiliation(
        conn, user_id, bob, AffiliationInput(organization_id=venue, is_primary=True)
    )
    assert primaries() == ["Bob"]
    # And moving it back via update_affiliation demotes Bob.
    contacts_repo.update_affiliation(conn, user_id, ann, venue, AffiliationUpdate(is_primary=True))
    assert primaries() == ["Ann"]


def test_primary_demotion_is_scoped_to_the_venue(seeded_db) -> None:
    conn, user_id, org_type, _ = seeded_db
    v1 = orgs_repo.create_organization(conn, user_id, _org(org_type, "Alpha"))
    v2 = orgs_repo.create_organization(conn, user_id, _org(org_type, "Bravo"))
    ann = contacts_repo.create_contact(conn, user_id, ContactInput(name="Ann"))
    bob = contacts_repo.create_contact(conn, user_id, ContactInput(name="Bob"))
    # Ann is primary at both venues.
    contacts_repo.add_affiliation(
        conn, user_id, ann, AffiliationInput(organization_id=v1, is_primary=True)
    )
    contacts_repo.add_affiliation(
        conn, user_id, ann, AffiliationInput(organization_id=v2, is_primary=True)
    )
    # Bob becomes primary at v1 — Ann is demoted at v1 only, untouched at v2.
    contacts_repo.add_affiliation(
        conn, user_id, bob, AffiliationInput(organization_id=v1, is_primary=True)
    )

    def primaries(v: int) -> list[str]:
        return [
            c["name"]
            for c in orgs_repo.get_affiliated_contacts(conn, user_id, v)
            if c["is_primary"]
        ]

    assert primaries(v1) == ["Bob"]
    assert primaries(v2) == ["Ann"]
