# Speaker Tracker — Database Schema

**MySQL 8.4.8** (`ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci`), schema
`speakertracker` on the shared `jobtracker-db` instance —
`db.t4g.micro`, 20 GB, publicly accessible with IAM auth enabled, in **us-west-2**, account
**381492047863**. Migrations live in `backend/src/migrations/*.sql`, applied forward-only in lexical
filename order by `handlers/migrate.py`, tracked in `schema_migrations`. IAM DB user:
`speakertracker_app`.

> **`db.t4g.micro` / 20 GB is shared with `jobtracker` and `legacytracker`.** This schema's volume is
> trivial (hundreds of opportunities, thousands of emails) — except `email_messages`, whose raw MIME
> is deliberately kept in **S3**, not the database. Keep it that way; a `MEDIUMTEXT` of raw MIME per
> message would exhaust 20 GB far faster than any other table here.

> **Status: implemented through migration `0006` (slices 1–5), plus `0007_target_labels` (UX
> reconciliation — catalog label update, no schema change).** This document is the schema contract;
> migrations `0001`–`0006` satisfy it, while slices 6–8 (`0008`–`0010`) remain target schema.
> Derived from `DESIGN.md` §4/§5 and supersedes any older sketch.

**Conventions** (inherited from the sibling apps, see `CODING-GUIDELINES.md` §2):

- Every entity table carries `id BIGINT AUTO_INCREMENT PK`, `created_at`,
  `updated_at … ON UPDATE CURRENT_TIMESTAMP`, and `deleted_at TIMESTAMP NULL` (soft delete).
- `user_id → users(id)` is `ON DELETE CASCADE` everywhere (tenant root), even though the app is
  single-user today — it costs nothing now and is painful to retrofit.
- **Catalog tables, not ENUMs.** Every catalog shares
  `id / short_name UK / description / sort_order / created_at / updated_at / deleted_at`.
  Repositories resolve `short_name ↔ id` at the boundary so callers pass `short_name`.
  Two deliberate exceptions are noted in §5.
- Money is `DECIMAL(10,2)` — never `FLOAT`.

---

## 1. ER diagram

Catalog tables are drawn separately (§1.2) to keep the entity graph legible.

### 1.1 Entities

```mermaid
erDiagram
    users ||--o{ organizations : owns
    users ||--o{ contacts : owns
    users ||--o{ opportunities : owns
    users ||--o{ outreaches : owns
    users ||--o{ email_threads : owns
    users ||--o{ follow_ups : owns
    users ||--o{ targets : owns
    users ||--o{ talks : owns
    users ||--o{ message_templates : "owns (NULL = shared)"

    organizations ||--o{ contact_organizations : "affiliations"
    contacts ||--o{ contact_organizations : "affiliations"
    organizations ||--o{ opportunities : "hosts"

    opportunities ||--o{ opportunity_contacts : "people on this gig"
    contacts ||--o{ opportunity_contacts : "gigs"
    opportunities ||--o{ opportunity_notes : "dated notes"
    opportunities ||--o{ status_events : "transitions"
    opportunities }o--o| talks : "talk_id (which talk offered)"

    contacts ||--o{ outreaches : "outbound touches"
    opportunities ||--o{ outreaches : "optional attribution"
    outreaches }o--o| email_messages : "email_message_id (SET NULL)"
    outreaches }o--o| message_templates : "message_template_id (SET NULL)"

    email_threads ||--o{ email_messages : "messages"
    email_threads }o--o| contacts : "contact_id (NULLABLE)"
    email_threads }o--o| opportunities : "opportunity_id (NULLABLE)"

    contacts ||--o{ follow_ups : "reminders"
    opportunities ||--o{ follow_ups : "reminders"

    talks ||--o{ materials : "files"

    users {
        bigint id PK
        varchar cognito_sub UK
        varchar email
        varchar display_name
        varchar timezone "default Pacific/Honolulu"
        timestamp deleted_at
    }
    organizations {
        bigint id PK
        bigint user_id FK
        bigint organization_type_id FK
        varchar name
        varchar location
        varchar website_url
        varchar email_domain "import: suggest org from sender"
        text what_it_is "Kindling"
        text why_it_fits "Kindling"
        text how_to_approach "Kindling"
        text notes
        timestamp deleted_at
    }
    contacts {
        bigint id PK
        bigint user_id FK
        bigint warmth_tier_id FK
        varchar name
        varchar email "indexed - poller matches From"
        varchar phone
        varchar source
        text how_you_know
        text notes
        timestamp deleted_at
    }
    contact_organizations {
        bigint id PK
        bigint contact_id FK
        bigint organization_id FK
        varchar title "role AT THIS ORG"
        bool is_primary "default contact for the org"
        bool is_power_partner "power partner AT THIS venue"
    }
    opportunities {
        bigint id PK
        bigint user_id FK
        bigint organization_id FK
        bigint talk_id FK "nullable"
        bigint opportunity_format_id FK
        bigint current_status_id FK "denorm"
        bigint comp_type_id FK
        bigint payment_status_id FK
        varchar title
        date event_date
        decimal fee_amount "10,2"
        char currency "3, default USD"
        date paid_on
        text angle "seeded from how_to_approach"
        text outcome
        timestamp closed_at "denorm - see section 4"
        timestamp deleted_at
    }
    opportunity_contacts {
        bigint id PK
        bigint opportunity_id FK
        bigint contact_id FK
        bigint contact_role_id FK
        bool is_primary "lead on this gig"
    }
    opportunity_notes {
        bigint id PK
        bigint user_id FK
        bigint opportunity_id FK
        mediumtext body
        timestamp occurred_at
        timestamp deleted_at
    }
    status_events {
        bigint id PK
        bigint user_id FK
        bigint opportunity_id FK
        bigint status_id FK
        text note "close reason"
        timestamp occurred_at
    }
    outreaches {
        bigint id PK
        bigint user_id FK
        bigint contact_id FK
        bigint opportunity_id FK "nullable"
        bigint outreach_kind_id FK
        bigint outreach_channel_id FK
        bigint message_template_id FK
        bigint email_message_id FK
        text note
        timestamp occurred_at
        timestamp deleted_at
    }
    email_threads {
        bigint id PK
        bigint user_id FK
        bigint contact_id FK "NULLABLE"
        bigint opportunity_id FK "NULLABLE"
        varchar subject_normalized
        enum last_direction "out|in"
        timestamp last_message_at
        timestamp last_read_at "NULL = never opened"
        timestamp closed_at "explicit - no reply needed"
        timestamp deleted_at
    }
    email_messages {
        bigint id PK
        bigint user_id FK
        bigint thread_id FK
        bigint contact_id FK "NULLABLE"
        bigint opportunity_id FK "NULLABLE"
        varchar message_id "UK with user_id"
        varchar in_reply_to
        text message_references
        enum direction "out|in"
        varchar subject
        varchar from_addr
        text to_addr
        text cc_addr
        varchar s3_key "raw MIME"
        varchar imap_folder
        bigint imap_uid
        timestamp sent_at
        timestamp received_at
    }
    follow_ups {
        bigint id PK
        bigint user_id FK
        bigint contact_id FK "nullable"
        bigint opportunity_id FK "nullable"
        date due_date
        text note
        bool remind_by_email
        timestamp completed_at "NULL = pending"
        timestamp deleted_at
    }
    message_templates {
        bigint id PK
        bigint user_id FK "NULL = shared"
        bigint message_template_kind_id FK "purpose"
        bigint channel_id FK "outreach_channels - how it is sent"
        varchar name
        varchar subject
        mediumtext body "merge fields"
        timestamp deleted_at
    }
    targets {
        bigint id PK
        bigint user_id FK
        bigint target_type_id FK
        enum cadence "weekly|monthly|quarterly"
        int goal_count
    }
    talks {
        bigint id PK
        bigint user_id FK
        varchar title
        int length_minutes
        text one_liner
        int sort_order
        timestamp deleted_at
    }
    materials {
        bigint id PK
        bigint user_id FK
        bigint talk_id FK "nullable"
        varchar name
        varchar s3_key
        varchar content_type
        bigint size_bytes
        timestamp deleted_at
    }
    imap_folder_cursors {
        bigint id PK
        bigint user_id FK
        varchar folder_name UK
        bigint uid_validity
        bigint last_seen_uid
        timestamp last_polled_at
    }
```

### 1.2 Catalogs

All share `id / short_name UK / description / sort_order / created_at / updated_at / deleted_at`;
extra columns noted.

```mermaid
erDiagram
    organization_types { bigint id PK }
    warmth_tiers { bigint id PK }
    contact_roles { bigint id PK }
    opportunity_statuses { bigint id PK }
    opportunity_formats { bigint id PK }
    comp_types { bigint id PK }
    payment_statuses { bigint id PK }
    outreach_kinds { bool counts_toward_target }
    outreach_channels { bigint id PK }
    message_template_kinds { bigint id PK }
    target_types { bigint id PK }
```

---

## 2. Entity tables

### `users`
Tenant root. `cognito_sub` UNIQUE — populated by the API's first-authenticated-request upsert
(`handlers/context.py` → `repositories/users.upsert_user_id`, idempotent), the **source of truth**
for the row. No Cognito `post_confirmation` trigger is used — an `AdminCreateUser` user is
pre-confirmed so it may never fire, and the API upsert is authoritative (see `DEV-PLAN.md` acceptance #4). `timezone` defaults to `Pacific/Honolulu`;
the `X-User-Timezone` header still governs per-request `SET time_zone` (Kauaʻi is UTC-10, so
"today" rollover is 10 hours off UTC and every date-bucketed metric depends on it).

### `organizations`
Venues, orgs, podcasts, expos. Holds the three **Kindling** research columns from `DESIGN.md` §5
(`what_it_is`, `why_it_fits`, `how_to_approach`) as structured columns, *not* a notes blob —
research-readiness (§4) is computed from them.

`email_domain` exists for the drop-folder import: when an unknown sender is imported, the sender's
domain is matched here to suggest an existing organization before offering to create one.
Indexes: `(user_id, name)`, `(user_id, organization_type_id)`, `(user_id, email_domain)`.
Organization **names are unique per user among live rows**: a generated `name_key` (the name when
`deleted_at IS NULL`, else NULL) backs `UNIQUE(user_id, name_key)`, so a soft-deleted name can be
re-used while two live orgs cannot collide. Contacts, by contrast, are deduped by *search*, not a
constraint — one person legitimately spans venues, and email may be absent.

### `contacts`
The *person*, deliberately with **no `organization_id`** — affiliation lives in
`contact_organizations`, because on Kauaʻi one person is frequently the contact for several venues.
Power-partnership is **not** a person-level flag: it lives per-affiliation on
`contact_organizations` (a person can be a power partner at one venue and not another).

**`(user_id, email)` is a load-bearing index, not a convenience.** The IMAP poller resolves every
inbound `From` address against it on every poll to decide whether a message is in scope at all
(`DESIGN.md` §3). Without it the poller table-scans contacts once a minute.

> ⚠️ **Known limitation: one email address per contact.** A coordinator who writes from both
> `jane@venue.com` and a personal address will match only on the stored one; the second address
> lands in the unknown-sender import flow and, on import, offers to attach to the existing contact
> (dedupe) — but the address still isn't retained for future matching. If this bites in practice
> the fix is a `contact_email_addresses` child table; it is deliberately **not** built now.

### `contact_organizations`
Many-to-many affiliation. `UNIQUE(contact_id, organization_id)` — this is what makes the
add-contact dedupe flow safe: adding an existing person to a second venue creates a new
*affiliation*, never a duplicate contact. `title` is their role **at that org** ("Events Chair" at
PWN, "Member" at BNI). `is_primary` = the go-to contact for that org; `is_power_partner` = a power
partner **at that venue** (both flags are per-edge, so they can differ across a person's venues).
**At most one primary per org** is an application invariant enforced on every write (no DB
constraint): setting an affiliation primary demotes any other primary at that org, so promoting a
new primary contact atomically moves it off the previous one.

### `opportunities`
One row per gig / podcast spot. `current_status_id` is denormalized from the latest
`status_events` row and kept in sync by the API (never recomputed on read).

Money/lifecycle: `comp_type_id` (paid / pro_bono / trade), `fee_amount` + `currency`,
`payment_status_id`, `paid_on`. **Pro bono is first-class** — it still counts as a booking.

`closed_at` is denormalized; the predicate that drives it is in §4.
Indexes: `(user_id, closed_at)` — the board/History split, so it is on nearly every query —
plus `(user_id, current_status_id)`, `(user_id, event_date)`, `(organization_id)`.

### `opportunity_contacts`
Many-to-many with a `contact_role_id` (primary / introducer / backup / coordinator) and
`is_primary`. Covers the intro-chain case: an insider `introducer` plus the working `primary`.
`UNIQUE(opportunity_id, contact_id)`. At most one lead (`is_primary`) per gig is an application
invariant enforced on every write — setting a new lead demotes the previous one — mirroring the
one-primary-per-org rule on `contact_organizations`.

> **Two scoped meanings of "primary", labelled distinctly in the UI:**
> `contact_organizations.is_primary` = "Primary contact" (default contact for an org);
> `opportunity_contacts.is_primary` = "Lead on this gig". They are unrelated flags — do not
> conflate them in queries.

### `opportunity_notes`
Free-form **dated** notes (call outcomes, scheduling changes, prep) — distinct from the
`outreaches` touch journal and the `status_events` transition log. `occurred_at` is user-settable
(a note can record something that happened yesterday); `created_at` is not.

### `status_events`
Journal of pipeline transitions — one row per stage move, written on drag-and-drop and on close.
A row is inserted only when the new status differs from `opportunities.current_status_id`.
`note` carries the close reason for terminal transitions (Lost / Passed pre-booking,
Cancelled post-booking).

**`occurred_at` is the only trustworthy date the funnel has** (`updated_at` bumps on any edit), so
it is the source for every funnel ratio and every "entered stage X" count. Anything that needs to
be dated belongs here. Indexes: `(opportunity_id, occurred_at)`, `(user_id, status_id, occurred_at)`.

### `outreaches`
Append-only journal of **outbound** touches, logged against the *contact*, decoupled from pipeline
stage. `opportunity_id` is nullable (a touch need not belong to a gig).

> **Outbound only — this is load-bearing for metrics.** It is *out*reach: mail Donna **received**
> never creates a row here. Inbound remains fully visible in history via the union view in §4.
> Combined with `outreach_kinds.counts_toward_target`, this is what stops a four-message thread
> about parking logistics with an already-booked venue from scoring as four "outreaches" and
> turning the *outreaches/week* target into a measure of email volume.

`email_message_id` links an email touch to its message (`ON DELETE SET NULL`).
Indexes: `(user_id, occurred_at)`, `(contact_id, occurred_at)`,
`(user_id, outreach_kind_id, occurred_at)` — the last one serves the target rollups.

### `email_threads`
Thread identity, assigned **once at ingest** by the poller (which already holds the RFC 5322 header
chain) rather than re-derived per read. `subject_normalized` strips `Re:`/`Fwd:` prefixes and
collapses whitespace, and is the fallback grouping key when the `References` chain is broken by a
misbehaving client.

**`contact_id` and `opportunity_id` are both NULLABLE**, for three legitimate states:

| State | `contact_id` | `opportunity_id` |
|---|---|---|
| Unknown sender, dragged to `Import`, awaiting triage | NULL | NULL |
| Side-channel mail with a known contact, tied to no gig | set | NULL |
| Gig correspondence | set | set |

**Threads close explicitly** — `closed_at` is set by Donna ("no reply needed") or automatically
when the linked opportunity closes. Nothing infers that a sent message is owed a reply, so terminal
mail never nags. `last_read_at` drives the unread badge (`last_direction = 'in' AND (last_read_at
IS NULL OR last_read_at < last_message_at)`).
Indexes: `(user_id, closed_at, last_message_at)`, `(contact_id)`, `(opportunity_id)`.

### `email_messages`
One row per sent/received message. `references` is a reserved word in some tooling, so the column
is **`message_references`**.

**`UNIQUE(user_id, message_id)` is the idempotency key for the whole poller.** It is what makes a
re-dragged email, a redelivered IMAP fetch, or an overlapping poll structurally incapable of
double-inserting. `message_id` is `VARCHAR(255)` — comfortably above real-world Message-IDs and
within the InnoDB DYNAMIC 3072-byte index limit at utf8mb4.

`direction` is an `ENUM('out','in')` — see §5 for why this one is not a catalog.
`s3_key` points at the raw MIME (attachments are extracted from it, not stored separately).
`imap_folder` + `imap_uid` record provenance and support re-fetch.
Indexes: `(thread_id, COALESCE(sent_at, received_at))`, `(user_id, message_id)` UK,
`(in_reply_to)` — the reply-matching lookup.

### `follow_ups`
A future, actionable reminder — distinct from `outreaches` (past touches) and `opportunity_notes`
(dated commentary). `due_date` is an explicit calendar **DATE**, never a relative "in N days".

`completed_at NULL` **is** the pending/done state; there is no separate `status` column.
`DESIGN.md` §4 lists both, but two columns encoding one fact is a guaranteed drift bug — one of
them would eventually disagree with the other.

`CHECK (contact_id IS NOT NULL OR opportunity_id IS NOT NULL)` — a follow-up attached to nothing is
unreachable in the UI. Both are individually nullable (a gig-level reminder may name no person).

EventBridge Scheduler uses the deterministic schedule name **`followup-<id>`** (ported from
job-tracker), so create/update/delete need no state read-back.
Indexes: `(user_id, due_date, completed_at)` — the Dashboard's due list.

### `message_templates`
`user_id NULL` = shared template, editable in place (admin-gated under multi-user); **Duplicate**
writes a personal copy with `user_id` set. `body` holds merge fields (`[Name]`, …) resolved
client-side for the copy-to-clipboard DM flow.

Two orthogonal axes, deliberately separate columns (see §5): `message_template_kind_id` is the
template's **purpose/audience** (`message_template_kinds` — e.g. a power-partner intro vs. a cold
pitch), while `channel_id → outreach_channels` is **how it is sent** (`dm` / `email`). Both this
table and its `message_template_kinds` catalog arrive in `0005`, not `0001`.

### `targets`
`UNIQUE(user_id, target_type_id, cadence)` — the key the `PUT /targets` upsert
(`ON DUPLICATE KEY UPDATE`) depends on.

### `talks` / `materials`
The Guest Workshop menu. `materials.s3_key` is uploaded via presigned PUT; `talk_id` is nullable
so a general one-sheet can exist independent of a specific talk.

### `imap_folder_cursors`
Per-folder poll watermark, `UNIQUE(user_id, folder_name)`. Each poll fetches only UIDs above
`last_seen_uid`, which is what makes a 1-minute interval cheap — most polls touch zero messages.

> **`uid_validity` is not optional bookkeeping.** IMAP UIDs are only meaningful within a given
> `UIDVALIDITY`; if the server changes it (folder recreated, mailbox migrated), previously stored
> UIDs become meaningless and **the cursor must be reset** rather than trusted. Silently ignoring
> this is the classic IMAP poller bug — it either re-imports everything or skips mail forever.

---

## 3. Catalog vocabularies

| Table | `short_name` values | Extra columns |
|---|---|---|
| `organization_types` | retreat_venue, resort, yoga_studio, spa, womens_network, podcast, expo, corporate, other | — |
| `warmth_tiers` | cold, lukewarm, warm | — |
| `contact_roles` | primary, introducer, coordinator, backup | — |
| `opportunity_formats` | workshop, keynote, podcast_spot, expo_table, panel, other | — |
| `opportunity_statuses` | researching, outreach_sent, in_conversation, pitched, booked, delivered, cancelled, lost *(nurture retired in `0004`)* | `is_terminal`, `sort_order` |
| `comp_types` | paid, pro_bono, trade | — |
| `payment_statuses` | unbilled, invoiced, partial, paid, n_a | `is_settled` |
| `outreach_kinds` | initial, follow_up, correspondence | **`counts_toward_target`** |
| `outreach_channels` | email, dm, call, in_person, text | — |
| `message_template_kinds` | *purpose vocabulary — values defined in slice 4 (§5); seeded in `0005`, not `0001`* | — |
| `target_types` | venues_researched, outreaches, pitches, bookings | — |

### `opportunity_statuses` — `sort_order` drives the funnel

| short_name | description | is_terminal | sort_order |
|---|---|---|---|
| researching | Researching | F | 10 |
| outreach_sent | Outreach Sent | F | 20 |
| in_conversation | In Conversation | F | 30 |
| pitched | Pitched | F | 40 |
| booked | Booked | F | 50 |
| delivered | Delivered | **T** | 60 |
| cancelled | Cancelled | **T** | 80 |
| lost | Lost / Passed | **T** | 90 |

> `nurture` (was 70) was **retired in `0004_remove_nurture.sql`** — keeping a relationship warm is a
> property of the contact/venue (warmth tier, power-partner) and of follow-ups, not a stage of a past
> gig. The acquisition pipeline now ends cleanly at Delivered. (Its `sort_order` gap is intentional.)

The four **funnel ratio stages** are `outreach_sent → in_conversation → pitched → booked` (10–50),
counted **reached-or-beyond** over `status_events.occurred_at`, so a gig that jumped straight to
Pitched still counts toward Outreach Sent.

Two notes on the ordering, because each is a trap:

- **`cancelled` still counts as a booking** for funnel purposes (it was booked, then fell through).
  The Dashboard funnel shows it as the leak between Booked and Delivered. Do **not** exclude it
  from the booked count.
- **`is_terminal` is genuinely consumed here**, unlike legacy-tracker where the equivalent flag is a
  documented dead column. It gates the `closed_at` predicate in §4 — do not assume it is decorative.

### `outreach_kinds.counts_toward_target`

| short_name | counts_toward_target | set when |
|---|---|---|
| initial | ✓ | first outbound touch to that contact |
| follow_up | ✓ | subsequent prospecting touch |
| correspondence | ✗ | logistics / admin on an existing conversation |

The composer **infers** the kind (`initial` if no prior outbound touch to the contact, else
`correspondence`) and shows it as an editable chip, so a genuine re-pitch to a cold contact can be
corrected to `follow_up`. Metric SQL joins this flag — it must never hardcode `short_name` values.

### `payment_statuses.is_settled`

`paid` and `n_a` are settled; `unbilled`, `invoiced`, `partial` are not. Pro bono and trade
opportunities are created with `n_a`. This flag exists so the §4 close predicate is catalog-driven
rather than a hardcoded `IN (...)` list.

---

## 4. Stored vs computed

**Stored / denormalized (API keeps in sync — never recomputed on read):**
`opportunities.current_status_id`, `opportunities.closed_at`, `email_threads.last_message_at`,
`email_threads.last_direction`, `imap_folder_cursors.last_seen_uid`.

### `closed_at` — the one predicate worth getting right

An opportunity is **closed** when it has reached a terminal status *and* money is settled:

```
closed  ⇔  (delivered AND payment_statuses.is_settled)
        ∨   cancelled
        ∨   lost
```

The payment gate applies **only to `delivered`**. This is deliberate: a delivered-but-unpaid gig
**stays on the active board** so it isn't lost before Donna collects. Cancelled and Lost close
immediately — there is nothing to collect.

`closed_at` is written by the API whenever a status or payment change makes the predicate true, and
cleared if it becomes false again (e.g. a payment status corrected back to `invoiced`). The board
filters `closed_at IS NULL`; History filters `closed_at IS NOT NULL`; the "Show closed" toggle
relaxes the board filter to a recent window.

> It is stored rather than a MySQL generated column because the predicate depends on
> `opportunity_statuses.is_terminal` and `payment_statuses.is_settled` — **columns in other
> tables**, which generated columns cannot reference.

### Computed on the fly (no backing column)

- **Contact timeline** — a read-time `UNION ALL` over `outreaches`, `email_messages`,
  `opportunity_notes`, and `status_events`, ordered by their respective timestamps. This view is
  what allows `outreaches` to stay outbound-only without losing unified history on the contact page.
- **Funnel ratios** — reached-or-beyond counts over `status_events` joined to
  `opportunity_statuses.sort_order`, reduced in SQL with a `CASE` + `GROUP BY`, not a Python pass.
- **Target actuals** — counts over `outreaches` (filtered by `counts_toward_target`),
  `organizations` (research-ready, below), `status_events` (pitched / booked), bucketed by the
  target's cadence in the **user's** timezone.
- **Research-readiness** — an org is "outreach-ready" when all three Kindling columns are non-empty
  *and* it has ≥1 affiliated contact. This is the quality bar for the *venues researched* target,
  so the target counts **ready** orgs, not merely created rows.
- **Thread status** — derived from `last_direction`, `last_message_at`, `last_read_at`, `closed_at`
  (see `email_threads`). There is no status column.
- **Money rollups** — Booked / Received / Outstanding and the pro-bono count, summed over
  `opportunities` by `comp_type_id` / `payment_status_id`.

**Config scalars (not aggregates):** `targets.goal_count`, `users.timezone`, catalog `sort_order`
and boolean flags.

---

## 5. Deliberate deviations

Two places where this schema departs from a convention or from `DESIGN.md`, with rationale:

1. **`ENUM` instead of a catalog — twice.** `targets.cadence` (`weekly|monthly|quarterly`) and
   `email_messages.direction` / `email_threads.last_direction` (`out|in`). A two- or three-row
   catalog with no extra columns and no prospect of user extension is overkill; job-tracker made
   the same call on `cadence` for the same reason. Every other vocabulary is a catalog.
2. **`follow_ups` has no `status` column** — `completed_at IS NULL` is the pending state.
   `DESIGN.md` §4 lists both `status` and `completed_at`; storing one fact in two columns
   guarantees they eventually disagree.

A resolved modelling decision worth recording: `DESIGN.md` §4 calls `message_templates.channel`
a *channel*, but its proposed values `dm / email / power_partner` conflated two axes — `dm`/`email`
are channels (*how* a message is sent), while `power_partner` is an **audience**, already modelled
as `contact_organizations.is_power_partner`. These are split: a template carries a `channel_id →
outreach_channels` (how it is sent) **and** a `message_template_kind_id → message_template_kinds`,
a *purpose* vocabulary (power-partner intro, cold pitch, …). Exact purpose values are settled in
slice 4, where `message_templates` and the `message_template_kinds` catalog are both created
(`0005`); `message_template_kinds` is therefore the one catalog **not** seeded in `0001`.
`outreach_channels` remains the genuine channel vocabulary shared with `outreaches`.

---

## 6. Migration plan

Forward-only, one file per vertical slice from `DESIGN.md` §6, so a slice is deployable on its own:

> **`schema_migrations` is created by the runner, not by a migration.** Putting it in `0001` is
> circular — the runner must query that table to decide whether `0001` has already run. The runner
> bootstraps it with `CREATE TABLE IF NOT EXISTS` before consulting it:
>
> ```sql
> CREATE TABLE IF NOT EXISTS schema_migrations (
>   version          VARCHAR(20)  NOT NULL,
>   name             VARCHAR(255) NOT NULL,
>   checksum         CHAR(64)     NOT NULL,
>   status           ENUM('running','applied','failed') NOT NULL,
>   statements_total INT UNSIGNED NOT NULL,
>   statements_ok    INT UNSIGNED NOT NULL DEFAULT 0,
>   started_at       TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
>   finished_at      TIMESTAMP(3) NULL,
>   execution_ms     INT UNSIGNED NULL,
>   error            TEXT NULL,
>   PRIMARY KEY (version)
> ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
> ```
>
> `checksum` (sha256 over the file, CRLF normalised to LF) is what detects a migration **edited
> after it was applied** — otherwise schema drift is completely silent. `status` plus the statement
> counters make a partially-applied file diagnosable: **MySQL 8 gives atomic DDL per statement, so
> no half-created table, but a half-applied *file* is possible and there is no rollback.** Wrapping
> a file in `BEGIN`/`COMMIT` is worse than useless — each DDL statement issues an implicit commit.
> Recovery is forward-only, which is why **every migration statement must be idempotent**
> (`CREATE TABLE IF NOT EXISTS`, `INSERT … ON DUPLICATE KEY UPDATE`): fix the SQL,
> `DELETE FROM schema_migrations WHERE version='NNNN'`, redeploy. A `failed` row is a deliberate
> hard stop requiring a human — auto-retrying from statement 1 against a partially-mutated schema is
> how databases get corrupted.

| Migration | Contents | Slice |
|---|---|---|
| `0001_initial.sql` | `users`, all catalog tables + seed rows — **except `message_template_kinds`**, deferred to `0005` (**not** `schema_migrations`) | 1 |
| `0002_orgs_contacts.sql` | `organizations`, `contacts`, `contact_organizations` | 2 |
| `0003_pipeline.sql` | `talks`, `opportunities`, `opportunity_contacts`, `opportunity_notes`, `status_events` | 3 |
| `0004_remove_nurture.sql` | retires the `nurture` status (catalog soft-delete) | 3 (follow-up) |
| `0005_outreach.sql` | `outreaches`, `message_templates` (+ `channel_id → outreach_channels`), the `message_template_kinds` purpose catalog + seed of the strategy-doc templates | 4 |
| `0006_targets.sql` | `targets` | 5 |
| `0007_target_labels.sql` | updates `target_types` display labels to the approved mockup wording (no schema change) | UX reconciliation |
| `0008_email.sql` | `email_threads`, `email_messages`, `imap_folder_cursors` | 6a |
| `0009_followups.sql` | `follow_ups` | 7 |
| `0010_materials.sql` | `materials` (`talks` shipped early in `0003`) | 6a / Talks |

Catalog seed rows ship in `0001` even for tables whose entity arrives later — seeding is idempotent
(`INSERT … ON DUPLICATE KEY UPDATE` on `short_name`) and keeps vocabulary changes in one place. The
sole exception is `message_template_kinds`: its shape (a *purpose* axis, §5) is unresolved until
`message_templates` is designed, so it is seeded in `0005` alongside that table rather than in `0001`.