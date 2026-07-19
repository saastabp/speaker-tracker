# Coding Guidelines — Backend

Conventions for the Python 3.12 Lambda backend. Frontend conventions live in a separate doc
(to be written). The global `~/.claude/CLAUDE.md` is **authoritative** for cross-cutting rules
(NumPy docstrings, API logging) — this doc points to those and adds project-specific structure;
where they overlap, the global rules win.

## 1. Layering & boundaries

Three layers, and **dependencies point inward** — presentation → core → data. A layer may call
inward, never outward. Nothing bleeds across.

| Layer | Directory | Responsibility | May import | Must NOT contain |
|---|---|---|---|---|
| **Presentation** | `handlers/` | Parse + validate the request, call core, format the response, boundary logging | core, models, common | Business rules, SQL, direct domain boto3 |
| **Core (business logic)** | `core/` | Pure domain logic / services on models | models, repository *protocols*, stdlib | HTTP/event shapes, SQL, boto3, network, clock/env reads |
| **Data** | `repositories/`, `models/`, `migrations/` | Persistence — the **only** place SQL lives; Pydantic row models | common (`db.py`), models, stdlib | Business rules, HTTP shapes |

- **Dependency inversion:** `core` depends on repository **`Protocol`s** it defines, not concrete
  implementations. The handler is the composition root — it wires concrete repositories into core.
  This keeps core pure and unit-testable without a database.
- **The handler is thin:** validate → delegate to core → map result/exception to an HTTP envelope.
  If a handler grows business logic, move it to `core/`.

```
backend/src/
  handlers/       # presentation — one module per route-group (routeKey dispatch)
  core/           # business logic — pure, no I/O
  repositories/   # data access — raw SQL, one module per aggregate
  models/         # pydantic models (api requests/responses + typed rows)
  migrations/     # forward-only .sql, tracked in schema_migrations
  common/         # shared infra — db.py, logger.py, http.py, auth.py, tz.py, mail.py
```

## 2. Data & persistence

- **Pydantic v2** for API request/response models (validate at the boundary — mirrors the frontend
  Zod schemas) and for typed row/domain models. Keep API models and persistence models
  *separable*: they may coincide for simple CRUD, but don't couple the DB schema to the API
  contract.
- **Raw-SQL repositories** over `pymysql` via `common/db.py` (per-invocation RDS IAM token). **No
  ORM.** Parameterized queries only — never string-format values into SQL.
- **Migrations:** forward-only, hand-written `.sql` in `migrations/`, tracked in
  `schema_migrations`, applied by the migrate Lambda (reuse the siblings' runner). No Alembic /
  autogeneration.
- **Catalog tables over ENUMs** (see `DESIGN.md`); resolve `short_name` ↔ `id` at the repository
  boundary so callers pass `short_name`.
- **Connections are reused at module scope**, not opened per invocation. Because a *single* Lambda
  serves every API route (see `ARCHITECTURE.md` §2), a container handles many requests, and a cold
  TLS handshake to RDS costs 2–6s — paying that per request is the difference between a snappy CRM
  and an unusable one. It also keeps connection count low on a `db.t4g.micro` shared with two
  sibling apps.
  - **Liveness is probed by the per-request `SET time_zone`** — needed anyway, so it costs no extra
    round trip. On `OperationalError` 2003/2006/2013/2055 or `InterfaceError`: close, reconnect
    **once** with a *fresh* IAM token, re-probe. A second failure is a real outage, not a stale
    socket — never loop.
  - 🚫 **Never `ping(reconnect=True)`.** It reconnects using credentials stored on the connection —
    the **expired IAM token** — so it fails on any container older than 15 minutes, intermittently
    and unreproducibly. It also silently discards session state and any open transaction. Enforced
    by a ruff `banned-api` rule, not by review.
  - Connect with `autocommit=True` and wrap multi-statement writes in a
    `@contextmanager transaction(conn)`. **Reuse introduces a hazard per-invocation connections did
    not have:** a handler raising mid-transaction otherwise leaves InnoDB locks held into the *next*
    invocation.
  - **Never connect at import time** — a DB outage should fail `/catalogs`, not take `/health` down
    with an init error.
  - TLS: ship the RDS global CA bundle and set `ssl_verify_cert=True` **and
    `ssl_verify_identity=True`. Over a public endpoint, omitting the latter leaves you
    encrypted-but-MITM-able**, which defeats the point.

## 3. Functions & modules

- **Primitives do one thing, with no side effects.** Pure and deterministic where possible.
  Separate pure computation from I/O.
- **Isolate side effects at the edges** (repositories, handlers, `common`): DB, network, clock,
  randomness, env reads. Core stays pure → trivial to unit-test.
- **Pass dependencies in** (as parameters); don't reach for globals/singletons inside core.
- **File size:** **~300 lines is the target, ~500 means refactor now, 1000 is a hard red line
  (never).** A large file signals too many responsibilities — split it (one route-group per
  handler file, one aggregate per repository, cohesive core modules). Prefer many small cohesive
  files over a few large ones.

## 4. Reuse over hand-rolling

- **Use maintained packages; don't reinvent.** Before writing a utility, check stdlib and PyPI. A
  hand-rolled helper that duplicates a package's job needs a one-line justification in the code.
- Go-to packages for this stack: **aws-lambda-powertools** (structured logging, correlation IDs, and
  the `APIGatewayHttpResolver` + `Router` that route the single API Lambda), **boto3** (AWS/SES/
  Secrets Manager), **pydantic** v2 (validation/models), **pymysql** (DB), **sqlparse** (splitting
  migration files — see below), **tenacity** (retries/backoff), stdlib **`email` / `email.mime`**
  (build MIME), **imapclient** (IMAP), stdlib **`zoneinfo`** / **python-dateutil** (timezones —
  Kauaʻi is UTC-10).
- **`uv`** manages dependencies and builds the Lambda bundles (`uv.lock` for reproducibility,
  `--python-platform aarch64-manylinux2014` so compiled wheels match arm64).
- Don't reimplement retries, JSON envelopes, date parsing, MIME assembly, or connection handling.
- **Don't hand-roll SQL statement splitting.** Naive `split(";")` breaks on semicolons inside string
  literals and comments — which catalog `description` seeds will eventually contain. Use
  `sqlparse.split()`.

## 5. Documentation

- **NumPy-style docstrings** on every public function/method/class (per global `CLAUDE.md`:
  one-line summary, `Parameters`, `Returns`/`Yields`, `Raises`, `Examples` for non-obvious usage;
  `name : type` headers; private functions get a one-line summary). The global rule governs — don't
  restate it, follow it.
- **Design docs mirror legacy-tracker:**
  - `docs/DESIGN.md` — high-level design of record (current).
  - `docs/DATABASE.md` — schema reference + a Mermaid **ERD** (` ```mermaid erDiagram `) of tables
    and relationships.
  - `docs/ARCHITECTURE.md` — the layer/module boundaries + a Mermaid **flowchart** of module
    dependencies and request paths.
  - `docs/slices/NN-*.md` — per-feature design notes.
- **Mermaid diagrams live in-repo** (GitHub renders them). Update the diagram in the *same* change
  that alters the schema or module graph, so docs don't drift.

## 6. Logging & observability

Follow the global **API-logging discipline** (authoritative in `CLAUDE.md`) on every handler:

- **Entry log** — correlation/request id, operation/route, key non-sensitive ids.
- **Exit log** — status, `duration_ms` from entry, same correlation id (entry/exit pairable).
- **Error responses** — `logger.exception(...)` (full traceback), the redacted response body, and
  the correlation id, at `ERROR`.
- **Key operations** — external calls (target/status/latency), DB writes (operation + entity id,
  not full rows), state transitions (`from → to`), authz decisions.
- Use **aws-lambda-powertools Logger** (or stdlib `logging`) — never `print`. **Lazy `%s`
  formatting**, not f-strings, inside log calls. `logger.exception` in `except` blocks.
- **Never log:** passwords, tokens, API keys, IMAP/SES/WorkMail creds, raw bodies that may hold
  secrets, full PII.

## 7. Testing

- **pytest.** Unit-test `core/` as pure functions (no DB/network). Repository tests exercise the
  SQL (test schema / transaction rollback). Handler tests cover request validation, the happy
  path, and error mapping.
- Tests mirror the source tree: `tests/unit/test_<module>.py`.
- Aim for meaningful coverage of core + repositories; don't chase 100%.

## 8. Error handling

- **Core raises typed domain exceptions** (`NotFound`, `Conflict`, `ValidationError`, …) — not HTTP
  concerns. **Presentation maps** domain exceptions → HTTP status + JSON envelope. Repositories
  raise on integrity/no-row (e.g. `FOUND_ROWS` → 0 rows matched = `NotFound`).
- No bare `except`; catch specific exceptions; `logger.exception` then re-raise or map.