"""Materials repository tests — the index over the files kept in S3.

No S3 here: this layer never touches the bucket. What these pin is the part that is easy to get
wrong once the library is editable — that replacing a file reports the key it superseded, so the
caller can clean it up, and that owner scoping holds on a row carrying an S3 key.

Skip without ``TEST_DATABASE_URL`` (see conftest).
"""

from __future__ import annotations

import pytest

from models.materials import MaterialInput, MaterialUpdate
from models.talks import TalkInput
from repositories import materials as mat
from repositories import talks as talks_repo


def _material(user_suffix: str = "a", **kw) -> MaterialInput:
    base = {"name": "One-Sheet.pdf", "s3_key": f"materials/1/{user_suffix}/One-Sheet.pdf"}
    base.update(kw)
    return MaterialInput(**base)


def _create(conn, user_id: int, **kw) -> int:
    data = _material(**kw)
    return mat.create_material(conn, user_id, data, content_type="application/pdf", size_bytes=1234)


def test_create_records_the_verified_size_and_type(seeded_db) -> None:
    """Size and type come from the caller's S3 read, not from the request body."""
    conn, user_id, _, _ = seeded_db
    mid = _create(conn, user_id)
    row = mat.get_material(conn, user_id, mid)
    assert row["name"] == "One-Sheet.pdf"
    assert row["content_type"] == "application/pdf"
    assert row["size_bytes"] == 1234
    assert row["talk_id"] is None


def test_list_is_owner_scoped(seeded_db, db_connection) -> None:
    # The row carries an S3 key; leaking it would leak a presigned URL for someone else's file.
    conn, user_id, _, _ = seeded_db
    _create(conn, user_id)
    with db_connection.cursor() as cur:
        cur.execute("INSERT INTO users (cognito_sub, email) VALUES ('other', 'other@x')")
        other = cur.lastrowid
    assert mat.list_materials(conn, user_id) != []
    assert mat.list_materials(conn, other) == []
    assert mat.get_material(conn, other, _create(conn, user_id, user_suffix="b")) is None


def test_list_can_scope_to_one_talk(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    talk = talks_repo.create_talk(conn, user_id, TalkInput(title="Wellness Wheel"))
    _create(conn, user_id, user_suffix="general")
    _create(conn, user_id, user_suffix="for-talk", talk_id=talk)
    assert len(mat.list_materials(conn, user_id)) == 2
    scoped = mat.list_materials(conn, user_id, talk_id=talk)
    assert [r["talk_id"] for r in scoped] == [talk]


def test_update_changes_metadata_only(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    talk = talks_repo.create_talk(conn, user_id, TalkInput(title="Legacy in Motion"))
    mid = _create(conn, user_id)
    before = mat.get_material(conn, user_id, mid)

    assert mat.update_material(conn, user_id, mid, MaterialUpdate(name="Renamed.pdf", talk_id=talk))

    row = mat.get_material(conn, user_id, mid)
    assert row["name"] == "Renamed.pdf"
    assert row["talk_id"] == talk
    # The bytes are a separate operation, so a rename cannot disturb what the row points at.
    assert row["s3_key"] == before["s3_key"]
    assert row["size_bytes"] == before["size_bytes"]


def test_replace_repoints_the_row_and_reports_the_old_key(seeded_db) -> None:
    """The returned key is how the superseded object gets cleaned up rather than orphaned."""
    conn, user_id, _, _ = seeded_db
    mid = _create(conn, user_id)
    old_key = mat.get_material(conn, user_id, mid)["s3_key"]

    superseded = mat.replace_material_file(
        conn,
        user_id,
        mid,
        s3_key="materials/1/v2/One-Sheet.pdf",
        content_type="application/pdf",
        size_bytes=9999,
    )

    assert superseded == old_key
    row = mat.get_material(conn, user_id, mid)
    assert row["s3_key"] == "materials/1/v2/One-Sheet.pdf"
    assert row["size_bytes"] == 9999
    # Identity survives a replacement — anything already pointing at this material still resolves.
    assert row["id"] == mid
    assert row["name"] == "One-Sheet.pdf"


def test_replace_reports_nothing_when_no_row_matches(seeded_db) -> None:
    # None means nothing moved, so the caller must not delete the key it was about to write.
    conn, user_id, _, _ = seeded_db
    assert (
        mat.replace_material_file(
            conn, user_id, 999999, s3_key="materials/1/x", content_type="text/plain", size_bytes=1
        )
        is None
    )


def test_soft_delete_hides_without_destroying(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    mid = _create(conn, user_id)

    assert mat.soft_delete_material(conn, user_id, mid)
    assert mat.get_material(conn, user_id, mid) is None
    assert mat.list_materials(conn, user_id) == []
    # Removing twice is not an error the second time, it is simply nothing to do.
    assert mat.soft_delete_material(conn, user_id, mid) is False
    # The key survives, which is what keeps an undelete possible.
    with conn.cursor() as cur:
        cur.execute("SELECT s3_key, deleted_at FROM materials WHERE id = %s", (mid,))
        row = cur.fetchone()
    assert row["s3_key"] and row["deleted_at"] is not None


def test_a_removed_material_cannot_be_updated_or_replaced(seeded_db) -> None:
    conn, user_id, _, _ = seeded_db
    mid = _create(conn, user_id)
    mat.soft_delete_material(conn, user_id, mid)

    assert mat.update_material(conn, user_id, mid, MaterialUpdate(name="X")) is False
    assert (
        mat.replace_material_file(
            conn, user_id, mid, s3_key="materials/1/y", content_type="text/plain", size_bytes=2
        )
        is None
    )


def test_the_same_key_cannot_be_registered_twice(seeded_db) -> None:
    """Two rows on one object would let a delete of either strand the other."""
    conn, user_id, _, _ = seeded_db
    _create(conn, user_id)
    with pytest.raises(Exception):  # noqa: B017 - pymysql's IntegrityError, surfaced by the constraint
        _create(conn, user_id)
