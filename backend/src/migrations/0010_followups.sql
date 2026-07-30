-- 0010_followups.sql — Speaker Tracker schema slice 7: scheduled follow-up reminders.
--
-- One table. A follow-up is a *future, actionable* reminder, which is what separates it from the
-- two tables it would otherwise resemble: `outreaches` records touches that already happened, and
-- `opportunity_notes` records dated commentary. Only a follow-up can be marked done, and only a
-- follow-up causes the app to reach out to Donna rather than the other way round.
--
-- NOT `0009`. DEV-PLAN reserved that number for this file, but slice 6b spent it on
-- `0009_external_message_id.sql`; `DATABASE.md`'s migration table is the authority on what is
-- already claimed. The runner keys its applied-set on the filename stem, so a duplicate number does
-- not raise — it silently skips whichever file it sees second, which is why this is worth a
-- sentence rather than a shrug.
--
-- Four decisions are baked into the columns below and each one overrides something written
-- elsewhere, so they are recorded here rather than only in the design docs:
--
--   1. NO `status` COLUMN. `completed_at IS NULL` *is* the pending state. DESIGN.md §4 lists both a
--      `status` and a `completed_at`; DATABASE.md overrides it, because two columns encoding one
--      fact drift apart and then disagree. Everything that asks "is this outstanding" asks
--      `completed_at IS NULL`, and marking done is a single UPDATE that cannot half-succeed.
--   2. `due_date` IS A DATE, NOT A TIMESTAMP. DESIGN.md §7 is explicit that a follow-up is set for
--      a calendar day, never a relative "in N days". A DATE also cannot be silently shifted by a
--      timezone conversion on the way in or out, which for a reminder that must fire on Donna's
--      Tuesday is the whole point. The *time* of day the email fires is not stored here at all —
--      it is applied when the EventBridge schedule is built (07:00 in the user's zone), so changing
--      that hour later is a code change and not a data migration.
--   3. BOTH LINKS ARE INDIVIDUALLY NULLABLE, BUT NOT BOTH AT ONCE. A gig-level reminder may name no
--      person ("chase the Hanalei contract"), and a person-level reminder may belong to no gig
--      ("check in with Kalei"), so neither column can be NOT NULL. A row with neither is
--      unreachable in the UI — it would appear on no contact and no opportunity — hence the CHECK.
--   4. `remind_by_email` DEFAULTS TO TRUE. The *rider* that creates a follow-up while logging
--      outreach is opt-in and defaults off (DESIGN.md §7 — sending an email must never silently
--      schedule anything). That is a different question from what an explicitly created follow-up
--      does: having asked for a reminder, the default is that it actually reminds you. Setting this
--      false makes the row dashboard-only and no schedule is created for it.
--
-- Idempotent, like every migration here (`CREATE TABLE IF NOT EXISTS`), so the forward-only
-- recovery path — DELETE the `schema_migrations` row, redeploy — re-runs this file cleanly.

-- ---------------------------------------------------------------------------
-- follow_ups — a scheduled reminder against a contact, an opportunity, or both.
--
-- EventBridge Scheduler names each schedule deterministically as `followup-<id>` (ported from
-- job-tracker), which is why no schedule identifier is stored: the name is a pure function of the
-- primary key, so create/update/delete need no read-back and a row can never be orphaned from its
-- schedule by a lost column. The consequence to keep in mind is that the notify Lambda is given
-- everything it needs in the schedule's payload and NEVER reads this table — so any edit to a
-- field the reminder email renders (the note, the due date) has to cancel and recreate the
-- schedule, and so does marking the follow-up done. A completed follow-up whose schedule survived
-- would email Donna about something she has already finished.
--
-- No ON DELETE behaviour on either link: contacts and opportunities are soft-deleted (deleted_at),
-- never removed, so a cascade would be dead code that quietly becomes load-bearing if anyone ever
-- hard-deletes.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS follow_ups (
  id              BIGINT    NOT NULL AUTO_INCREMENT,
  user_id         BIGINT    NOT NULL,
  contact_id      BIGINT    NULL,               -- nullable: a gig-level reminder names no person
  opportunity_id  BIGINT    NULL,               -- nullable: a person-level reminder names no gig
  due_date        DATE      NOT NULL,           -- calendar day, never a relative offset
  note            TEXT      NOT NULL,           -- free-form; it is the body of the reminder
  remind_by_email BOOL      NOT NULL DEFAULT 1, -- false = dashboard only, no schedule created
  completed_at    TIMESTAMP NULL DEFAULT NULL,  -- NULL = pending; this IS the done-state
  created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted_at      TIMESTAMP NULL DEFAULT NULL,
  PRIMARY KEY (id),
  -- The Dashboard's due list, in the order it asks: this user's rows, by date, pending only
  -- (DATABASE.md §"follow_ups"). `completed_at` trails `due_date` because the query ranges on the
  -- date and equality-matches NULL on the flag, not the other way round.
  KEY ix_follow_ups_user_due (user_id, due_date, completed_at),
  -- These two are not speculative optimization: InnoDB requires an index on every referencing
  -- column and will auto-create an unnamed one if it is absent. Naming them here means the contact
  -- and opportunity detail pages get their lookup from an index we chose and can find again.
  KEY ix_follow_ups_contact (contact_id),
  KEY ix_follow_ups_opportunity (opportunity_id),
  -- A follow-up attached to nothing is unreachable in the UI, so it is rejected by the database
  -- rather than only by the API — this is the one invariant a bad migration or a manual INSERT
  -- could otherwise breach. Note there is no three-valued-logic trap here: `IS NOT NULL` yields
  -- TRUE or FALSE and never NULL, so the CHECK cannot pass by evaluating to unknown.
  CONSTRAINT ck_follow_ups_target CHECK (contact_id IS NOT NULL OR opportunity_id IS NOT NULL),
  CONSTRAINT fk_follow_ups_user FOREIGN KEY (user_id) REFERENCES users (id),
  CONSTRAINT fk_follow_ups_contact FOREIGN KEY (contact_id) REFERENCES contacts (id),
  CONSTRAINT fk_follow_ups_opportunity FOREIGN KEY (opportunity_id) REFERENCES opportunities (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;