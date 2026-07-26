"""Secrets Manager access tests — no AWS, no database.

The boto3 client is replaced at the ``common.secrets._client`` seam (decision #1: CI and local
never reach real AWS), so these run in milliseconds and assert three things that matter:

- **caching behaviour**, because a Secrets Manager round trip per send is waste, and because the
  cache is what makes a rotated password stick until ``refresh=True`` clears it;
- **loud failure on a misconfigured secret**, since the alternative — a half-configured mailbox
  that silently does nothing — is exactly what 6b's acceptance #11 exists to prevent;
- **that the credential never leaks into an exception message or a log line**, which is easy to
  regress the moment someone adds the offending value to an error string to aid debugging.
"""

from __future__ import annotations

import json
import logging

import pytest

from common import secrets

SECRET_ID = "speakertracker/imap"
PASSWORD = "hunter2-do-not-leak"
VALID_SECRET = {"username": "donna.king@360balancedliving.com", "password": PASSWORD}


class FakeClient:
    """Stand-in for the boto3 Secrets Manager client, counting calls."""

    def __init__(self, *responses: dict) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []

    def get_secret_value(self, SecretId: str) -> dict:  # noqa: N803 - boto3's parameter name
        self.calls.append(SecretId)
        # Repeat the final response once exhausted, so a test need only supply what varies.
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[index]


@pytest.fixture(autouse=True)
def clean_cache(monkeypatch: pytest.MonkeyPatch):
    """Reset module-scope state and give every test a configured environment."""
    secrets.reset_cache()
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setenv(secrets.IMAP_SECRET_ENV, SECRET_ID)
    yield
    secrets.reset_cache()


def install(monkeypatch: pytest.MonkeyPatch, *responses: dict) -> FakeClient:
    """Install a fake client at the seam and return it."""
    client = FakeClient(*responses)
    monkeypatch.setattr(secrets, "_client", lambda: client)
    return client


def string_secret(payload: dict | str) -> dict:
    """Build a get_secret_value response carrying `payload` as its SecretString."""
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    return {"SecretString": raw}


# --- caching ----------------------------------------------------------------------------------


def test_second_fetch_is_served_from_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    client = install(monkeypatch, string_secret(VALID_SECRET))

    first = secrets.get_secret_json(SECRET_ID)
    second = secrets.get_secret_json(SECRET_ID)

    assert first == second == VALID_SECRET
    assert len(client.calls) == 1, "a warm container must not re-fetch on every call"


def test_refresh_bypasses_the_cache_and_replaces_it(monkeypatch: pytest.MonkeyPatch) -> None:
    rotated = {**VALID_SECRET, "password": "rotated"}
    client = install(monkeypatch, string_secret(VALID_SECRET), string_secret(rotated))

    assert secrets.get_secret_json(SECRET_ID)["password"] == PASSWORD
    assert secrets.get_secret_json(SECRET_ID, refresh=True)["password"] == "rotated"
    # The refreshed value must also become the new cached value, or the next caller regresses.
    assert secrets.get_secret_json(SECRET_ID)["password"] == "rotated"
    assert len(client.calls) == 2


def test_reset_cache_forces_a_refetch(monkeypatch: pytest.MonkeyPatch) -> None:
    client = install(monkeypatch, string_secret(VALID_SECRET))

    secrets.get_secret_json(SECRET_ID)
    secrets.reset_cache()
    monkeypatch.setattr(secrets, "_client", lambda: client)
    secrets.get_secret_json(SECRET_ID)

    assert len(client.calls) == 2


# --- malformed secrets ------------------------------------------------------------------------


def test_missing_secret_string_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, {"SecretBinary": b"nope"})
    with pytest.raises(RuntimeError, match="no SecretString"):
        secrets.get_secret_json(SECRET_ID)


def test_non_json_secret_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, string_secret("not json at all"))
    with pytest.raises(RuntimeError, match="not valid JSON"):
        secrets.get_secret_json(SECRET_ID)


def test_non_object_json_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, string_secret("[1, 2, 3]"))
    with pytest.raises(RuntimeError, match="must be a JSON object"):
        secrets.get_secret_json(SECRET_ID)


def test_a_failed_fetch_propagates_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    # A denied or missing secret must surface as the real AWS error, not a swallowed one.
    class Boom:
        def get_secret_value(self, SecretId: str):  # noqa: N803 - boto3's parameter name
            raise PermissionError("AccessDeniedException")

    monkeypatch.setattr(secrets, "_client", lambda: Boom())
    with pytest.raises(PermissionError, match="AccessDeniedException"):
        secrets.get_secret_json(SECRET_ID)


# --- the credential must never leak -------------------------------------------------------------


def test_parse_failure_does_not_leak_the_secret_body(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # The unparseable body IS the credential; the obvious f"could not parse {raw}" would put it in
    # CloudWatch forever.
    leaky_body = f"username=donna&password={PASSWORD}"
    install(monkeypatch, string_secret(leaky_body))

    with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError) as excinfo:
        secrets.get_secret_json(SECRET_ID)

    assert PASSWORD not in str(excinfo.value)
    assert PASSWORD not in caplog.text


def test_successful_fetch_does_not_log_the_secret(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    install(monkeypatch, string_secret(VALID_SECRET))

    with caplog.at_level(logging.DEBUG):
        secrets.get_imap_credentials()

    assert PASSWORD not in caplog.text


# --- IMAP credentials -------------------------------------------------------------------------


def test_imap_credentials_are_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, string_secret(VALID_SECRET))

    creds = secrets.get_imap_credentials()

    assert creds.username == VALID_SECRET["username"]
    assert creds.password == PASSWORD


def test_imap_refresh_reaches_secrets_manager_again(monkeypatch: pytest.MonkeyPatch) -> None:
    # The rotation-recovery path: an auth failure retries with refresh=True.
    rotated = {**VALID_SECRET, "password": "rotated"}
    client = install(monkeypatch, string_secret(VALID_SECRET), string_secret(rotated))

    assert secrets.get_imap_credentials().password == PASSWORD
    assert secrets.get_imap_credentials(refresh=True).password == "rotated"
    assert len(client.calls) == 2


def test_unset_secret_id_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, string_secret(VALID_SECRET))
    monkeypatch.delenv(secrets.IMAP_SECRET_ENV, raising=False)

    with pytest.raises(RuntimeError, match=secrets.IMAP_SECRET_ENV):
        secrets.get_imap_credentials()


@pytest.mark.parametrize(
    ("payload", "missing"),
    [
        ({"username": "donna@x.com"}, "password"),
        ({"password": PASSWORD}, "username"),
        ({"username": "", "password": ""}, "username, password"),
    ],
)
def test_incomplete_secret_names_the_missing_keys(
    monkeypatch: pytest.MonkeyPatch, payload: dict, missing: str
) -> None:
    install(monkeypatch, string_secret(payload))

    with pytest.raises(RuntimeError) as excinfo:
        secrets.get_imap_credentials()

    assert missing in str(excinfo.value)
    assert PASSWORD not in str(excinfo.value)


# --- region resolution ------------------------------------------------------------------------


def test_region_prefers_the_explicit_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRETS_REGION", "us-east-1")
    assert secrets._region() == "us-east-1"


def test_region_falls_back_to_aws_region(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SECRETS_REGION", raising=False)
    assert secrets._region() == "us-west-2"


def test_region_missing_entirely_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SECRETS_REGION", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    with pytest.raises(RuntimeError, match="AWS_REGION"):
        secrets._region()
