"""S3 storage tests — no AWS, no database.

The boto3 client is replaced at the ``common.storage._client`` seam (decision #1), matching
``test_secrets.py``. What these pin:

- **key construction**, because the Api stack's IAM grants are prefix-scoped — a key built outside
  the documented prefixes would be denied at runtime, not at review;
- **loud failure**, so a missing object or denied read surfaces the real AWS error instead of an
  empty body standing in for one;
- **the presigned URL's parameters**, since the signed ``ContentType`` is what stops the URL being
  a general-purpose write grant, and the URL itself must never reach a log line.
"""

from __future__ import annotations

import io
import logging

import pytest

from common import storage

BUCKET = "speaker-tracker-sandbox-content"
SIGNED_URL = "https://s3.example.com/put?X-Amz-Signature=deadbeefsecret"


class FakeS3:
    """Stand-in for the boto3 S3 client, recording calls."""

    def __init__(self, *, body: bytes = b"", url: str = SIGNED_URL) -> None:
        self.body = body
        self.url = url
        self.gets: list[dict] = []
        self.puts: list[dict] = []
        self.presigns: list[tuple[str, dict, int]] = []

    def get_object(self, **kwargs) -> dict:
        self.gets.append(kwargs)
        return {"Body": io.BytesIO(self.body)}

    def put_object(self, **kwargs) -> dict:
        self.puts.append(kwargs)
        return {}

    def generate_presigned_url(self, operation: str, Params: dict, ExpiresIn: int) -> str:  # noqa: N803 - boto3's parameter names
        self.presigns.append((operation, Params, ExpiresIn))
        return self.url


@pytest.fixture(autouse=True)
def clean_client(monkeypatch: pytest.MonkeyPatch):
    """Reset the cached client and configure the bucket for every test."""
    storage.reset_client()
    monkeypatch.setenv(storage.CONTENT_BUCKET_ENV, BUCKET)
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    yield
    storage.reset_client()


def install(monkeypatch: pytest.MonkeyPatch, **kwargs) -> FakeS3:
    """Install a fake S3 client at the seam and return it."""
    client = FakeS3(**kwargs)
    monkeypatch.setattr(storage, "_client", lambda: client)
    return client


# --- configuration ------------------------------------------------------------------------------


def test_bucket_name_comes_from_the_environment() -> None:
    assert storage.bucket_name() == BUCKET


def test_missing_bucket_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(storage.CONTENT_BUCKET_ENV, raising=False)
    with pytest.raises(RuntimeError, match=storage.CONTENT_BUCKET_ENV):
        storage.bucket_name()


# --- key construction ---------------------------------------------------------------------------


def test_raw_message_key_strips_brackets_and_scopes_by_user() -> None:
    key = storage.raw_message_key(7, "<abc@example.com>")

    assert key == "email/raw/7/abc@example.com.eml"
    assert key.startswith(storage.RAW_MESSAGE_PREFIX), "prefix-scoped IAM would deny anything else"
    assert "<" not in key and ">" not in key


def test_raw_message_key_tolerates_an_unbracketed_id() -> None:
    assert storage.raw_message_key(1, "abc@example.com") == "email/raw/1/abc@example.com.eml"


def test_raw_message_keys_of_different_users_do_not_collide() -> None:
    assert storage.raw_message_key(1, "<a@x.com>") != storage.raw_message_key(2, "<a@x.com>")


# --- get / put ------------------------------------------------------------------------------------


def test_get_object_returns_the_body(monkeypatch: pytest.MonkeyPatch) -> None:
    client = install(monkeypatch, body=b"raw mime bytes")

    assert storage.get_object_bytes("email/raw/7/a.eml") == b"raw mime bytes"
    assert client.gets == [{"Bucket": BUCKET, "Key": "email/raw/7/a.eml"}]


def test_get_object_propagates_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    # A missing object must raise, never return b"" — an empty body would silently become an
    # empty email body in the thread view.
    class Boom:
        def get_object(self, **kwargs):
            raise PermissionError("NoSuchKey")

    monkeypatch.setattr(storage, "_client", lambda: Boom())
    with pytest.raises(PermissionError, match="NoSuchKey"):
        storage.get_object_bytes("email/raw/7/missing.eml")


def test_put_object_writes_and_returns_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    client = install(monkeypatch)

    key = storage.put_object("email/raw/7/a.eml", b"mime", content_type="message/rfc822")

    assert key == "email/raw/7/a.eml"
    assert client.puts == [
        {
            "Bucket": BUCKET,
            "Key": "email/raw/7/a.eml",
            "Body": b"mime",
            "ContentType": "message/rfc822",
        }
    ]


def test_put_object_defaults_the_content_type(monkeypatch: pytest.MonkeyPatch) -> None:
    client = install(monkeypatch)
    storage.put_object("materials/one-sheet.pdf", b"%PDF")
    assert client.puts[0]["ContentType"] == "application/octet-stream"


def test_put_object_propagates_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class Boom:
        def put_object(self, **kwargs):
            raise PermissionError("AccessDenied")

    monkeypatch.setattr(storage, "_client", lambda: Boom())
    with pytest.raises(PermissionError, match="AccessDenied"):
        storage.put_object("email/raw/7/a.eml", b"mime")


# --- presigned upload -----------------------------------------------------------------------------


def test_presigned_put_signs_bucket_key_and_content_type(monkeypatch: pytest.MonkeyPatch) -> None:
    client = install(monkeypatch)

    url = storage.presigned_put_url(
        f"{storage.ATTACHMENT_PREFIX}7/one-sheet.pdf", content_type="application/pdf"
    )

    assert url == SIGNED_URL
    operation, params, ttl = client.presigns[0]
    assert operation == "put_object"
    assert params["Bucket"] == BUCKET
    assert params["Key"] == "email/attachments/7/one-sheet.pdf"
    # Signing the content type is what keeps the URL from being a general write grant.
    assert params["ContentType"] == "application/pdf"
    assert ttl == storage.PRESIGNED_PUT_TTL_S


def test_presigned_put_honours_an_explicit_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    client = install(monkeypatch)
    storage.presigned_put_url("email/attachments/7/a.pdf", expires_in=60)
    assert client.presigns[0][2] == 60


def test_presigned_url_is_never_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # The URL embeds the signature; logging it would put an upload grant in CloudWatch.
    install(monkeypatch)

    with caplog.at_level(logging.DEBUG):
        storage.presigned_put_url("email/attachments/7/a.pdf")

    assert SIGNED_URL not in caplog.text
    assert "X-Amz-Signature" not in caplog.text


# --- client caching ---------------------------------------------------------------------------


def test_client_is_cached_between_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[int] = []

    def fake_boto_client(service: str, **kwargs):
        created.append(1)
        return FakeS3()

    monkeypatch.setattr(storage.boto3, "client", fake_boto_client)
    storage.reset_client()

    storage._client()
    storage._client()

    assert len(created) == 1, "a warm container must reuse one S3 client"
