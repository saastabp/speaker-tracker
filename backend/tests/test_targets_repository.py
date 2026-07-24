"""Targets repository tests against a seeded MySQL — upsert, list, delete, tenancy.

Skip without ``TEST_DATABASE_URL`` (see conftest). Mechanize the PUT-upsert on the unique key
(one row per (target_type, cadence), goal updated in place), owner-scoping, and hard-delete unset.
"""

from __future__ import annotations

import pytest

from common import errors
from models.targets import TargetInput
from repositories import targets as targets_repo


def _one(rows: list[dict], target_type: str, cadence: str) -> dict | None:
    return next(
        (r for r in rows if r["target_type"] == target_type and r["cadence"] == cadence), None
    )


def test_upsert_creates_then_updates_in_place(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    targets_repo.upsert_target(
        conn, user_id, TargetInput(target_type="outreaches", cadence="weekly", goal_count=5)
    )
    row = _one(targets_repo.list_targets(conn, user_id), "outreaches", "weekly")
    assert row["goal_count"] == 5
    # Same (type, cadence) upserts in place — no duplicate row.
    targets_repo.upsert_target(
        conn, user_id, TargetInput(target_type="outreaches", cadence="weekly", goal_count=9)
    )
    rows = targets_repo.list_targets(conn, user_id)
    assert (
        len([r for r in rows if r["target_type"] == "outreaches" and r["cadence"] == "weekly"]) == 1
    )
    assert _one(rows, "outreaches", "weekly")["goal_count"] == 9


def test_distinct_type_cadence_pairs_coexist(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    targets_repo.upsert_target(
        conn, user_id, TargetInput(target_type="outreaches", cadence="weekly", goal_count=5)
    )
    targets_repo.upsert_target(
        conn, user_id, TargetInput(target_type="pitches", cadence="monthly", goal_count=3)
    )
    targets_repo.upsert_target(
        conn, user_id, TargetInput(target_type="bookings", cadence="quarterly", goal_count=2)
    )
    rows = targets_repo.list_targets(conn, user_id)
    assert len(rows) == 3
    assert _one(rows, "pitches", "monthly")["goal_count"] == 3


def test_unknown_target_type_rejected(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    with pytest.raises(errors.InvalidInput):
        targets_repo.upsert_target(
            conn, user_id, TargetInput(target_type="nonsense", cadence="weekly", goal_count=1)
        )


def test_delete_unsets_and_is_idempotent(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    targets_repo.upsert_target(
        conn, user_id, TargetInput(target_type="outreaches", cadence="weekly", goal_count=5)
    )
    assert targets_repo.delete_target(conn, user_id, "outreaches", "weekly") is True
    assert targets_repo.list_targets(conn, user_id) == []
    # Deleting an unset target is a no-op.
    assert targets_repo.delete_target(conn, user_id, "outreaches", "weekly") is False


def test_targets_are_owner_scoped(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    targets_repo.upsert_target(
        conn, user_id, TargetInput(target_type="outreaches", cadence="weekly", goal_count=5)
    )
    with conn.cursor() as cur:
        cur.execute("INSERT INTO users (cognito_sub, email) VALUES ('u2', 'u2@example.com')")
        other = cur.lastrowid
    assert targets_repo.list_targets(conn, other) == []
