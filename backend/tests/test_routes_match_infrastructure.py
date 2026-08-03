"""Every backend route must be declared in the CDK's route table, and vice versa.

The HTTP API declares routes **explicitly** rather than as ``ANY /{proxy+}`` — that is what lets
``/health`` stay open while everything else carries the JWT authorizer. The cost is a list in
``infra/cdk/lib/api-stack.ts`` that has to be kept in step with the routers by hand, and its own
comment says so.

Documenting that was not enough: slice 9 shipped seven materials routes, every backend test passed,
CDK synthesised cleanly, and the whole feature 404'd on the deployed API because the table was not
updated. This test turns that comment into a check. It is the only thing in the suite that compares
the application to its infrastructure, and it exists because the failure it catches is invisible
everywhere else — it appears only after a deploy, and looks like a broken feature rather than a
missing line of config.

Parsed with a regex rather than by executing TypeScript: the entries are one-per-line literals, and
a parser that could be fooled by clever formatting would be worse than one that fails loudly if the
file's shape ever changes (see :func:`_cdk_routes`, which asserts it found a plausible number).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import app as app_module

API_STACK = Path(__file__).resolve().parents[2] / "infra" / "cdk" / "lib" / "api-stack.ts"

#: `{ method: apigwv2.HttpMethod.GET, path: '/talks/{id}', authRequired: true }`, however wrapped.
_ROUTE_RE = re.compile(
    r"method:\s*apigwv2\.HttpMethod\.(?P<method>[A-Z]+)\s*,\s*path:\s*'(?P<path>[^']+)'",
    re.MULTILINE,
)

#: Path parameters are spelled `<name>` by Powertools and `{name}` by API Gateway. Neither name is
#: meaningful to the comparison — only the position of a variable segment is.
_PLACEHOLDER_RE = re.compile(r"<[^>]+>|\{[^}]+\}")


def _normalise(method: str, path: str) -> tuple[str, str]:
    return method.upper(), _PLACEHOLDER_RE.sub("{}", path)


def _app_routes() -> set[tuple[str, str]]:
    """Every route the Powertools resolver actually serves."""
    routes = list(app_module.app._dynamic_routes) + list(app_module.app._static_routes)
    return {_normalise(route.method, route.path) for route in routes}


def _cdk_routes() -> set[tuple[str, str]]:
    """Every route declared in the CDK stack's ROUTES table."""
    source = API_STACK.read_text()
    found = {_normalise(m.group("method"), m.group("path")) for m in _ROUTE_RE.finditer(source)}
    # A regex that silently matched nothing would make every assertion below vacuously pass, which
    # is the one way this test could fail to do its job.
    assert len(found) > 20, (
        f"parsed only {len(found)} routes from {API_STACK}; has its shape changed?"
    )
    return found


@pytest.mark.skipif(not API_STACK.exists(), reason="CDK sources not present")
def test_every_backend_route_is_declared_in_the_api_gateway() -> None:
    """A route the gateway does not know about is a 404 no amount of backend testing will catch."""
    missing = _app_routes() - _cdk_routes()
    assert not missing, (
        "These routes exist in the backend but are not declared in api-stack.ts, so they will 404 "
        f"once deployed: {sorted(missing)}"
    )


@pytest.mark.skipif(not API_STACK.exists(), reason="CDK sources not present")
def test_no_route_is_declared_for_an_endpoint_that_does_not_exist() -> None:
    """The other direction: a stale entry routes traffic at a handler that was removed."""
    extra = _cdk_routes() - _app_routes()
    assert not extra, (
        "These routes are declared in api-stack.ts but no backend handler serves them: "
        f"{sorted(extra)}"
    )
