# Speaker Tracker — Design

> Working design doc for a bespoke, behind-auth CRM that funnels **live speaking and
> podcast-guest gigs** for Donna King (360 Balanced Living). Not for public consumption.
> Status: **pre-implementation** — architecture chosen, UI mocked, no code written yet.

## 1. Purpose & scope

A lightweight, single-user (Donna; Brian is the developer/admin) pipeline CRM for
**getting Donna booked on stages and podcasts**. The workflow, drawn from her Strike-A-Match
strategy session (`docs/strategy-session-doc.pdf`):

> build a target list of venues / orgs / podcasts → find a contact per venue →
> warm intro or templated outreach → follow up on cadence → book → deliver → review the funnel.

### Boundary with legacy-tracker (important)

This app is **upstream, gig-acquisition only**. The sibling app **legacy-tracker**
(`~/360-balanced-living/legacy-tracker`) owns the *listener-conversion* side of Donna's
funnel — Legacy Spark Chats, discovery calls, podcast/guide-download/Legacy-Lounge lead
sources. **Do not pull those concepts into speaker-tracker.** A speaker-tracker opportunity
ends at `Delivered / Nurture`; any inbound interest a delivered gig produces is handed off to
legacy-tracker, not tracked here.

## 2. Features

**Core CRM**
1. **Venues / Organizations** — retreat venues, resorts, yoga studios, spas, women's networks
   (PWN Hawaii, WBEC-West, HLTA), podcasts, expos. Type catalog, location, links, notes.
2. **Contacts** — event coordinator / wellness director / retreat manager / podcast host.
   Power-partner flag; warmth tier; warm-intro / mutual-connection; how you know them.
3. **Gig pipeline (kanban)** — one card per speaking/podcast opportunity:
   `Researching → Outreach Sent → In Conversation → Pitched → Booked → Delivered → Nurture`.
   Card holds talk offered, event date, format (workshop / keynote / podcast spot / expo table),
   outcome. Stage moves journaled (drag-and-drop).
4. **Outreach journal** — append-only touch log per contact (email, DM, call, in-person),
   decoupled from pipeline stage.
5. **Message templates** — the three from the strategy doc (DM, formal email, power-partner DM);
   shared-seed + clone-on-edit.

**Email**
6. **Rich composer with attachments** — full formatting, attach one-sheet / speaking menu,
   send *as Donna via Gmail* (deliverability + Sent-folder continuity), auto-logged as an
   outreach touch.
7. **Follow-up reminders** — no-reply-in-N-days nudges per opportunity.

**Goals & measurement** (the strategy doc's weakest-scored gap — the point of the app)
8. **Targets** — per-cadence goals: new venues researched/month (doc says 3–5), outreaches/week,
   pitches/month, bookings/quarter.
9. **Dashboard** — actual-vs-target tiles; funnel ratios (outreach → conversation → pitched →
   booked); stale-opportunity list; upcoming schedule / quarterly review.

**Assets**
10. **Talks & materials** — the three talks (title, length, one-liner = the Guest Workshop menu)
    and files (one-sheet PDFs) in S3, attachable to outreach.

**Compensation, payment & lifecycle**
11. **Compensation & payment** — each opportunity records comp type (Paid / Pro bono / Trade),
    fee + currency, and payment status (Unbilled → Invoiced → Partial → Paid) + paid-on date.
    Pro bono is first-class (expected for visibility-building) and still counts as a booking.
    Pipeline cards show a money / "Pro bono" chip + a payment chip.
12. **Full lifecycle** — terminal statuses **Delivered**, **Cancelled** (was booked then fell
    through — still counts as booked for tracking), and **Lost / Passed** (didn't convert before
    booking). A gig is **closed** when Delivered + payment settled, or Cancelled, or Lost.
13. **History** — closed gigs leave the kanban and live in a filterable History table (outcome,
    date, venue, comp, amount, paid) with a History detail record. Money rolls up on the Dashboard
    (Booked / Received / Outstanding, count + value, + pro-bono count); overdue / awaiting payments
    surface as rows in Needs attention. The board keeps a "Show closed" toggle to peek at recently
    closed cards; a delivered-but-unpaid gig stays on the board until settled so it isn't lost
    before you collect.

Styling: straight business app, "not ugly," **no gamification** (no confetti/streaks — that's
legacy-tracker's look). **Light theme by default** (Donna dislikes dark themes). Branded with the
360 Balanced Living palette — see the Branding section.

## 3. Architecture

Take **legacy-tracker as the structural baseline** (the newer, cleaner fork) and **port
job-tracker's email / scheduler / targets subsystems** into it.

```
Route53 → CloudFront (one distribution)
            ├─ default behavior → S3 (private, OAC) — Vite/React SPA
            └─ /api/* → API Gateway HTTP API (JWT authorizer in prod)
                          → Python 3.12 Lambdas (arm64, outside VPC)
                              → RDS MySQL 8 via IAM auth + TLS (new `speakertracker` schema)
                              → S3 (attachments, one-sheets — presigned PUT)
                              → Gmail API (send-as-Donna) / SES (reminder notifications)
              EventBridge Scheduler → followup_notify Lambda → SES
Cognito (Hosted UI) ── react-oidc-context in the SPA
```

**Key decisions**
- **IaC: AWS CDK (TypeScript)**, not SAM. legacy-tracker already migrated off SAM; copy
  `infra/cdk/` with its `ROUTES` table, prod/sandbox envs parameterized by `authMode`, and the
  in-deploy migration Trigger.
- **Same-origin CloudFront** (`/api/*` behavior + prefix-strip function) → zero CORS, no
  env-specific API URLs.
- **Data store: MySQL**, not Postgres. Greenfield Postgres would be defensible, but reuse the
  running `jobtracker-db` instance (a new schema costs $0), the battle-tested `common/db.py`
  RDS-IAM-auth code, and the migration runner. Accepted tradeoffs (already accepted twice in the
  siblings): public RDS endpoint gated by IAM auth + TLS; master secret lives in the job-tracker
  repo; Lambdas run outside any VPC (no NAT/bastion; cold-start TLS handshake 2–6s).
- **Frontend: Vite + React 18 + TypeScript + Mantine + TanStack Query + dnd-kit**, keeping
  `react-oidc-context` + a `useApi()` bearer-token wrapper and the `X-User-Timezone` header
  (Kauaʻi is UTC-10 → "today" rollover matters). Adopt Mantine over job-tracker's react-bootstrap
  and legacy-tracker's hand-rolled Tailwind; Mantine gives tables (`mantine-react-table`), forms,
  date pickers, notifications, and the Tiptap rich-text editor for the email composer. TanStack
  Query fixes the hand-rolled-`fetch` wart both siblings flagged (needed for optimistic kanban
  drag mutations).
- **Email: Gmail API for outreach** (port job-tracker's Gmail OAuth/compose cluster); **SES only
  for system notifications** (follow-up reminders).
- **Conventions from the siblings:** forward-only SQL migrations; `id` / `created_at` /
  `updated_at` / `deleted_at` on every entity table; **catalog tables over ENUMs**; `status_events`
  journal + denormalized `current_status_id`; server-owned funnel ordering.

Repo layout mirrors siblings: `backend/src/{handlers,common,migrations}`, `infra/cdk`,
`frontend/src/{pages,api,components}`, `docs/slices/NN-*.md` per feature.

## 4. Data model

Core entities (user-scoped via `user_id`; every entity table has `id` /
`created_at` / `updated_at` / `deleted_at`; lookup values in catalog tables, not ENUMs):

- **`organizations`** — venues/orgs/podcasts. Type (catalog), location, links, source, notes,
  plus the three **Kindling** research columns (§5).
- **`contacts`** — the *person*. `warmth_tier`, `is_power_partner`, `source`, notes.
  **No `organization_id`** — see the join below. Power-partner is a person-level flag,
  independent of any org.
- **`contact_organizations`** — **many-to-many** contact ↔ org. Fields: `contact_id`,
  `organization_id`, `title` (their role *at that org* — "Events Chair" at PWN, "Member" at BNI),
  `is_primary` (the go-to contact for that org). `UNIQUE(contact_id, organization_id)`.
  Rationale: in a small community (Kauaʻi) people wear multiple hats — one person is a contact
  for several venues.
- **`opportunities`** — one per gig/podcast spot. Talk offered, event date, format, outcome;
  `current_status_id` (denormalized) + `status_events` journal driving the funnel. Money/lifecycle
  fields: `comp_type` (paid / pro_bono / trade), `fee_amount` + `currency`, `payment_status`
  (unbilled / invoiced / partial / paid / n_a), `paid_on`. **Closed** = a terminal status reached
  *and* settled: Delivered + payment resolved (paid, or pro bono / n/a), or Cancelled, or Lost —
  closed opportunities leave the active board and appear in History.
- **`opportunity_contacts`** — **many-to-many** opportunity ↔ contact with a `role`
  (primary / introducer / backup / coordinator) and `is_primary` (lead on this gig). Covers the
  intro-chain case (insider `introducer` + working `primary`), multiple people per event, and
  backup coverage.
- **`opportunity_notes`** — free-form **dated notes** on an opportunity (call outcomes, scheduling
  changes, prep), distinct from the `outreaches` touch journal and the `status_events` transition
  log. Editing an opportunity happens on its detail page (Edit re-opens the create modal
  pre-filled). **Closing** it (a close × on any card, or the detail page) writes a terminal
  `status_event` + a note capturing the reason — **Lost / Passed** pre-booking, **Cancelled**
  post-booking. (Advancing to Delivered is a normal drag to the Delivered column — not a button.)
- **`outreaches`** — append-only touch journal (channel, note, optional `opportunity_id`),
  decoupled from stage. Logged against the *contact*.
- **`message_templates`** — nullable `user_id` = shared seed (immutable); clone-on-edit.
- **`targets`** — per-user, per-type, cadence (weekly/monthly/quarterly), `goal_count`.
- **`talks`** + **materials** (S3 files) — the Guest Workshop menu.
- Catalog tables: `organization_types`, `contact_roles`, `opportunity_statuses`
  (`is_terminal`, `sort_order`; includes terminal **Delivered**, **Cancelled**, **Lost/Passed**),
  `comp_types`, `payment_statuses`, `warmth_tiers`, `target_types`, etc.

**Two scoped meanings of "primary" (labeled distinctly in the UI):**
`contact_organizations.is_primary` = default contact for an org ("Primary contact");
`opportunity_contacts.is_primary` = lead contact for a gig ("Lead on this gig").

**Add-contact must dedupe:** adding an existing person to a second venue creates a *new
`contact_organizations` affiliation*, not a duplicate contact. The add-contact flow needs a
"this person may already exist" search step.

## 5. Kindling research fields (strategy doc, page 8)

Every organization gets three structured research columns (not a free-form notes blob), gathered
once during research and surfaced at outreach time:

- `what_it_is` — factual description
- `why_it_fits` — audience/fit rationale
- `how_to_approach` — the play (attend first, warm intro, who to ask for)

Uses: a "Research" panel on the org detail page; a **research-readiness** indicator (all three
filled + ≥1 contact = "outreach-ready", which is the quality bar for the *new venues researched*
target); surfaced in the email/DM composer side panel while drafting; first line of `why_it_fits`
shown as a scan column in the venue list. Fields stay **org-level**; a gig-specific angle lives on
the opportunity card, seeded from `how_to_approach`.

## 6. Build order (vertical slices)

1. Infra skeleton + auth + health
2. Orgs + contacts CRUD (incl. `contact_organizations` many-to-many + dedupe)
3. Pipeline board + status journal
4. Outreach log + templates
5. Targets + dashboard
6. Gmail composer + attachments
7. Follow-up reminders

## 7. Open decisions

- **Shared `jobtracker-db` instance** (recommended) vs. a dedicated RDS instance.
- **Single-user** (Donna only, Brian admin) vs. real multi-user — affects whether Gmail OAuth is
  a single stored credential or per-user.
- Venue detail Contacts card: show affiliation info (title + primary-contact flag) vs. the current
  mock which shows opportunity roles (Primary/Introducer) there.

## 8. Mockup

Clickable HTML/CSS mockup (light theme, brand palette, real strategy-doc sample data) lives at
`samples/speaker-tracker-mockup.html` — see `samples/README.md`. It covers all pages plus
Log-outreach / Add-venue / Add-contact / New-opportunity (comp + payment) / Close-opportunity
modals, the multi-org contact detail page, the **Opportunity detail** page (fields, linked
contacts, **dated notes**, lifecycle) reached by clicking a pipeline card, contact-role chips on
opportunities, money/payment pipeline cards with a per-card close × (→ Close-opportunity) and a
Show-closed toggle, the Dashboard money card + funnel (Booked → Delivered with the Cancelled leak),
and the **History** list + History detail pages. The pipeline expands to full browser width (Donna
works on a 36" monitor).

## 9. Branding

Palette from `~/360-balanced-living/ghl/guides-incoming-resources/Website_Update_Implementation_Guide.docx.pdf`
(which retires the old green/teal from a previous business):

| Deep navy `#1F3B4D` | Terracotta `#C2483A` | Gold `#D9A02C` | Warm cream `#FBF8F2` | Warm gray `#555555` |
|---|---|---|---|---|
| nav rail + headings | primary buttons, emphasis | accents, power-partner ★, warmth | page background | body text |

Logo: `360-balanced-living-logo-NAV-darkbg.svg` (dark-bg lockup) in the navy sidebar; other
variants (square, inline, white-bg) in `~/360-balanced-living/ghl/assets/images/logos/` — copy the
needed files into this project when scaffolding. **Fonts: sans-serif for the app.** The guide's
Playfair Display / Lato spec is for Donna's public website, not this internal tool — here color
carries the brand, not type. Light theme is the default; a dark theme exists but is opt-in.

## 9. Baselines surveyed

- **`~/360-balanced-living/legacy-tracker`** — CDK(TS), same-origin CloudFront, Python Lambdas +
  RDS IAM auth, Vite/React + Tailwind, `react-oidc-context`. Structural baseline. (`DESIGN.md`
  there is stale — its live docs are `docs/ARCHITECTURE.md`, `docs/DATABASE.md`.)
- **`~/projects/job-tracker`** — SAM + SSM, Python Lambdas + RDS IAM auth (MySQL), react-bootstrap.
  Source for the Gmail composer, EventBridge-Scheduler+SES reminders, and `targets` subsystems.