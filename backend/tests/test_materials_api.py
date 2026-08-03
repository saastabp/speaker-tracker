"""Materials routes end-to-end through the Powertools resolver, with S3 faked at the seam.

The interesting surface here is not CRUD. It is the three places the API declines to trust the
client, because uploads bypass the API entirely and everything it knows about a file arrives as a
claim:

- **a key is not a capability** — naming an object outside your own prefix is refused, which is what
  stops one caller registering another's file and being handed a presigned URL for it;
- **size and type come from S3**, so the cap cannot be talked around by understating a size;
- **a claimed upload that never happened** is refused rather than recorded, because a row whose
  download 404s reads as data loss.

Skips without ``TEST_DATABASE_URL`` (via ``db_connection``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import app as app_module
from common import storage
from common.auth import Principal
from handlers import context
from migrations.runner import run_migrations

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "src" / "migrations"
SIGNED = "https://s3.example.com/signed?X-Amz-Signature=secret"


class FakeS3:
    """Records presign calls and answers HEAD from a per-key size/type map."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[int, str]] = {}
        self.presigns: list[tuple[str, dict]] = []
        self.deleted: list[str] = []

    def generate_presigned_url(self, operation: str, Params: dict, ExpiresIn: int) -> str:  # noqa: N803 - boto3's names
        self.presigns.append((operation, Params))
        return SIGNED

    def head_object(self, Bucket: str, Key: str) -> dict:  # noqa: N803 - boto3's names
        if Key not in self.objects:
            raise FileNotFoundError("NoSuchKey")
        size, content_type = self.objects[Key]
        return {"ContentLength": size, "ContentType": content_type}

    def delete_object(self, Bucket: str, Key: str) -> dict:  # noqa: N803 - boto3's names
        self.deleted.append(Key)
        return {}


@pytest.fixture
def api(db_connection, monkeypatch):
    """Return ``(call, s3, user_id)`` with the principal, connection and S3 client all faked."""
    run_migrations(db_connection, MIGRATIONS_DIR)
    monkeypatch.setattr(
        context, "principal_from_event", lambda event: Principal(sub="dev", email="dev@example.com")
    )
    monkeypatch.setattr(context, "get_connection", lambda tz: db_connection)
    monkeypatch.setenv(storage.CONTENT_BUCKET_ENV, "test-content-bucket")
    s3 = FakeS3()
    monkeypatch.setattr(storage, "_client", lambda: s3)

    with db_connection.cursor() as cur:
        cur.execute("INSERT INTO users (cognito_sub, email) VALUES ('dev', 'dev@example.com')")
        user_id = cur.lastrowid

    def call(method: str, path: str, body: dict | None = None, params: dict | None = None):
        event = {
            "version": "2.0",
            "routeKey": f"{method} {path}",
            "rawPath": path,
            "rawQueryString": "&".join(f"{k}={v}" for k, v in (params or {}).items()),
            "headers": {"content-type": "application/json"},
            "queryStringParameters": params or None,
            "requestContext": {
                "stage": "$default",
                "http": {"method": method, "path": path, "sourceIp": "1.2.3.4", "userAgent": "t"},
            },
            "body": json.dumps(body) if body is not None else None,
            "isBase64Encoded": False,
        }
        resp = app_module.app.resolve(event, None)
        return resp["statusCode"], (json.loads(resp["body"]) if resp.get("body") else None)

    return call, s3, user_id


def _upload(call, s3, filename="One-Sheet.pdf", *, size=1024, content_type="application/pdf"):
    """Run the real upload-url round trip and put the object in the fake bucket."""
    status, body = call(
        "POST", "/materials/upload-url", {"filename": filename, "content_type": content_type}
    )
    assert status == 200, body
    s3.objects[body["s3_key"]] = (size, content_type)
    return body["s3_key"]


# --- the happy path -------------------------------------------------------------------------------


def test_upload_url_is_scoped_to_the_caller(api) -> None:
    call, s3, user_id = api
    status, body = call(
        "POST", "/materials/upload-url", {"filename": "a.pdf", "content_type": "application/pdf"}
    )

    assert status == 200
    # Server-generated and user-scoped; a client-chosen key would let one caller write elsewhere.
    assert body["s3_key"].startswith(f"materials/{user_id}/")
    assert body["s3_key"].endswith("/a.pdf")
    assert body["upload_url"] == SIGNED
    operation, params = s3.presigns[0]
    assert operation == "put_object"
    assert params["ContentType"] == "application/pdf"


def test_register_records_the_size_s3_reports(api) -> None:
    call, s3, _ = api
    key = _upload(call, s3, size=4096)

    status, body = call("POST", "/materials", {"name": "One-Sheet.pdf", "s3_key": key})

    assert status == 200
    assert body["size_bytes"] == 4096
    assert body["content_type"] == "application/pdf"
    assert call("GET", "/materials")[1]["materials"][0]["id"] == body["id"]


def test_url_is_inline_by_default_and_attachment_on_request(api) -> None:
    call, s3, _ = api
    key = _upload(call, s3)
    mid = call("POST", "/materials", {"name": "One-Sheet.pdf", "s3_key": key})[1]["id"]

    status, body = call("GET", f"/materials/{mid}/url")
    assert status == 200 and body["url"] == SIGNED
    _, params = s3.presigns[-1]
    assert "ResponseContentDisposition" not in params, "a preview displays rather than downloads"

    call("GET", f"/materials/{mid}/url", params={"disposition": "attachment"})
    _, params = s3.presigns[-1]
    assert params["ResponseContentDisposition"] == 'attachment; filename="One-Sheet.pdf"'


# --- the API declines to trust the client ---------------------------------------------------------


def test_a_key_outside_your_prefix_is_refused(api) -> None:
    """The check that stops one caller registering another's object and signing a URL for it."""
    call, s3, _ = api
    s3.objects["materials/999/theirs/secret.pdf"] = (10, "application/pdf")

    status, _ = call(
        "POST", "/materials", {"name": "theirs", "s3_key": "materials/999/theirs/secret.pdf"}
    )

    assert status == 400
    assert call("GET", "/materials")[1]["materials"] == []


def test_a_key_outside_the_materials_prefix_is_refused(api) -> None:
    call, s3, user_id = api
    s3.objects[f"email/raw/{user_id}/a-message.eml"] = (10, "message/rfc822")

    status, _ = call(
        "POST",
        "/materials",
        {"name": "someone's mail", "s3_key": f"email/raw/{user_id}/a-message.eml"},
    )

    assert status == 400


def test_a_claimed_upload_that_never_happened_is_refused(api) -> None:
    # Recording it would list a material whose download 404s — data loss, not a failed upload.
    call, _s3, user_id = api
    status, _ = call(
        "POST", "/materials", {"name": "ghost", "s3_key": f"materials/{user_id}/nope/ghost.pdf"}
    )
    assert status == 400


def test_a_file_over_the_cap_is_refused_and_cleaned_up(api) -> None:
    call, s3, _ = api
    key = _upload(call, s3, "huge.zip", size=storage.MAX_MATERIAL_BYTES + 1)

    status, body = call("POST", "/materials", {"name": "huge.zip", "s3_key": key})

    assert status == 400
    assert "25 MB" in json.dumps(body)
    assert call("GET", "/materials")[1]["materials"] == []
    # Nothing will ever reference the rejected object, so it is not left to linger.
    assert key in s3.deleted


def test_exactly_the_cap_is_allowed(api) -> None:
    """The boundary is inclusive; a file of exactly the limit is not over it."""
    call, s3, _ = api
    key = _upload(call, s3, "big.pdf", size=storage.MAX_MATERIAL_BYTES)
    assert call("POST", "/materials", {"name": "big.pdf", "s3_key": key})[0] == 200


# --- editing --------------------------------------------------------------------------------------


def test_rename_leaves_the_file_alone(api) -> None:
    call, s3, _ = api
    key = _upload(call, s3)
    mid = call("POST", "/materials", {"name": "One-Sheet.pdf", "s3_key": key})[1]["id"]

    status, body = call("PUT", f"/materials/{mid}", {"name": "Donna One-Sheet.pdf"})

    assert status == 200
    assert body["name"] == "Donna One-Sheet.pdf"
    assert body["s3_key"] == key
    assert s3.deleted == [], "a rename must not touch the object"


def test_replacing_the_file_keeps_identity_and_cleans_the_old_object(api) -> None:
    """Overwriting a one-sheet is the normal way the library stays current."""
    call, s3, _ = api
    first = _upload(call, s3, size=1000)
    mid = call("POST", "/materials", {"name": "One-Sheet.pdf", "s3_key": first})[1]["id"]
    second = _upload(call, s3, "One-Sheet.pdf", size=2000)

    status, body = call("PUT", f"/materials/{mid}/file", {"s3_key": second})

    assert status == 200
    assert body["id"] == mid, "identity survives, so anything referring to it still resolves"
    assert body["name"] == "One-Sheet.pdf"
    assert body["s3_key"] == second
    assert body["size_bytes"] == 2000
    assert s3.deleted == [first], "the superseded object is cleaned up, not orphaned"


def test_replacing_with_a_foreign_key_is_refused_and_changes_nothing(api) -> None:
    call, s3, _ = api
    key = _upload(call, s3)
    mid = call("POST", "/materials", {"name": "One-Sheet.pdf", "s3_key": key})[1]["id"]
    s3.objects["materials/999/theirs/x.pdf"] = (5, "application/pdf")

    status, _ = call("PUT", f"/materials/{mid}/file", {"s3_key": "materials/999/theirs/x.pdf"})

    assert status == 400
    assert call("GET", f"/materials/{mid}/url")[0] == 200
    assert s3.deleted == [], "a refused replacement must not delete the file it kept"


def test_remove_hides_it_without_deleting_the_object(api) -> None:
    call, s3, _ = api
    key = _upload(call, s3)
    mid = call("POST", "/materials", {"name": "One-Sheet.pdf", "s3_key": key})[1]["id"]

    assert call("DELETE", f"/materials/{mid}")[0] == 200
    assert call("GET", "/materials")[1]["materials"] == []
    assert call("GET", f"/materials/{mid}/url")[0] == 404
    # The object survives, which is what keeps an undelete possible.
    assert s3.deleted == []
    assert call("DELETE", f"/materials/{mid}")[0] == 404


def test_scoping_the_list_to_a_talk(api) -> None:
    call, s3, _ = api
    talk_id = call("POST", "/talks", {"title": "Wellness Wheel"})[1]["id"]
    general = _upload(call, s3, "general.pdf")
    for_talk = _upload(call, s3, "handout.pdf")
    call("POST", "/materials", {"name": "general.pdf", "s3_key": general})
    call("POST", "/materials", {"name": "handout.pdf", "s3_key": for_talk, "talk_id": talk_id})

    assert len(call("GET", "/materials")[1]["materials"]) == 2
    scoped = call("GET", "/materials", params={"talk_id": talk_id})[1]["materials"]
    assert [m["name"] for m in scoped] == ["handout.pdf"]
