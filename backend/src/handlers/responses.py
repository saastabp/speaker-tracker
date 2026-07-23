"""Detail-response composition shared by the route modules.

Given an id, read the aggregate's row plus its related rows and build the full Pydantic detail
response. Kept out of the route modules so no route imports a sibling route; each route module
stays pure routing + orchestration, and "given an id, build its detail" has one home.
"""

from __future__ import annotations

from pymysql.connections import Connection

from common import errors
from core.research import is_research_ready
from models.contacts import Contact, OrganizationAffiliation
from models.opportunities import (
    Opportunity,
    OpportunityContact,
    OpportunityNote,
    StatusEvent,
)
from models.organizations import AffiliatedContact, Organization
from models.talks import Talk
from repositories import contacts as contacts_repo
from repositories import opportunities as opps_repo
from repositories import organizations as orgs_repo
from repositories import talks as talks_repo


def organization_response(conn: Connection, user_id: int, org_id: int) -> dict:
    """Build an organization's full detail response, or raise NotFound.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.
    org_id : int
        The organization id.

    Returns
    -------
    dict
        The JSON-ready :class:`models.organizations.Organization` with affiliated contacts and the
        computed ``research_ready`` flag.

    Raises
    ------
    common.errors.NotFound
        When the organization does not exist for this user.
    """
    row = orgs_repo.get_organization(conn, user_id, org_id)
    if row is None:
        raise errors.NotFound("organization not found")
    contacts = orgs_repo.get_affiliated_contacts(conn, user_id, org_id)
    research_ready = is_research_ready(
        row["what_it_is"], row["why_it_fits"], row["how_to_approach"], row["contact_count"]
    )
    organization = Organization(
        **row,
        research_ready=research_ready,
        contacts=[AffiliatedContact(**c) for c in contacts],
    )
    return organization.model_dump(mode="json")


def contact_response(conn: Connection, user_id: int, contact_id: int) -> dict:
    """Build a contact's full detail response, or raise NotFound.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.
    contact_id : int
        The contact id.

    Returns
    -------
    dict
        The JSON-ready :class:`models.contacts.Contact` with its organization affiliations.

    Raises
    ------
    common.errors.NotFound
        When the contact does not exist for this user.
    """
    row = contacts_repo.get_contact(conn, user_id, contact_id)
    if row is None:
        raise errors.NotFound("contact not found")
    affiliations = contacts_repo.get_affiliations(conn, user_id, contact_id)
    contact = Contact(
        **row,
        organizations=[OrganizationAffiliation(**a) for a in affiliations],
    )
    return contact.model_dump(mode="json")


def talk_response(conn: Connection, user_id: int, talk_id: int) -> dict:
    """Build a talk's detail response, or raise NotFound.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.
    talk_id : int
        The talk id.

    Returns
    -------
    dict
        The JSON-ready :class:`models.talks.Talk`.

    Raises
    ------
    common.errors.NotFound
        When the talk does not exist for this user.
    """
    row = talks_repo.get_talk(conn, user_id, talk_id)
    if row is None:
        raise errors.NotFound("talk not found")
    return Talk(**row).model_dump(mode="json")


def opportunity_response(conn: Connection, user_id: int, opp_id: int) -> dict:
    """Build an opportunity's full detail response, or raise NotFound.

    Composes the base row with its linked contacts, dated notes, and status journal — the read-time
    aggregate the frontend refreshes from after every write.

    Parameters
    ----------
    conn : pymysql.connections.Connection
        A live connection.
    user_id : int
        The owning user.
    opp_id : int
        The opportunity id.

    Returns
    -------
    dict
        The JSON-ready :class:`models.opportunities.Opportunity` with contacts, notes, and events.

    Raises
    ------
    common.errors.NotFound
        When the opportunity does not exist for this user.
    """
    row = opps_repo.get_opportunity(conn, user_id, opp_id)
    if row is None:
        raise errors.NotFound("opportunity not found")
    contacts = opps_repo.get_opportunity_contacts(conn, user_id, opp_id)
    notes = opps_repo.get_opportunity_notes(conn, user_id, opp_id)
    events = opps_repo.get_status_events(conn, user_id, opp_id)
    opportunity = Opportunity(
        **row,
        contacts=[OpportunityContact(**c) for c in contacts],
        notes=[OpportunityNote(**n) for n in notes],
        status_events=[StatusEvent(**e) for e in events],
    )
    return opportunity.model_dump(mode="json")
