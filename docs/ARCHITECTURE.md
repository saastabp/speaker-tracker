# Speaker Tracker — Component Architecture

Authoritative map of how the pieces fit: the React SPA, one CloudFront distribution serving both
SPA and API, the Python Lambda handlers, the layered backend, the WorkMail/SES/IMAP email
subsystem, and the CDK stacks that deploy them.

> **Status: pre-implementation.** Nothing here is built yet — this is the *target* architecture and
> the contract the scaffold must satisfy. Derived from `DESIGN.md` §3 and
> `CODING-GUIDELINES.md` §1. Where this doc and a sibling repo disagree, the disagreement is
> deliberate and called out inline.

---

## 1. Runtime request flow

The SPA and the API are **same-origin** — one CloudFront distribution, so there is no CORS and no
environment-specific API URL baked into the frontend build.

```mermaid
flowchart TB
    subgraph Browser["Browser — React 18 SPA, Vite + Mantine"]
        UI["Pages: Dashboard, Pipeline, Venues,<br/>Contacts, Emails, History,<br/>Templates, Targets, Talks"]
        RQ["TanStack Query<br/>cache + optimistic kanban mutations"]
        APICLIENT["api/client.ts — useApi()<br/>fetch('/api'+path)<br/>Bearer JWT + X-User-Timezone<br/>401 → signinRedirect"]
        UI --> RQ --> APICLIENT
    end

    subgraph Auth["Cognito — prod only"]
        COG["User Pool + SPA client + Hosted UI<br/>invite-only, refresh TTL 90d"]
    end

    subgraph CFDIST["CloudFront — one distribution"]
        S3SPA["default behavior → S3 (OAC)<br/>SPA static assets"]
        APIB["/api/* behavior<br/>CF Function strips /api"]
    end

    APIGW["HTTP API Gateway v2<br/>conditional Cognito JWT authorizer<br/>prod = on, sandbox = open"]

    subgraph Lambdas["Route Lambdas — Python 3.12, arm64, OUTSIDE the VPC"]
        LH["handlers/*.py<br/>one module per route-group"]
    end

    subgraph Backend["Layered backend — see section 2"]
        CORE["core/ — pure domain logic"]
        REPO["repositories/ — raw SQL"]
        COMMON["common/ — db, auth, http, tz,<br/>logger, secrets, mail, imap, scheduler"]
    end

    RDS[("RDS MySQL 8 — jobtracker-db<br/>schema: speakertracker<br/>IAM user: speakertracker_app")]
    S3ATT[("S3 — raw MIME + attachments<br/>one-sheets, presigned PUT")]

    UI -->|"HTTPS static"| S3SPA
    Browser -->|"OIDC code flow — react-oidc-context"| COG
    APICLIENT -->|"/api/* · same origin · Bearer JWT"| APIB
    APIB --> APIGW
    APIGW -->|"validates JWT (prod)"| COG
    APIGW --> LH
    LH --> CORE
    LH --> REPO
    CORE -.->|"repository Protocols"| REPO
    REPO --> COMMON
    COMMON -->|"IAM auth token, TLS, public internet"| RDS
    LH --> S3ATT
```

**Key path facts**

- **Same-origin, no CORS.** The browser calls `/api/...`; a CloudFront Function strips the `/api`
  prefix before the HTTP API sees it.
- **Lambdas run outside any VPC** and reach RDS over the public internet with a short-lived **RDS
  IAM auth token** regenerated per invocation. No password in transit, no ENI cold-start penalty;
  the accepted cost is a 2–6s TLS handshake on a cold start.
- **No connection pooling** — one `pymysql` connection per invocation, closed in `finally`.
- Every data handler calls `apply_session_timezone(conn, event)` immediately after connecting, so
  `CURDATE()` and friends evaluate in the caller's local time. **Kauaʻi is UTC-10**, so "today"
  rollover is ten hours off UTC and every date-bucketed metric depends on this.
- Auth is a Cognito JWT authorizer at the gateway in prod. In **sandbox** the authorizer is omitted
  and `AUTH_MODE=dev` injects a fixed user.

### 1.1 Three fixes we are *not* inheriting

legacy-tracker's equivalents of these are broken; porting them verbatim would import the bugs.

| Problem there | What this app does |
|---|---|
| Unhandled exceptions are re-raised, so API Gateway emits `{"message": "Internal Server Error"}` — a **different error shape** from every handled error | A catch-all in `common/http.py` maps unhandled exceptions to `{"error": "internal error"}` + 500, after `logger.exception`. One error shape, always. |
| `UserNotFoundError` subclasses `LookupError`, falls into the re-raise branch, and surfaces as **500 instead of 404** | Domain exceptions map explicitly; `NotFound → 404` including the user lookup. |
| API client never inspects response status — an **expired token returns a raw `Response`** to callers, which renders as a broken page | `useApi()` treats 401 as an auth event and triggers `signinRedirect()`, preserving the intended path. |

### 1.2 Auth UX and session

No full-screen "Login with Cognito" splash. The app **lands on the normal shell** — nav rail, logo,
header — with a **Sign In** link in the header and a sign-in prompt in the content area. A deep link
followed while signed out is stored and restored after the redirect returns.

Session: `refreshTokenValidity` **90 days**, `automaticSilentRenew` rolling the ≤24h access token
over via the **refresh-token grant** (no hidden iframe), tokens in `localStorage` so a browser
restart stays signed in. Donna signs in roughly quarterly. Accepted because she works from a fixed
office desktop — revisit for multi-user or mobile.

Cognito is **invite-only**: `selfSignUpEnabled: false`, admin-created users, `removalPolicy: RETAIN`.
legacy-tracker uses `true` / `DESTROY`; that is not appropriate for a CRM holding a client's contacts
and correspondence.

---

## 2. Backend layering

Three layers, dependencies pointing **inward only** (`CODING-GUIDELINES.md` §1). The handler is the
**composition root**: it constructs concrete repositories and injects them into core, which depends
only on `Protocol`s it defines. That is what keeps `core/` unit-testable with no database.

```mermaid
flowchart LR
    subgraph P["Presentation — handlers/"]
        H["parse · validate · delegate<br/>map result/exception → envelope<br/>entry+exit logging"]
    end
    subgraph C["Core — core/ (PURE, no I/O)"]
        C1["opportunities — status transitions,<br/>closed_at predicate"]
        C2["outreach — kind inference"]
        C3["email_threading — header matching,<br/>subject normalization"]
        C4["funnel · targets · research"]
    end
    subgraph D["Data"]
        R["repositories/ — raw SQL only"]
        M["models/ — pydantic v2"]
    end
    subgraph CM["common/ — side effects at the edge"]
        DB["db.py"]
        SEC["secrets.py"]
        MAIL["mail.py — SES + MIME"]
        IMAP["imap.py"]
        SCH["scheduler.py"]
        HTTP["http.py · auth.py · tz.py · logger.py"]
    end

    H --> C1 & C2 & C3 & C4
    H --> R
    C1 & C2 & C3 & C4 -.->|"Protocol"| R
    C1 & C2 & C3 & C4 --> M
    R --> M
    R --> DB
    H --> HTTP
    MAIL --> SEC
    IMAP --> SEC
```

**The rule that matters:** `core/` imports no `boto3`, no SQL, no HTTP shapes, no clock, no env.
Anything that reads the world is passed in. Concretely — `core/email_threading.py` receives parsed
headers plus candidate rows and *returns a decision*; `common/imap.py` does the talking.

```
backend/src/
  handlers/       presentation — one module per route-group
  core/           business logic — pure
  repositories/   data access — raw SQL, one module per aggregate
  models/         pydantic models — API contracts + typed rows
  migrations/     forward-only .sql
  common/         shared infra
```

**Response envelope** matches the siblings: bare JSON on success (each handler names its own
top-level keys — no `{"data": ...}` wrapper), `{"error": "<message>"}` on failure, with
400 / 404 / 405 / 500 mapped centrally.

---

## 3. Endpoint → handler map

Routes are wired in `infra/cdk/lib/api-stack.ts` via a `ROUTES` table (ported from legacy-tracker).

| Handler | Routes |
|---|---|
| `health.py` | GET `/health` |
| `migrate.py` | *(no route — in-deploy `Trigger`)* |
| `post_confirmation.py` | *(Cognito trigger; prod only, lives in the Auth stack)* |
| `catalogs.py` | GET `/catalogs` |
| `organizations.py` | GET/POST `/organizations`, GET/PUT/DELETE `/organizations/{id}` |
| `contacts.py` | GET/POST `/contacts`, GET/PUT/DELETE `/contacts/{id}`, GET `/contacts/{id}/timeline` |
| `contact_organizations.py` | POST `/contacts/{id}/organizations`, PUT/DELETE `/contacts/{id}/organizations/{orgId}` |
| `opportunities.py` | GET/POST `/opportunities`, GET/PUT/DELETE `/opportunities/{id}`, PATCH `/opportunities/{id}/status`, POST `/opportunities/{id}/close` |
| `opportunity_contacts.py` | POST `/opportunities/{id}/contacts`, PUT/DELETE `/opportunities/{id}/contacts/{contactId}` |
| `opportunity_notes.py` | GET/POST `/opportunities/{id}/notes`, PUT/DELETE `/opportunities/{id}/notes/{noteId}` |
| `outreaches.py` | GET/POST `/outreaches`, GET/PUT/DELETE `/outreaches/{id}` |
| `message_templates.py` | GET/POST `/message-templates`, PUT/DELETE `/message-templates/{id}`, POST `/message-templates/{id}/duplicate` |
| `follow_ups.py` | GET/POST `/follow-ups`, PUT/DELETE `/follow-ups/{id}`, POST `/follow-ups/{id}/complete` |
| `targets.py` | GET/PUT `/targets` |
| `dashboard.py` | GET `/dashboard` |
| `emails.py` | GET `/emails/threads`, GET `/emails/threads/{id}`, PATCH `/emails/threads/{id}` (read / close), POST `/emails/send`, POST `/emails/threads/{id}/reply` |
| `email_imports.py` | GET `/emails/pending-import`, POST `/emails/pending-import/{id}/link` |
| `talks.py` | GET/POST `/talks`, PUT/DELETE `/talks/{id}` |
| `materials.py` | GET/POST `/materials`, POST `/materials/presign`, DELETE `/materials/{id}` |
| `imap_poll.py` | *(no route — EventBridge, 1-minute)* |
| `followup_notify.py` | *(no route — EventBridge Scheduler target)* |
| `seed_sandbox_user.py` | *(no route — sandbox seeding)* |

**History has no handler of its own.** It is closed opportunities:
`GET /opportunities?closed=true` for the table, `GET /opportunities/{id}` for the detail. Adding a
parallel `history.py` would duplicate the same SQL against the same rows.

**Dedupe is a query, not an endpoint.** The add-contact "this person may already exist" step is
`GET /contacts?q=` against the existing list route — no separate search handler.

---

## 4. Email subsystem

The most involved part of the app, and the one that least resembles either sibling — job-tracker's
Gmail OAuth cluster is **not reusable**.

```mermaid
flowchart TB
    subgraph Out["Outbound — composer"]
        COMP["Emails composer (Tiptap)"]
        SEND["emails.py POST /emails/send"]
        MIME["common/mail.py<br/>build raw MIME, stable Message-ID,<br/>In-Reply-To + References on reply"]
        SES["SES SendRawEmail<br/>DKIM-signed by WorkMail domain"]
        APPEND["common/imap.py<br/>APPEND to Sent, found via \\Sent SPECIAL-USE"]
    end

    subgraph Poll["Inbound — imap_poll.py, every 1 min, reserved concurrency 1"]
        CUR["read imap_folder_cursors<br/>check UIDVALIDITY"]
        FETCH["fetch UIDs above watermark<br/>INBOX · \\Sent · Import · Processed"]
        MATCH["core/email_threading.py<br/>In-Reply-To/References → stored Message-ID<br/>fallback: From + normalized subject + window"]
        SCOPE{"in scope?"}
        DROP["ignore — never ingested"]
        STORE["upsert email_messages<br/>UNIQUE(user_id, message_id)"]
        MOVE["Import → Processed"]
    end

    BADGE["email_imports.py<br/>'N emails awaiting import'<br/>→ Add Contact prefilled from From"]
    MBOX[("WorkMail mailbox<br/>Outlook = peer IMAP client")]

    COMP --> SEND --> MIME --> SES --> MBOX
    MIME --> APPEND --> MBOX
    MBOX --> CUR --> FETCH --> SCOPE
    SCOPE -->|"tracked contact address"| MATCH
    SCOPE -->|"in Import folder"| MOVE --> STORE
    SCOPE -->|"neither"| DROP
    MATCH --> STORE
    STORE --> BADGE
```

**Scope — two consented surfaces, never the whole mailbox.** (a) correspondence with a tracked
contact, matched against the `(user_id, email)` index on `contacts`, or against a stored outbound
`Message-ID`; (b) messages Donna explicitly dragged into `Speaker Tracker/Import`. Everything else
is ignored at the poller, not filtered later in the UI.

**Folders are auto-created, never typed.** On first connect and defensively on every poll, the
poller `LIST`s and, if missing, `CREATE`s **and `SUBSCRIBE`s** `Speaker Tracker/Import` and
`Speaker Tracker/Processed`. `SUBSCRIBE` matters — an unsubscribed folder may not appear in
Outlook's tree, which is indistinguishable from the folder never being created. `\Sent` is
*discovered* via SPECIAL-USE, never assumed by name: it is WorkMail's folder, not ours.

**Why a folder move rather than forward-to-import:** an IMAP move transfers the original RFC822
message byte-for-byte, so the `Message-ID` survives and Donna's reply threads correctly at the
venue's end. Forwarding rewrites the `Message-ID`, forcing `.eml`-attachment parsing and
mis-threading the reply.

**Non-negotiables for a 1-minute interval** (retrofitting the cursor means a backfill):

- **Reserved concurrency = 1** — a poll running past 60s must never overlap the next.
- **Per-folder `UIDNEXT` cursor**, with `UIDVALIDITY` checked and the cursor **reset** if it
  changed. Stale UIDs across a UIDVALIDITY change either re-import everything or skip mail forever.
- **Secrets Manager fetch cached at module scope** — not once per minute.
- **`LOGOUT` in a `finally`** on every path. WorkMail caps simultaneous IMAP connections per
  mailbox and Outlook already holds some; leaked connections exhaust the quota.
  *Verify that quota before deploying.*

**Write invariant:** a send writes `email_messages` + `email_threads` + `outreaches` (+ optionally
`follow_ups`) in **one transaction**. A partial write loses the touch or orphans the thread.

**Threading uses RFC 5322 headers only** — `Message-ID` / `In-Reply-To` / `References`. Microsoft's
proprietary `Thread-Index` is not used; external senders don't set it.

---

## 5. Scheduled work

| Trigger | Target | Purpose |
|---|---|---|
| EventBridge **Rule**, `rate(1 minute)` | `imap_poll.py` | Reply threading + drop-folder imports |
| EventBridge **Scheduler**, one-shot `at()` | `followup_notify.py` | Due follow-up reminder via SES |

Follow-up scheduling is ported from job-tracker: deterministic schedule name **`followup-<id>`**, so
create/update/delete need no state read-back; `NotFound` on cancel is swallowed because a one-shot
schedule may already have fired. `common/scheduler.py` **no-ops with a warning** when its env vars
are unset, which lets the Api stack function before the Messaging stack exists.

`followup_notify.py` **never touches the database** — every field needed to render the email travels
in the schedule payload. That keeps it outside the VPC with no SES interface endpoint. The accepted
tradeoff: payloads are snapshots, so editing a follow-up after scheduling requires
cancel-then-recreate (which the handler does).

---

## 6. Infrastructure — CDK stacks

One TypeScript CDK app, parameterized per environment by `authMode`. Stacks wire by **direct
construct reference** — no SSM plumbing between our own stacks. The shared RDS instance is
*referenced* from `/jobtracker/data/*`, never constructed here.

```mermaid
flowchart TB
    JTDATA["/jobtracker/data/*<br/>shared RDS coords via SSM"]
    AUTH["&lt;env&gt;-Auth<br/>Cognito pool + client + Hosted UI<br/>+ post_confirmation (prod)"]
    CERT["&lt;env&gt;-Cert (us-east-1)<br/>ACM cert (prod)"]
    MSG["&lt;env&gt;-Messaging<br/>SES identity · Scheduler group<br/>followup_notify · imap_poll<br/>IMAP secret"]
    API["&lt;env&gt;-Api<br/>HTTP API + route Lambdas<br/>+ migrate Trigger"]
    FE["&lt;env&gt;-Frontend<br/>S3 + CloudFront + Route53 (prod)"]

    JTDATA -->|SSM lookup| API
    JTDATA -->|SSM lookup| MSG
    AUTH -->|userPool + client| API
    MSG -->|"group name + role + notify ARN"| API
    CERT -->|"cert ARN, cross-region"| FE
    API -->|"httpApi as /api/* origin"| FE
```

Acyclic by construction: no SPA↔API URL cycle (same-origin), no auth↔api cycle (`post_confirmation`
lives in `Auth`), and `Messaging` depends on nothing of `Api`'s — `imap_poll` writes to the database
directly and `followup_notify` reads only its payload.

| Stack | Region | Role | Envs |
|---|---|---|---|
| `<env>-Auth` | us-west-2 | Cognito pool, client, Hosted UI, post-confirmation trigger | prod |
| `<env>-Cert` | **us-east-1** | ACM cert for the SPA domain | prod |
| `<env>-Messaging` | us-west-2 | SES identity, Scheduler group + exec role, `followup_notify`, `imap_poll` + its 1-min rule, IMAP secret | prod + sandbox |
| `<env>-Api` | us-west-2 | HTTP API, route Lambdas, migrate Trigger, conditional JWT authorizer | prod + sandbox |
| `<env>-Frontend` | us-west-2 | S3 SPA bucket, CloudFront (S3 + `/api/*` origins), Route53 alias | prod + sandbox |

**Sandbox** deploys the same routes with an **open gateway** and `ENV_TYPE=sandbox` /
`AUTH_MODE=dev`. Port `common/auth.py`'s import-time assertion **verbatim**:

```python
if _AUTH_MODE == "dev" and _ENV_TYPE != "sandbox":
    raise RuntimeError("AUTH_MODE=dev is only allowed when ENV_TYPE=sandbox")
```

A misconfigured prod Lambda then fails at cold start rather than silently accepting anonymous
traffic against Donna's CRM.

**Config vs secrets.** Everything except the IMAP credential is an env var resolved from SSM at
*deploy* time — matching both siblings, which perform no runtime parameter reads. The WorkMail IMAP
credential is the **first runtime secret** in the family: `common/secrets.py`, module-scope cached,
used only by `imap_poll`. Sending needs no credential at all — SES is IAM-authed.

---

## 7. Frontend structure

```
frontend/src/
  pages/        one per route
  components/   shared UI — AppShell, PipelineBoard, ThreadView, composer
  api/          client.ts (useApi) + one hook module per resource
  auth/         config.ts, devAuth.tsx
```

| Path | Page |
|---|---|
| `/` | Dashboard — targets vs actuals, funnel, money rollup, Needs attention |
| `/pipeline` | Kanban board (dnd-kit), full browser width |
| `/venues`, `/venues/{id}` | Organizations list + detail with the Kindling research panel |
| `/contacts`, `/contacts/{id}` | Contacts list + detail with multi-org affiliations and timeline |
| `/opportunities/{id}` | Opportunity detail — fields, linked contacts, dated notes, lifecycle |
| `/emails`, `/emails/{threadId}` | Thread list + thread view with inline reply |
| `/history`, `/history/{id}` | Closed gigs table + detail |
| `/templates`, `/targets`, `/talks` | Templates, Targets, Talks & materials |

**TanStack Query** owns server state — this is the piece both siblings lack, and the optimistic
kanban drag (move card → `PATCH /opportunities/{id}/status` → rollback on failure) is why it is
non-optional here.

**Server-owned ordering.** Stage order, labels, and funnel composition come from `/catalogs` and the
dashboard response. The frontend never re-derives `sort_order` or hardcodes stage names — same
discipline as legacy-tracker's `common/funnel.py`.

Light theme by default (Donna dislikes dark themes); **sans-serif** — the brand guide's Playfair /
Lato pairing is for the public website, not this internal tool. Color carries the brand: navy
`#1F3B4D` nav rail and headings, terracotta `#C2483A` primary actions, gold `#D9A02C` accents and
power-partner ★, cream `#FBF8F2` page background.

---

## 8. Testing & CI

`pytest`, mirroring the source tree under `backend/tests/unit/`. `core/` is pure, so it tests with
no database and no mocking — that is the entire point of the layering. Repository tests exercise
real SQL against a test schema with transaction rollback; handler tests cover validation, the happy
path, and error mapping.

**GitHub Actions from slice 1** — `ruff`, `pytest`, `tsc --noEmit` on PR and push. **No deploy
step**; deploys stay manual.

> Neither sibling has any CI, and legacy-tracker carries **2 tests total** despite being scaffolded
> from job-tracker's ~280. `CODING-GUIDELINES.md` §7 is currently aspirational across the family;
> this is where it stops being.

Highest-value tests, in order: the `closed_at` predicate (§4 of `DATABASE.md`), outreach-kind
inference, `email_threading` header matching including the broken-`References` fallback, and the
UIDVALIDITY reset path.