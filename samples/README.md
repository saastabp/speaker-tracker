# UI Mockups

`speaker-tracker-mockup.html` — a self-contained, clickable HTML/CSS mockup of the Speaker
Tracker UI. No build step; open the file in a browser, or view the published private Artifact:

**Artifact:** https://claude.ai/code/artifact/d6a3cd29-1c4a-4eee-bb47-53a585de0e67

## What it is

A high-fidelity, static mockup (Mantine-style look) used to react to the UI before any code is
written. All data is realistic **sample data** pulled from the strategy session
(`../docs/strategy-session-doc.pdf`) — PWN Hawaii, Hanalei Bay Resort, WBEC-West, the three talks,
Donna's real pipeline shape — not lorem ipsum. See `../docs/DESIGN.md` for the architecture and
data model this visualizes.

- **Branded** with the 360 Balanced Living palette (see `../docs/DESIGN.md` §Branding): navy nav
  rail with the dark-bg logo lockup (inline SVG), warm-cream content, terracotta buttons, gold
  accents. Sans-serif throughout — the guide's Playfair/Lato spec is for the public site, not this
  app; color carries the brand here.
- **Light theme by default** (Donna dislikes dark themes). The `prefers-color-scheme: dark` media
  query was removed; dark only via the Artifact viewer's explicit theme toggle. Both themes are
  styled via CSS custom-property tokens.
- **Pipeline runs full browser width** (Donna works on a 36" monitor) — that page opts out of the
  1180px content cap.
- Left sidebar navigates between pages (SPA-style show/hide). A **Modals** section opens each
  dialog directly.

## Pages

Dashboard (targets + funnel + **Revenue & payments** card) · Pipeline (kanban, full-width) ·
**Opportunity detail** (fields, notes, lifecycle) · **History** (closed-gig table) ·
**History detail** (closed-gig record) · Venues & Orgs (list) ·
Venue detail (with Kindling research panel) · Contacts (list) · **Contact detail** (multi-org
affiliations) · **Emails** (thread inbox) · **Email thread** (conversation + inline reply) ·
Compose (rich email + research context) · Templates · Targets · Talks & Materials.

## Modals

- **Log outreach** — contact, channel, a **template picker** (merges `[Name]` + **Copy to
  clipboard** for pasting into a DM), date, opportunity link, note, and a calendar follow-up (date + note).
- **Add venue** — basics + a distinct Kindling research section + readiness hint.
- **Add contact** — dedupe search ("find existing person first") + new-person form; power-partner
  toggle with inline definition; role/title labeled "at this org".
- **New opportunity** — venue, talk/offer, format, starting stage (Researching), lead contact,
  per-gig angle, **compensation** (Paid / Pro bono / Trade + fee), **payment status**, follow-up toggle.
- **Close opportunity** — outcome (Cancelled / Lost, defaulted by stage) + reason + date; logs the
  reason to the opportunity's notes and moves it to History.
- **Schedule follow-up** — **calendar-dated** reminder + **free-form note** on a contact/opportunity,
  standalone (no outreach needed first); reminds on the Dashboard + email. Openable from the sidebar,
  any "Next follow-up" card, the composer footer, and the Log-outreach / New-opportunity riders
  (which now use a date picker + note, not an "in N days" selector).
- **Edit template** — edit a shared template in place (or **Duplicate** to keep a personal copy);
  the body uses merge fields like `[Name]` that fill on use. Openable from each Templates card.

## Money, payment & lifecycle

- **Pipeline cards** carry a money chip (`$1,500` / "Pro bono") + a payment chip. **Click a card**
  to open its **Opportunity detail** (fields, linked contacts, dated notes, lifecycle); each card
  has a close **×** (on hover) that opens the **Close-opportunity** modal (outcome + reason).
  Delivered is reached by dragging to the Delivered column — no button.
- **Lifecycle**: terminal statuses Delivered, Cancelled (still counts as booked), Lost. A gig is
  *closed* when Delivered + settled, or Cancelled, or Lost — closed gigs leave the board for
  **History**. A delivered-but-unpaid gig stays on the board until paid.
- **Dashboard**: a Revenue & payments card (Booked / Received / Outstanding + pro-bono count); the
  funnel adds Booked → Delivered with the Cancelled leak shown; overdue/awaiting payments appear as
  rows in **Needs attention**.
- **Show closed** toggle on the pipeline reveals a "Recently closed" column.

## Email

- **Emails inbox** (sidebar → Outreach → Emails) — all threads with awaiting-reply / new-reply /
  replied status; rows open the thread.
- **Email thread** — full conversation (sent + received, attachments, timestamps) with an **inline
  reply box** that threads correctly, sends via WorkMail/SES, appends to Sent, and logs a touch.
- Threads also surface on the **contact detail** (Emails card); new replies feed Needs-attention.

## Modeling made visible

- **Contact ↔ org is many-to-many.** The Contact detail page (click any contacts row, or the
  "↳ Pua Lindsey" sidebar link) shows one person with three affiliations (each with its own title),
  her opportunities across orgs with per-gig roles, and a unified activity log.
- **Opportunity ↔ contact roles** — the PWN opportunity card lists `Marcy · Primary` +
  `★ Pua · Introducer` (the insider-intro-chain case).
- **Context prefill** — opening Add-contact or New-opportunity *from the venue detail page*
  pre-fills that org; from the Contacts/Pipeline pages it shows the generic new-lead sample.
- **"Primary" is scoped** — "Primary contact" (default for an org) vs. "Lead on this gig".

## Editing

Plain HTML/CSS/JS in one file. After editing, re-publish to the same Artifact URL (Claude Code
`Artifact` tool with the same file path keeps the URL stable).