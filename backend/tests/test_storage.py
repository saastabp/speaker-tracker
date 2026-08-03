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

from common import aws, storage

BUCKET = "speaker-tracker-sandbox-content"
SIGNED_URL = "https://s3.example.com/put?X-Amz-Signature=deadbeefsecret"


class FakeS3:
    """Stand-in for the boto3 S3 client, recording calls."""

    def __init__(
        self,
        *,
        body: bytes = b"",
        url: str = SIGNED_URL,
        head_content_type: str = "application/pdf",
    ) -> None:
        self.body = body
        self.url = url
        self.head_content_type = head_content_type
        self.gets: list[dict] = []
        self.puts: list[dict] = []
        self.heads: list[dict] = []
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

    def head_object(self, **kwargs) -> dict:
        self.heads.append(kwargs)
        return {"ContentLength": len(self.body), "ContentType": self.head_content_type}


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


# --- presigned GET / head (the materials library) ------------------------------------------------


def test_presigned_get_is_inline_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """A preview wants the browser to display the object, not save it."""
    client = install(monkeypatch)

    url = storage.presigned_get_url(f"{storage.MATERIAL_PREFIX}7/one-sheet.pdf")

    assert url == SIGNED_URL
    operation, params, ttl = client.presigns[0]
    assert operation == "get_object"
    assert params["Bucket"] == BUCKET
    assert params["Key"] == "materials/7/one-sheet.pdf"
    assert "ResponseContentDisposition" not in params
    assert ttl == storage.PRESIGNED_GET_TTL_S


def test_presigned_get_can_force_a_download(monkeypatch: pytest.MonkeyPatch) -> None:
    client = install(monkeypatch)

    storage.presigned_get_url("materials/7/x.pdf", download_as="Donna One-Sheet.pdf")

    _, params, _ = client.presigns[0]
    assert params["ResponseContentDisposition"] == 'attachment; filename="Donna One-Sheet.pdf"'


def test_presigned_get_strips_quotes_from_the_filename(monkeypatch: pytest.MonkeyPatch) -> None:
    """A quote in the name would otherwise break out of the header it is embedded in."""
    client = install(monkeypatch)

    storage.presigned_get_url("materials/7/x.pdf", download_as='we"ird.pdf')

    _, params, _ = client.presigns[0]
    assert params["ResponseContentDisposition"] == 'attachment; filename="weird.pdf"'


def test_head_object_reports_size_and_type(monkeypatch: pytest.MonkeyPatch) -> None:
    client = install(monkeypatch, body=b"x" * 1234, head_content_type="application/pdf")

    size, content_type = storage.head_object("materials/7/one-sheet.pdf")

    assert (size, content_type) == (1234, "application/pdf")
    assert client.heads[0] == {"Bucket": BUCKET, "Key": "materials/7/one-sheet.pdf"}


def test_head_object_defaults_a_missing_content_type(monkeypatch: pytest.MonkeyPatch) -> None:
    client = install(monkeypatch, body=b"abc")
    client.head_content_type = ""

    assert storage.head_object("materials/7/x")[1] == "application/octet-stream"


# --- client caching ---------------------------------------------------------------------------


def test_client_is_cached_between_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[int] = []

    def fake_boto_client(service: str, **kwargs):
        created.append(1)
        return FakeS3()

    # Patched on `common.aws`, which now owns the construction — `storage` no longer imports boto3
    # itself. The caching being asserted is still storage's: `client_for` builds a client per call,
    # and `_client` is what makes a warm container reuse one.
    monkeypatch.setattr(aws.boto3, "client", fake_boto_client)
    storage.reset_client()

    storage._client()
    storage._client()

    assert len(created) == 1, "a warm container must reuse one S3 client"
