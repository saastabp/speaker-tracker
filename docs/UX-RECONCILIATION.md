# Speaker Tracker — UX Reconciliation

> **Status: open — reconciliation pass on branch `ux-reconciliation` (started 2026-07-23).**
> The implemented SPA drifted from the Donna-approved mockup. This is the tracked worklist to
> bring each page back into line before resuming slice 6a.

**Source of truth:** `samples/speaker-tracker-mockup.html` (Donna-approved) + the mockup-render
screenshots. Line refs below (`mL###`) point into that HTML file.

**Dispositions:**
- **FIX** — build/change to match the approved mockup.
- **KEEP** — implementation deviates on purpose (recorded decision or reasonable real-app addition);
  do **not** revert. Confirm at triage.
- **DEFER** — needs backend/surfaces from a later slice; do not half-build now.

**Method:** one page at a time as a self-contained unit, per-mutation approval, `ruff`/tests where
backend is touched. Check items off as they land.

---

## Shared / cross-boundary items (build once, reused by several pages)

- [ ] **Filter-chip + search row** — a reusable top-of-list toolbar (search box + pill filters).
  Reused by Venues, Contacts, History (and Emails in 6a). Build with the first list page (Venues).
- [ ] **Follow-up scheduling** — **DEFER.** The mockup places it in the Log Outreach modal, New
  Opportunity modal, Opportunity detail, Venue detail, and Contact detail, but it writes to
  `follow_ups`, which ships in `0008_followups.sql` (**slice 7 — not yet migrated**). Tag every
  follow-up finding DEFER; do not half-build UI against a missing table.
- [ ] **Email surfaces on detail pages** — **DEFER** to slice 6a: "Compose email" buttons and
  per-contact/per-venue Emails cards, email-thread views.
- [ ] **Talks & Materials screen + one-step attach** — **DEFER** to the Talks & Materials slice.
- [ ] **Dashboard aggregate drill-down** — **DEFER** to DEV-PLAN slice 8 (click an aggregate → the
  filtered summary list). Not in the mockup; a new feature. Prerequisite is the list filter rows
  this pass builds, so don't wire click handlers during reconciliation.

---

## 1. Dashboard  (`pages/Dashboard.tsx` vs mL382–443)

> **✅ SHIPPED & browser-verified (2026-07-23).** All FIX items done (greeting, week/tz subtitle,
> funnel conversion %, Delivered row, money sub-counts + pro-bono tile, Coming-up card, two-column
> layout, Stale demoted). KEEP: "% of goal" tile subtitle; DROP: card hint sub-headers (Brian).
> Added `research_incomplete` to Needs attention (any not-research-ready venue → links to venue).
> DEFERRED: "+ Log outreach" header button (needs contact-picker), funnel-leak caveat, richer
> needs-attention timing reasons (need `follow_ups`/0009 — see [[ticklers-reminders-future]]).
> Backend: `0007_target_labels` + dashboard repo/model additions (deployed to sandbox).

**High**
- [ ] FIX — Restore personalized header "Good morning, Donna" (generic "Dashboard" title now) — mL384
- [ ] FIX — Restore week-of + timezone subtitle ("Week of Jul 13 – 19 · Kauaʻi (HST)") — mL384
- [ ] FIX — Restore primary "+ Log outreach" action button in the page head — mL385
- [ ] FIX — Restore the "Coming up" schedule card (dated agenda list) — mL429–438
- [ ] FIX — Funnel: render stage-to-stage **conversion %** (e.g. 39/57/75%), not just raw counts — mL399–402
- [ ] FIX — Restore "Pro bono" as a money stat tile (w/ "visibility gigs" sub-label); currently demoted to a text line — mL412

**Medium**
- [ ] FIX — Money card title "Revenue & payments — Q3" (now "Money") — mL407
- [ ] FIX — Funnel card title "Pipeline funnel — Q3 to date" (now "Funnel — reached or beyond") — mL396
- [ ] FIX — Restore funnel-leak annotation ("1 booked gig cancelled — still counts as booked…") — mL403
- [x] DROP — Card hint sub-headers ("conversion between stages" / "paid gigs only" / "outreach + payments"): **not reproduced** (Brian 2026-07-23). Illustrative microcopy; and "paid gigs only" is wrong — the money card shows the pro-bono count. — mL396/407/418
- [ ] FIX — Stat tiles: guarantee the four signed-off metric labels (New venues researched, Outreach touches, Pitches sent, Gigs booked) — mL388–391
- [x] KEEP — Per-tile subtitle stays the neutral **"% of goal"**, not the mockup's interpretive sentences (Brian 2026-07-23): that detail belongs in Needs Attention / Coming Up, not duplicated per tile. — mL388–391
- [ ] FIX — Money tiles: restore supporting sub-counts (Outstanding "2 invoiced", Received "2 collected", Booked "3 paid gigs") — mL409–411
- [ ] KEEP? — "Stale — needs a nudge" card is an EXTRA (not in mockup); it occupies the mockup's "Coming up" slot. Decide keep-alongside vs replace at triage — impl only

**Low**
- [ ] FIX — "Needs attention" chips: restore the richer status vocabulary (Overdue pay / day-counts / Awaiting pay / "1/3") — mL421–425
- [ ] FIX — Right-column pairing: mockup pairs "Needs attention" with "Coming up" — mL416–439

---

## 2. Pipeline  (`pages/Pipeline.tsx` + `OpportunityFormModal` + `LogOutreachModal`)

> **Board: Tier 1 + Tier 2 SHIPPED & browser-verified (2026-07-23).** Tier 1: stage-color dots,
> subtitle, "+ New opportunity", footer note, kept Log-outreach button, approved payment-chip
> colors, warm-panel column background + uppercase headers, **Recently-closed column**, app-wide
> sentence-case badges. Tier 2: card leads with venue + "talk · format" line + type chip — needed
> `talk_title` + `organization_type` added to `_SUMMARY_SELECT`/model/api (deployed).
> **DEFERRED:** Tier 3 (due/overdue, research N/3, warm-intro — need `follow_ups`/0009), removing
> the free-text `title` field (revisit at the Opportunity detail page).
> **Modals: REWORKED to the mockup & browser-verified (2026-07-24).** Log Outreach: uppercase
> `FieldLabel`s throughout, Template + editable Message split (merge fills `[Name]`, "there"
> fallback, Copy-to-clipboard), picker always visible (un-gated), channel segmented (email
> excluded), Kind kept. New Opportunity: `FieldLabel`s + mockup order, **title derived from venue +
> talk** (free-text field dropped), **Starting stage / Lead contact / Payment status** now built —
> via a new backend `OpportunityCreateInput` + create-seed (atomic: one `status_events` row at the
> chosen stage, lead linked `is_primary`, non-terminal-stage guard; 59 backend tests green). Payment
> status shown only for paid comp. Shared `FieldLabel` + `BRAND_MUTED`/`BRAND_FAINT` added to
> `theme.ts`. **Still DEFERRED:** the follow-up blocks in both (need `follow_ups`/0009); event-date
> free-text coarse dates (kept the native picker — data-model).

### Board (vs mL444–490)
**High**
- [ ] FIX — Card: add the talk/offer line (e.g. "Wellness Wheel for Women · guest workshop") — mL455–475
- [ ] FIX — Card: lead with organization, de-emphasize talk (emphasis currently inverted) — mL455
- [ ] FIX — Card: restore the category/venue-type chip (Association/Expo/Resort/…) — mL455–483
- [ ] FIX — Card: restore due/follow-up indicators ("follow up Jul 19", "no reply · 9d" overdue, …) — mL456–479

**Medium**
- [ ] FIX — Card: research-progress chip ("Research 1/3") — mL455
- [ ] FIX — Card: "★ warm intro" chip — mL465
- [ ] FIX — Dedicated "Recently closed" column when Show closed is on — mL481–485
- [ ] FIX — Page subtitle / open-count ("10 open opportunities · drag cards between stages") — mL446
- [ ] FIX — Primary button label "+ New opportunity" (now "Add opportunity") — mL449
- [x] KEEP — "Log outreach" button in board header (EXTRA vs mockup) stays (Brian 2026-07-23): a wanted action even though the mockup omits it. — impl only

**Low**
- [ ] FIX — Footer note about closed gigs living in History — mL487
- [ ] (Column set/order matches well — data-driven from funnel catalog)

### New / Edit Opportunity modal (vs mL1200–1239)
**High**
- [ ] DEFER — Follow-up block (switch + date + note) — needs `follow_ups` (0009) — mL1228–1233
- [x] FIX — "Payment status" segmented control (Unbilled/Invoiced/Partial/Paid) — create-only, paid comp — mL1224–1226
- [x] FIX — "Starting stage" select (default Researching) — mL1213
- [x] FIX — "Lead contact" select — mL1214
- [x] FIX/DISCUSS — Remove free-text "Title" field; derived from venue + talk on submit (Brian: derive on frontend) — mL1204

**Medium**
- [x] FIX — "Compensation" section header + Pro-bono explanatory note — mL1217/1227
- [x] FIX (remove) — "Currency" field (app is USD-only) — impl only
- [x] FIX — Comp "Type" as segmented control (Paid/Pro bono/Trade) vs dropdown — mL1219–1221
- [x] FIX — Footer hint "Starts in Researching — drag it across the board…" — mL1235

**Low**
- [x] FIX — Labels: "Venue / organization", "Talk / offer", "Angle for this gig" — mL1204/1206/1216
- [x] FIX — Field pairing/order (Talk+Event date; Format standalone segmented) — mL1205–1211
- [ ] DEFER — Event date free-text "e.g. Oct 2026" (coarse dates) vs native picker — kept native picker (data-model) — mL1207

### Log Outreach modal (vs mL1106–1132)
**High**
- [ ] DEFER — Follow-up block — needs `follow_ups` (0009) — mL1122–1127

**Medium**
- [x] KEEP — Email channel removed (owned by composer; slice-4 decision) — mL1112
- [x] KEEP — "Kind" segmented control (initial/correspondence; contact-scoped inference; slice-4) — impl only
- [x] FIX — Submit button "Log touch" — mL1129
- [ ] FIX — Footer hint "Counts toward this week's 8-touch target" — kept generic "…this week's outreach target" (avoid hardcoding the count) — mL1129

**Low**
- [x] FIX — Labels "Opportunity" / "Date" — mL1119/1118
- [x] FIX — Channel as segmented control vs dropdown — mL1111–1113
- [x] TODO — `TemplatePicker` split into Template select + editable merged Message textarea + copy-to-clipboard — mL1114–1116

---

## 3. Venues  (`pages/Venues.tsx` + `VenueDetail.tsx` + `VenueFormModal`)

### List (vs mL652–674)
**High**
- [ ] FIX — Search box ("Search venues…") — mL658  *(shared filter row)*
- [ ] FIX — Filter pills (All types / Resorts / Networks / Podcasts / Ready only) — mL659  *(shared)*
- [ ] FIX — "Last touch" column — mL662

**Medium**
- [ ] FIX — Research column: show partial progress fraction (2/3) for not-ready rows — mL668–671
- [ ] FIX — Organization cell subtitle line ("Statewide · est. 1978") — mL664
- [ ] FIX — Type as color-coded chip vs plain text — mL664–671

**Low**
- [ ] FIX — Column header "Organization" (now "Name") — mL662
- [ ] FIX — Page heading "Venues & Organizations" + count subhead ("8 tracked · 5 outreach-ready") — mL654
- [ ] FIX — Column order (Organization, Type, Why it fits, Research, Contacts, Last touch) — mL662
- [ ] FIX (remove) — "Location" column (not in mockup list) — impl only
- [ ] KEEP? — "Log outreach" button in list header — impl only
- [ ] FIX — Add button "+ Add venue" — mL655

### Detail (vs mL677–742)
**High**
- [ ] FIX — "Opportunities" panel (opp row, stage chip, on-this-gig contact chips) — mL691–705
- [ ] FIX — "Activity" panel (outreach + stage-change timeline) — mL706–714
- [ ] DEFER — "Compose email" action (6a) — mL681
- [ ] DEFER — "Log touch" action — needs follow-up/outreach entry point; wire when reconciling outreach — mL681

**Medium**
- [ ] DEFER — "Next follow-up" panel (needs `follow_ups`) — mL724–730
- [ ] FIX — "Details" panel (Type / Location / Source / Added key-value grid) — mL731–739
- [ ] FIX — Research card header "Edit" affordance — mL686
- [ ] FIX — Research card title "Research — Kindling" — mL686
- [ ] FIX — Contacts panel: warmth chips + power-partner star — mL720–721

**Low**
- [ ] FIX — Header: colored type chip + description + website + Outreach-ready inline — mL680
- [ ] FIX — Ready badge label "Outreach-ready" — mL680
- [ ] FIX — Breadcrumb "Venues & Orgs › <name>" — mL678
- [ ] KEEP — "Delete" button (reasonable real-app addition) — impl only

### Modal (vs mL1134–1155)
**High**
- [ ] FIX — "Source" select field — mL1145
- [ ] DEFER/FIX — Research-readiness hint callout (references the target — safe to add as static copy) — mL1151

**Medium**
- [ ] FIX — "Research — Kindling" section divider before the three fields — mL1147
- [ ] KEEP? — "Notes" field (EXTRA) — impl only
- [ ] KEEP? — "Email domain" field (EXTRA) — impl only

**Low**
- [ ] FIX — Name label "Organization name" — mL1138
- [ ] FIX — Website "optional" tag — mL1144
- [ ] FIX — Field order (Name; Type/Location; Website/Source) — mL1138–1146
- [ ] FIX — Secondary footer action "Save, finish research later" — mL1153

*(Kindling fields What it is / Why it fits / How to approach are present in both — OK.)*

---

## 4. Contacts  (`pages/Contacts.tsx` + `ContactDetail.tsx` + `ContactFormModal`)

### List (vs mL745–767)
**High**
- [ ] FIX — Search box — mL751  *(shared filter row)*
- [ ] FIX — Filter pills (Everyone / Power partners / Needs follow-up) — mL752  *(shared)*
- [ ] FIX — "Role · Organization" column w/ "+N orgs" overflow chip (now bare "Venues" count) — mL755/759

**Medium**
- [ ] FIX — "Source" column — mL755
- [ ] FIX — "Last touch" column — mL755
- [ ] DEFER — "Next follow-up" column (needs `follow_ups`) — mL755/761/763

**Low**
- [ ] FIX (remove) — "Email" column (not in mockup) — impl only
- [ ] FIX — Header subtitle "7 people · 2 power partners" — mL747
- [ ] FIX — Power-partner "Power partner" sub-label under name — mL757/759
- [ ] FIX — Add button "+ Add contact" — mL748

### Detail (vs mL769–847)
**High**
- [ ] FIX — "Opportunities across orgs" card (role per gig + Introducer/Primary + status chips) — mL785–797
- [ ] FIX — "Relationship" card (power-partner marker + warmth explanation) — mL823–829
- [ ] DEFER — "Compose email" primary action (6a) — mL773

**Medium**
- [ ] DEFER — "Emails" card (per-contact thread list) (6a) — mL798–803
- [ ] DEFER — "Next follow-up" card (needs `follow_ups`) — mL830–836
- [ ] FIX — Power-partner marker on header + header chips ("Wears three hats…") — mL772
- [ ] FIX — "Reach her" block: add LinkedIn + Instagram — mL816–821

**Low**
- [ ] FIX — Details card "Added" date — mL838–843
- [ ] FIX — Action "Log touch" (now "Log outreach") — mL773
- [ ] KEEP — Edit/Delete header buttons (reasonable) — impl only
- [ ] FIX — Affiliations "+ Add affiliation" button (progressive disclosure) vs always-inline row — mL778

### Modal (vs mL1158–1197)
**High**
- [ ] FIX — Power-partner toggle (★) + explanatory description — mL1188–1191
- [ ] FIX — Multi-hat org attach ("Add this org to her" + note) — mL1163–1170
- [ ] FIX — "Organization" select + "Role / title at this org" fields — mL1174/1176

**Medium**
- [ ] FIX — "Warm intro / mutual connection" field — mL1192
- [ ] FIX — Warmth as Cold/Warming/Warm segmented control vs dropdown — mL1185–1187
- [ ] FIX — Dedupe as dedicated "Find existing person first" search vs passive Name-triggered alert — mL1162

**Low**
- [ ] FIX — LinkedIn + Instagram fields — mL1182–1183
- [ ] KEEP? — "Source" text field (EXTRA in modal; mockup has it on detail) — impl only
- [ ] FIX — "…or create a new person" section divider — mL1171
- [ ] FIX — Submit button "Add contact" — mL1195

---

## 5. History  (`pages/History.tsx` + history-detail)

### List (vs mL562–585)
**High**
- [ ] FIX — Summary stat line ("9 closed gigs · 6 delivered · 2 cancelled · 1 lost · $1,850 collected · 3 pro bono") — mL564
- [ ] FIX — Outcome/comp/year filter pills (All outcomes/Delivered/Cancelled/Lost/Paid/Pro bono/2026) — mL569  *(shared filter row)*
- [ ] FIX — Search box ("Search closed gigs…") — mL568  *(shared)*

**Medium**
- [ ] FIX — "Export CSV" button — mL565
- [ ] FIX — "Format" column (Keynote/Workshop/Podcast/Expo) — mL572
- [ ] FIX — "Comp" column (Paid/Pro bono/Trade chip) — mL572
- [ ] FIX — "Date" column = event date (currently shows closed_at) — mL572/574

**Low**
- [ ] FIX — Column "Gig" (now "Title") + two-line cell w/ "Talk · format" sub-line — mL572/574
- [ ] FIX — Column "Amount" (now "Fee") — mL572
- [ ] FIX — Column "Paid" (now "Payment") — mL572
- [ ] FIX (remove) — "Venue" column (not in mockup) — impl only

### Detail (read-only closed-gig record — vs mL588–649)
**High**
- [ ] FIX/DISCUSS — Serve a **read-only record** view (Duplicate/Reopen only), not the editable pipeline detail. Decide: separate component vs read-only mode of OpportunityDetail — mL591–592

**Medium**
- [ ] FIX — Header status/payment chip row (Delivered / Paid · $ / ✓ Paid date / read-only) — mL591
- [ ] FIX — "Duplicate" and "Reopen" buttons — mL592
- [ ] FIX — "Record" card (Closed date + Source) — mL640–646
- [ ] FIX — "Invoiced" field (invoice date + number) in compensation card — mL612

**Low**
- [ ] FIX — Heading "Compensation & payment" (now "Money & payment") — mL607
- [ ] FIX — "Paid on" carries payment method ("· check") — mL613
- [ ] FIX — "Venue & contact" card (venue line + organizer w/ Warm chip) vs "People on this gig" — mL628–632
- [ ] FIX — "Outcome notes" card — mL635–638

---

## 6. Templates  (`pages/Templates.tsx` + `TemplateFormModal`)

### Page (vs mL989–1023)
**High**
- [ ] FIX — Rebuild as **card grid** (rich per-template card) vs data table — mL994–1022
- [ ] FIX — Per-template body / merge-field preview ("Hi [Name], I've been following…") — mL999/1008/1017
- [ ] FIX — Per-template audience/description paragraph — mL998/1007/1016

**Medium**
- [ ] FIX — Per-template usage metadata ("Used 7 times · last Jul 12") — mL1000/1009/1018
- [ ] KEEP — Delete action (personal templates only) — reasonable — impl only
- [ ] FIX — Scope chip "Your copy" (now "Personal") — mL1005

**Low**
- [ ] FIX — Heading "Message Templates" — mL991
- [ ] FIX — Button "+ New template" — mL992
- [ ] FIX — Subhead wording — mL991
- [ ] FIX — Edit/Duplicate as labeled text buttons vs icon-only — mL1001
- [ ] KEEP? — Explicit Purpose/Channel columns (table-only artifact; moot after card rebuild) — impl only

### Modal (vs mL1280–1291)
**High**
- [ ] FIX — "Duplicate as my copy" action in footer — mL1291
- [ ] FIX — `kind-hint` block (merge-field note + shared-template warning) — mL1289

**Medium**
- [ ] KEEP — Split Purpose + Channel selects vs mockup's single "Use for" (matches the channel/purpose data model) — mL1286
- [ ] KEEP — "Subject" field (EXTRA; needed for email templates) — impl only

**Low**
- [ ] FIX — Footer status hint "Shared template · editable in place" — mL1291
- [ ] FIX — Save button "Save template" — mL1291
- [ ] FIX — Body helper wording — mL1288
- [ ] KEEP? — Footer "Cancel" button — impl only

---

## 7. Targets  (`pages/Targets.tsx` vs mL1026–1068)

**High**
- [ ] FIX/DISCUSS — Rebuild as a **progress-tracking list** (one cadence per target + progress meter + actual/goal + Edit) rather than the target×cadence entry matrix — mL1031–1068
- [ ] FIX — Actual-vs-target **progress meter** per row (good/warm color states) — mL1036–1064
- [ ] FIX — "Actual / Goal" numeric column (e.g. 4/5, 6/8) — mL1037–1065

**Medium**
- [ ] FIX — "+ New target" button — mL1029
- [ ] FIX — Per-row "Edit" button vs inline-editable cells — mL1038–1066
- [ ] FIX — Single cadence chip per target vs three editable cadence columns — mL1035–1063
- [ ] FIX — Subtitle "Goals that fit two hours a week — one hour Monday, one hour Friday" — mL1028

**Low**
- [ ] FIX — Two-line target labels (name + helper copy) for the five signed-off targets — mL1034–1062
- [ ] FIX — Column headers (Target / Cadence / Progress / Actual · Goal) — mL1032

---

## 8. Nav / chrome  (`components/AppShell.tsx` + `main.tsx` vs mL340–376)

**High**
- [ ] FIX — Group/section headings: Relationships / Outreach / Growth (now flat) — mL356/361/366
- [ ] DEFER — "Compose" nav item + `/composer` route (6a) — mL364

**Medium**
- [ ] FIX — Nav label "Venues & Orgs" (now "Venues") — mL357
- [ ] DEFER — Nav label "Talks & Materials" (now "Talks") — becomes real with the Talks slice — mL368
- [ ] FIX — Nav order (Dashboard/Pipeline/History cluster; then Relationships; then Outreach; then Growth) — mL351–368
- [ ] FIX — 360 Balanced Living logo in the sidebar (text-only now) — mL346–348
- [ ] DEFER — Emails/Talks currently route to Placeholder — resolve as those slices land — main.tsx wildcard

**Low**
- [ ] KEEP — Header auth controls (email + Sign in/out) — real-world chrome, not in the static mockup — impl only