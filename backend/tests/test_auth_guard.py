"""Tests for the import-time auth-mode guard in ``common.auth`` — plan verification #4.

``AUTH_MODE=dev`` is only legal when ``ENV_TYPE=sandbox``; a prod Lambda mistakenly deployed
with dev auth must fail at **cold start** rather than accept anonymous traffic. The guard runs
at import, so each case re-imports the module under a different environment via
``importlib.reload``. No database required.
"""

from __future__ import annotations

import importlib
import os

import pytest

from common import auth as auth_module


@pytest.fixture
def reload_auth():
    """Reload ``common.auth`` under a given (AUTH_MODE, ENV_TYPE), restoring it afterwards.

    Manages the environment directly (not via ``monkeypatch``) so the baseline-restoring reload
    at teardown runs with the original environment in place — a ``monkeypatch`` finalizer would
    revert the env only *after* this fixture's teardown, leaving a reloaded dev-mode module
    leaking into later tests.
    """
    original = {key: os.environ.get(key) for key in ("AUTH_MODE", "ENV_TYPE")}

    def _reload(auth_mode: str | None, env_type: str | None):
        for key, value in (("AUTH_MODE", auth_mode), ("ENV_TYPE", env_type)):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return importlib.reload(auth_module)

    yield _reload

    for key, value in original.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    importlib.reload(auth_module)  # return the module to its baseline state for other tests


@pytest.mark.parametrize("env_type", ["prod", "staging", None])
def test_dev_auth_outside_sandbox_fails_at_import(reload_auth, env_type) -> None:
    # None => ENV_TYPE unset, which defaults to the production-safe value and still trips.
    with pytest.raises(RuntimeError, match="AUTH_MODE=dev is only allowed when ENV_TYPE=sandbox"):
        reload_auth("dev", env_type)


@pytest.mark.parametrize(
    ("auth_mode", "env_type"),
    [("cognito", "prod"), ("cognito", "sandbox"), (None, None), (None, "prod")],
)
def test_cognito_configurations_import_and_read_claims(reload_auth, auth_mode, env_type) -> None:
    module = reload_auth(auth_mode, env_type)
    event = {
        "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "s", "email": "e@x.com"}}}}
    }
    principal = module.principal_from_event(event)
    # Loaded in cognito mode: it reads the JWT claims rather than injecting the dev principal.
    assert principal.sub == "s"
    assert principal.email == "e@x.com"


def test_dev_auth_in_sandbox_is_allowed_and_injects_dev_principal(reload_auth) -> None:
    module = reload_auth("dev", "sandbox")
    # Dev mode ignores the event entirely and returns the fixed sandbox principal.
    principal = module.principal_from_event({})
    assert principal.sub == module.DEV_USER_SUB
    assert principal.email == module.DEV_USER_EMAIL
