"""Test the catalogs repository against a seeded database — plan verification #6.

Applies the real migration, then asserts every vocabulary is present and the three flag
catalogs (is_terminal / is_settled / counts_toward_target) carry correct values. Skips without
``TEST_DATABASE_URL``.
"""

from __future__ import annotations

from pathlib import Path

from migrations.runner import run_migrations
from repositories.catalogs import fetch_catalogs

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "src" / "migrations"


def _names(items) -> list[str]:
    return [item.short_name for item in items]


def test_fetch_catalogs_returns_every_vocabulary(db_connection) -> None:
    run_migrations(db_connection, MIGRATIONS_DIR)
    catalogs = fetch_catalogs(db_connection)

    assert _names(catalogs.organization_types) == [
        "retreat_venue",
        "resort",
        "yoga_studio",
        "spa",
        "womens_network",
        "podcast",
        "expo",
        "corporate",
        "other",
    ]
    assert _names(catalogs.warmth_tiers) == ["cold", "lukewarm", "warm"]
    assert _names(catalogs.contact_roles) == ["primary", "introducer", "coordinator", "backup"]
    assert _names(catalogs.opportunity_formats) == [
        "workshop",
        "keynote",
        "podcast_spot",
        "expo_table",
        "panel",
        "other",
    ]
    assert _names(catalogs.comp_types) == ["paid", "pro_bono", "trade"]
    assert _names(catalogs.outreach_channels) == ["email", "dm", "call", "in_person", "text"]
    assert _names(catalogs.target_types) == [
        "venues_researched",
        "outreaches",
        "pitches",
        "bookings",
    ]

    terminal = {status.short_name: status.is_terminal for status in catalogs.opportunity_statuses}
    assert terminal["delivered"] is True
    assert terminal["cancelled"] is True
    assert terminal["lost"] is True
    assert terminal["researching"] is False
    assert "nurture" not in terminal  # retired in 0004

    settled = {status.short_name: status.is_settled for status in catalogs.payment_statuses}
    assert settled == {
        "unbilled": False,
        "invoiced": False,
        "partial": False,
        "paid": True,
        "n_a": True,
    }

    counts = {kind.short_name: kind.counts_toward_target for kind in catalogs.outreach_kinds}
    assert counts == {"initial": True, "follow_up": True, "correspondence": False}
