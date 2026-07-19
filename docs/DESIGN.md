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
   decoupled from pipeline stage. DMs are *logged*, not sent from the app (no practical
   Instagram/LinkedIn send API): pick a template, it merges + copies to the clipboard, you paste it
   in the DM, then log the touch. **Email is the only in-app send channel** (see §3 Email).
5. **Message templates** — the three from the strategy doc (DM, formal email, power-partner DM).
   Selectable when logging outreach or composing email; **merge fields** (e.g. `[Name]`) fill from
   the contact, and a **Copy to clipboard** action supports the DM paste flow (à la Legacy Tracker).
   Shared templates are **editable in place**; **Duplicate** keeps a personal variant. (Under
   multi-user, editing shared templates becomes an admin role; single-user edits in place.)

**Email**
6. **Rich composer with attachments** — full formatting, attach one-sheet / speaking menu,
   send *as Donna* from her business address, with the sent copy kept in her Sent folder for
   continuity; logged as an **outbound** touch whose *kind* is inferred (`initial` if it is the
   first outbound touch to that contact, otherwise `correspondence`) and shown as an **editable
   chip** in the composer — only prospecting kinds count toward targets (see §4 `outreaches`).
   Replies are threaded back to the opportunity (see §3 Email — WorkMail / SES / IMAP, not Gmail).
   **Email history is readable:** an **Emails inbox** (thread list surfacing open threads awaiting
   a reply and unread inbound mail) and a **thread view** (full conversation + attachments) with an
   **inline reply** box; threads also surface on the contact and its linked opportunity. A thread
   is **explicitly closed** ("no reply needed") — nothing infers that a sent message is owed an
   answer, so terminal mail never nags.
7. **Follow-ups** — schedule a **calendar-dated** follow-up with a **free-form note** on a contact
   or opportunity, standalone or as a rider when logging outreach / composing email; due reminders
   surface on the Dashboard and by email, and can be marked done. (Not a relative "in N days"
   selector — an explicit date.) The composer/outreach rider is **opt-in, default off** — sending
   an email never silently schedules a follow-up, or the Dashboard fills with noise.

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
job-tracker's scheduler + targets subsystems** into it. (Email is *not* ported from job-tracker —
that app uses Gmail; this one uses WorkMail / SES / IMAP — see the Email decision below.)

```
Route53 → CloudFront (one distribution)
            ├─ default behavior → S3 (private, OAC) — Vite/React SPA
            └─ /api/* → API Gateway HTTP API (JWT authorizer in prod)
                          → ONE Python 3.12 API Lambda (arm64, outside VPC)
                            (Powertools resolver; handlers/ = Router modules)
                              → RDS MySQL 8 via IAM auth + TLS (new `speakertracker` schema)
                              → S3 (attachments, one-sheets — presigned PUT)
                              → SES SendRawEmail (send-as-Donna) + IMAP (WorkMail: Sent-append)
              EventBridge Scheduler → followup_notify → SES
                                    · imap_poll → thread replies + drop-folder imports
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
- **Auth UX & session — no splash gate.** Both siblings gate the whole app behind a full-screen
  "Login with Cognito" interstitial. This app instead **lands on the normal page** (branded shell,
  nav rail, logo) with a **Sign In** link in the header, like an ordinary web app; the content area
  prompts to sign in rather than a modal blocking the route. A deep link followed while signed out
  is remembered and restored after login. **Session persists:** Cognito
  `refreshTokenValidity: Duration.days(90)`, `automaticSilentRenew` in `react-oidc-context` rolling
  the ≤24h access token over via the **refresh-token grant** (no hidden iframe), tokens in
  `localStorage` so a browser restart stays signed in. Donna signs in about quarterly.
  Accepted tradeoff: a 90-day token at rest grants standing access to anyone at the machine —
  acceptable because she works from a **fixed office desktop**, not a travelling laptop. Revisit if
  this ever goes multi-user or mobile. Also fix two gaps inherited from legacy-tracker: the API
  client must **handle 401** (trigger re-auth rather than returning the raw `Response` to callers),
  and `UserNotFoundError` must map to **404, not 500**.
- **Email: AWS WorkMail + SES + IMAP — not Gmail.** Donna's business mailbox is WorkMail, which she
  reads via **Outlook over IMAP**. The app operates on the same WorkMail mailbox server-side, so its
  changes and her Outlook stay in sync.
  - **Account / region topology — the app and the mailbox live in different accounts.**

    | Piece | Account | Region |
    |---|---|---|
    | App (Lambdas, RDS, S3, Cognito, CloudFront) | **381492047863** (Brian) | us-west-2 |
    | ACM cert for the SPA | 381492047863 | us-east-1 (CloudFront requirement) |
    | Route53 zone `360balancedliving.com` | 381492047863 | global |
    | **SES sending identity** `360balancedliving.com` | **381492047863** | **us-east-1** |
    | **WorkMail mailbox** (`m-aa419e28e9c44881a91c711910d9b1b5`) | **730335513412** (Donna) | us-east-1 |

  - **No cross-account IAM is required**, which is the whole reason this topology works:
    - **Sending** uses the `360balancedliving.com` **domain identity already verified in Brian's
      account** (us-east-1, DKIM `SUCCESS`, signing enabled). Domain verification covers every
      address beneath it, so `From: donna@360balancedliving.com` needs no per-address identity and
      no role assumption into Donna's account. *Rejected alternative:* cross-account sending
      authorization on the identity in her account — more moving parts, and it is unclear whether
      production access/quota is evaluated against the sending or the identity-owning account.
    - **IMAP is username/password, not IAM**, so reaching her WorkMail mailbox from Brian's account
      is not a cross-account problem at all. Endpoint is **us-east-1**.
  - **Send** via SES `SendRawEmail` against the **us-east-1** endpoint (IAM-authed — no email
    password needed to send). The app crafts the raw MIME: `From: donna@360balancedliving.com`,
    attachments from S3, and a stable `Message-ID` it records; on replies it sets `In-Reply-To` +
    `References`. SES signs with that account's domain DKIM. Domain auth is already in place:
    SPF includes `amazonses.com`, DMARC is `p=none` (nothing quarantines while alignment settles).
    - **SES production access: granted** (us-east-1, 2026-07-18) — 50,000/day, 14 msg/s,
      enforcement `HEALTHY`. Production access is **per-region**; this covers us-east-1, where both
      the identity and the WorkMail mailbox live. The quota is irrelevant at this volume — what
      mattered was escaping the sandbox's verified-recipients-only restriction.
    - *Optional later:* a custom **MAIL FROM** subdomain for stricter DMARC alignment.
      `bounce.360balancedliving.com` already exists in DNS with the right SPF and feedback MX, but
      is **not** configured on the identity. Not a blocker at `p=none`.
  - **Sent-folder continuity:** IMAP-`APPEND` the sent MIME to her Sent folder (discovered via the
    IMAP `\Sent` SPECIAL-USE flag, not a hard-coded name) so it appears in Outlook.
  - **Reply threading:** an IMAP **poller Lambda** (EventBridge schedule) reads new mail and matches
    each reply's `In-Reply-To` / `References` to a stored outbound `Message-ID` → links it to the
    opportunity + logs an inbound touch. Fallback: `From` + normalized subject + time window.
    Polling the Sent folder also captures mail Donna sends straight from Outlook.
  - **Scope — never the whole mailbox.** The app reads exactly **two consented surfaces**:
    (a) correspondence with a **tracked contact** (mail whose `From`/`To` matches a contact address,
    or that matches a stored outbound `Message-ID`), and (b) messages Donna **explicitly dragged**
    into the import folder below. Personal / unrelated mail is never ingested. (Outlook is a peer
    IMAP client on the same mailbox, not something the app reads *through*.)
  - **Inbound-first threads.** A venue that emails Donna first has a standard `Message-ID` like any
    message; the poller reads it via IMAP, creates an `email_messages` row (`direction: in`), and
    associates it by `From`-address. Donna's app reply then threads via `In-Reply-To` → that stored
    `Message-ID`. Threading uses **RFC 5322 headers only** (`Message-ID` / `In-Reply-To` /
    `References`); Microsoft's proprietary `Thread-Index` isn't needed (external senders don't set
    it).
  - **Import of an unknown sender — the IMAP drop folder.** Mail from someone who isn't a tracked
    contact is out of scope for the poller, so it needs an explicit hand-off. In Outlook, Donna
    **drags the email into `Speaker Tracker/Import`**; the poller watches that folder, stores the
    message with `contact_id` / `opportunity_id` **NULL**, moves it to `Speaker Tracker/Processed`
    so it is never re-ingested (idempotency keyed on `Message-ID`), and badges the app — *"1 email
    awaiting import."* Clicking it opens **Add Contact pre-filled from the `From` header** (display
    name, address, and the sender domain used to suggest an existing org or seed a new one), routed
    through the §4 dedupe search so a second mail from a known person offers *attach* rather than
    *create*. On save the contact + thread link up and the full thread appears on the contact.
    - **Why a folder move, not forward-to-import:** an IMAP move transfers the original RFC822
      message byte-for-byte, so the **`Message-ID` is preserved** and replies thread correctly at
      the venue's end. Forwarding rewrites the `Message-ID` and wraps the original, forcing
      quoted-text or `.eml`-attachment parsing and mis-threading the reply.
    - **Folders are auto-created, never typed.** On first connect — and defensively on every poll —
      the poller `LIST`s and, if missing, `CREATE`s **and `SUBSCRIBE`s** `Speaker Tracker/Import`
      and `Speaker Tracker/Processed`. (`SUBSCRIBE` matters: an unsubscribed folder may not appear
      in Outlook's tree, which looks identical to the folder never being created.) Donna never
      types the name, so it cannot be misnamed; deleting it is self-healing. The **`\Sent`** folder
      is still *discovered* via the SPECIAL-USE flag — it is WorkMail's, not ours.
    - The poller's folder set is therefore **configuration, not a constant**: `INBOX`, `\Sent`,
      `Import`, `Processed`.
  - **Mailbox facts.** Address **`donna.king@360balancedliving.com`**; WorkMail org alias
    `360-balanced-living` (`m-aa419e28e9c44881a91c711910d9b1b5`), us-east-1; webmail at
    `https://webmail.mail.us-east-1.awsapps.com/workmail/?organization=360-balanced-living`.
    IMAP endpoint **`imap.mail.us-east-1.awsapps.com:993`** over SSL — confirm at first connect
    rather than trusting the documented pattern. **No MFA is configured**, so plain
    username/password IMAP authenticates; no app-specific-password mechanism is needed.
  - **Credentials:** WorkMail IMAP creds in Secrets Manager **in Brian's account** (single-user →
    one credential). This is the **first runtime secret read** in the family — both siblings resolve
    all config to env vars at deploy time — so `common/secrets.py` (cached fetch) is new code, not a
    port.
    - **CDK creates the `Secret` resource; the value is set out of band.** CDK owns the resource
      (tags, `removalPolicy: RETAIN`, `grantRead` to the poller) but never sees the password:
      ```
      aws secretsmanager put-secret-value --secret-id speakertracker/imap \
        --secret-string '{"username":"donna.king@360balancedliving.com","password":"..."}' \
        --profile brian-admin --region us-west-2
      ```
    - *Rejected:* a gitignored config file read at synth time. That forces
      `SecretValue.unsafePlainText()`, which bakes the password into the synthesized template — so
      it lands unencrypted in `cdk.out/`, in the CDK staging bucket, and in CloudFormation, readable
      by anyone with `cloudformation:GetTemplate`. The gitignore protects the source file and none
      of the four places the value actually ends up. Rotation also becomes a stack deploy instead of
      one CLI call.
  - **IMAP auth failure is an alarm condition, never a swallowed error.** Brian is the sole admin of
    Donna's account, which makes accidental breakage *more* likely, not less: he may rotate the
    mailbox password for an unrelated reason with nothing connecting that act to "inbound threading
    stopped." The failure is otherwise invisible — the poller keeps running on schedule, finds
    nothing, and replies silently stop appearing.
  - **Inbound mail flow (resolved).** The apex `MX` points at `inbound-smtp.us-east-1.amazonaws.com`,
    which is the **standard MX for a WorkMail-managed domain** — WorkMail runs on SES infrastructure.
    Brian's account has **no SES receipt rule sets at all** in us-east-1, so nothing of his touches
    her inbound mail. Worth a one-off confirm that her account's active rule set is WorkMail's
    default and nothing extra copies mail to S3 or a Lambda — this matters because the
    never-the-whole-mailbox guarantee must hold *below* the app, not only inside it.
  - **SES** also powers system notifications (follow-up reminders) — unchanged. **job-tracker's
    Gmail OAuth/compose cluster is not reusable here.**
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
- **`outreaches`** — append-only journal of **outbound** touches only (channel, note, `kind`,
  optional `opportunity_id`), decoupled from stage. Logged against the *contact*. Email touches link
  to an `email_messages` row.
  - **Outbound only — this is load-bearing for metrics.** It is *out*reach: mail Donna **received**
    never creates a row here. Inbound is visible in history via `email_messages` (see the timeline
    note below), so nothing is lost from the record while the journal that feeds targets stays
    clean.
  - **`outreach_kinds` carries `counts_toward_target`** (catalog-driven, so metric SQL never
    hardcodes kind names): `initial` ✓, `follow_up` ✓, `correspondence` ✗. Without this, a
    four-message thread about parking logistics with an already-booked venue scores as four
    "outreaches" and the *outreaches/week* target measures email volume instead of prospecting
    effort.
- **Contact timeline is a read-time union view** over `outreaches` + `email_messages` +
  `opportunity_notes` + `status_events` — not a table. This is what lets `outreaches` stay
  outbound-only without losing a unified history on the contact page.
- **`email_threads`** — thread identity, assigned **once at ingest** by the poller (which already
  holds the header chain) rather than re-derived on every read: `subject_normalized`, `contact_id`,
  `opportunity_id`, `last_message_at`, `last_direction`, `closed_at`. **`contact_id` and
  `opportunity_id` are both NULLABLE** — three legitimate states: unknown sender awaiting import
  (both NULL), side-channel mail with a known contact tied to no gig (`contact_id` only), and gig
  correspondence (both set).
  - **Threads close explicitly, never by inference.** `closed_at` is set by Donna ("no reply
    needed") or automatically when the linked opportunity closes. Needs-attention surfaces a thread
    only when it is *open*, `last_direction = 'out'`, and aged past a threshold — so a terminal
    message that never wanted an answer produces no nag. There is no "awaiting reply" status
    derived from direction alone.
- **`email_messages`** — sent/received emails backing the composer + reply threading: `thread_id`
  (FK → `email_threads`), `message_id` (RFC 5322), `in_reply_to`, `references`, `direction` (out/in),
  `subject`, `from_addr`, `to_addr`, `opportunity_id`, `contact_id`, `s3_key` (raw MIME /
  attachments), `sent_at` / `received_at`. **`contact_id` / `opportunity_id` are nullable** for the
  same three states as the thread. Inbound replies match on `in_reply_to` / `references` → a stored
  outbound `message_id`.
- **`follow_ups`** — a scheduled reminder: `due_date` (explicit calendar date), `note` (free-form),
  `status` (pending / done), `contact_id`, optional `opportunity_id`, `remind` (dashboard + email),
  `completed_at`. Created standalone (from a contact, opportunity, the composer, or a Next-follow-up
  card) or as a rider on a logged outreach. Distinct from `outreaches` (past touches) and
  `opportunity_notes` (dated commentary): follow-ups are *future*, actionable, and can be marked done.
- **`message_templates`** — `channel` (dm / email / power_partner), body with **merge fields**
  (`[Name]`, …). `user_id` NULL = shared template (editable in place — admin-gated under
  multi-user); **duplicate** a shared template into a personal copy (`user_id` set).
- **`targets`** — per-user, per-type, cadence (weekly/monthly/quarterly), `goal_count`.
- **`talks`** + **materials** (S3 files) — the Guest Workshop menu.
- Catalog tables: `organization_types`, `contact_roles`, `opportunity_statuses`
  (`is_terminal`, `sort_order`; includes terminal **Delivered**, **Cancelled**, **Lost/Passed**),
  `outreach_kinds` (**`counts_toward_target`** — see `outreaches` above), `outreach_channels`,
  `comp_types`, `payment_statuses`, `warmth_tiers`, `target_types`, etc.

**Write invariant — one transaction per send.** Sending an email writes an `email_messages` row,
its `email_threads` row (created or touched), an `outreaches` row, and *optionally* a `follow_ups`
row **atomically**. A partial write would either lose the touch from the journal or leave a thread
with no messages.

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
6. Email: SES composer + attachments, IMAP Sent-append + reply-threading poller
7. Follow-up reminders

## 7. Decisions

**Decided**
- **Data store:** share the running `jobtracker-db` RDS instance (new `speakertracker` schema) — not
  a dedicated instance.
- **Scope:** single-user for now (Donna; Brian admin). Multi-user is a future expansion whose main
  impact is the **email layer** (per-user mailbox connections — OAuth via Gmail/Graph for external
  providers, generic IMAP/SMTP fallback, behind a provider-adapter abstraction); the rest of the
  data model — including `email_messages` threading — is unaffected. Not designed now.
- **Email send path:** SES `SendRawEmail` + IMAP-`APPEND` to Sent (IAM-authed send, full header
  control) — not authenticated WorkMail SMTP.
- **Venue Contacts card:** shows **affiliation info only** — title + primary-contact flag + warmth +
  ★ power-partner. Opportunity roles (Introducer / Lead) are per-gig and live on the opportunity,
  not the org's contact list.
- **Targets:** no revenue / money target for now — the `targets` model is generic, so a
  paid-bookings / `$-booked` target is a trivial later add (a new `target_type`). Targets stay
  activity-based while Donna is in visibility-building mode.
- **`outreaches` is outbound-only**, and `outreach_kinds.counts_toward_target` gates the metric.
  Inbound mail never enters the journal; the contact timeline is a read-time union view. Rationale:
  email correspondence must not inflate activity stats (§4).
- **Composer outreach kind is inferred, not asked** — `initial` on first outbound touch to a
  contact, else `correspondence` — surfaced as an editable chip. Right by default, always
  correctable, no friction tax on one-line replies.
- **Unknown-sender import = IMAP drop folder** (`Speaker Tracker/Import`), auto-created +
  subscribed by the poller. Chosen over forward-to-import because a folder move preserves the
  original `Message-ID` (so replies thread correctly) and over a Gmail-style thread-ID handle
  because IMAP has no such addressable handle — job-tracker's import mechanism does not transfer.
- **Threads close explicitly** (`email_threads.closed_at`), never inferred from `last_direction`;
  an opportunity closing auto-closes its threads. Follow-up riders are **opt-in, default off**.
- **No splash gate; 90-day session.** Land on the normal page with a header **Sign In**;
  `refreshTokenValidity` 90 days + `automaticSilentRenew` + `localStorage`. Justified by Donna
  working from a **fixed office desktop**, not a travelling laptop — revisit for multi-user/mobile.
- **API envelope matches the siblings** — bare JSON, `{"error": "<message>"}`, 400/404/405 mapped
  at the handler top level. **But** unhandled exceptions get a catch-all → `{"error": "internal
  error"}` + 500 (the siblings re-raise and leak API Gateway's `{"message": ...}` shape), and
  `UserNotFoundError` maps to 404 rather than falling into the 500 branch.
- **Cognito is invite-only:** `selfSignUpEnabled: false`, admin-created users,
  `removalPolicy: RETAIN` (legacy-tracker uses `true` / `DESTROY` — not appropriate for Donna's
  CRM, where a stack teardown must not silently delete the user pool).
- **CI from slice 1:** GitHub Actions on PR/push running `ruff`, `pytest`, and `tsc --noEmit`.
  **No deploy step** — deploys stay manual. Neither sibling has any CI, and legacy-tracker carries
  2 tests total; speaker-tracker is where CODING-GUIDELINES §7 stops being aspirational.
- **One API Lambda, not one per route-group.** Powertools `APIGatewayHttpResolver` + a `Router` per
  route-group; `migrate` / `imap_poll` / `followup_notify` stay separate. ~20 functions would each
  cold-start independently and each pay the 2–6s RDS TLS handshake, which a sporadic single user
  would hit on nearly every distinct action. The layered architecture is unaffected.
- **DB connection reused at module scope** (only possible because of the single Lambda), with the
  per-request `SET time_zone` as the liveness probe and a single reconnect on a lost connection.
  `ping(reconnect=True)` is **banned** — it reconnects with the expired IAM token stored on the
  connection, failing intermittently on any container older than 15 minutes.
- **Cognito Managed Login**, not the classic Hosted UI (in maintenance; no passkeys, no real
  branding). Requires the Essentials feature plan plus an explicit branding resource.
- **The SPA sends the ID token**, not the access token — Cognito ID tokens carry `aud = clientId`,
  which is what `HttpJwtAuthorizer` validates. Access-token behaviour is unverified; test before
  relying on it.
- **The API owns `users`-row creation**, not the Cognito trigger. `post_confirmation` has a hard 5s
  timeout against a 2–6s cold TLS handshake, and `AdminCreateUser` creates users already-confirmed
  so the trigger may never fire at all. A lazy idempotent upsert on the first authenticated request
  runs on a warm path and cannot break sign-in; the trigger stays best-effort.
- **Packaging: `uv`** with `--python-platform aarch64-manylinux2014` and `--only-binary=:all:`,
  bundled per function rather than via a layer. `pydantic-core` ships compiled wheels, and without
  the binary-only flag a host-platform sdist build silently ships x86 objects to an arm64 function.

- **IMAP poll interval: flat 1 minute.** ~43,800 invocations/month ≈ **$1.50/month** — cost is not
  the constraint. Required from the start, because retrofitting the cursor means a backfill:
  **reserved concurrency = 1** (a poll running past 60s must never overlap the next); a per-folder
  **`UIDNEXT` cursor** so each poll is an incremental UID-range fetch above the watermark, not a
  rescan (most polls touch zero messages); the Secrets Manager fetch **cached at module scope**;
  and `LOGOUT` in a `finally` on every path.
  - **The WorkMail connection quota is resolved and non-binding: 10 concurrent per user+IP pair.**
    Lambda outside a VPC draws from rotating source IPs, and reserved concurrency 1 means the poller
    holds at most one connection at a time — so it cannot contend with Donna's Outlook, which is a
    different IP. The 1-minute interval is safe.
  - *Documented fallback if WorkMail throttles:* two-tier polling — `Import` every 1 min (near-always
    empty, and the only **interactive** path: Donna drags in Outlook, then switches to the app
    expecting the badge), `INBOX`/`Sent` every 5 min (she sees replies in Outlook instantly anyway;
    the app is the CRM record, not her mail client). The folder set is already configuration, so
    this is a config change, not a rewrite.

**Still open**
- *(nothing — see Decided above.)*

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

## 10. Baselines surveyed

- **`~/360-balanced-living/legacy-tracker`** — CDK(TS), same-origin CloudFront, Python Lambdas +
  RDS IAM auth, Vite/React + Tailwind, `react-oidc-context`. Structural baseline. (`DESIGN.md`
  there is stale — its live docs are `docs/ARCHITECTURE.md`, `docs/DATABASE.md`.)
- **`~/projects/job-tracker`** — SAM + SSM, Python Lambdas + RDS IAM auth (MySQL), react-bootstrap.
  Source for the EventBridge-Scheduler + SES reminders and `targets` subsystems. **Its Gmail
  composer is not reusable** — this app uses WorkMail / SES / IMAP (see §3 Email).