-- 0011_reminder_failures.sql — record that a follow-up's reminder email never arrived.
--
-- One nullable column on `follow_ups`. It exists because of a gap found while walking slice 7's
-- acceptance criteria: when a reminder fails, the follow-up row is untouched — still pending, still
-- on the Dashboard — so nothing is *lost*, but nothing tells Donna the nudge never came either. She
-- sees a follow-up that looks identical to one whose email arrived fine. The operator gets a
-- CloudWatch alarm; the person who needed the reminder gets silence.
--
-- **NOT `0012`, and materials moved rather than this one.** `DATABASE.md` had `0011_materials`
-- claimed as target schema. Materials is unwritten, this is being written now, so it takes the
-- next free number and materials becomes `0012` — the same resolution slice 7's own migration got
-- when 6b spent `0009`. The alternative (taking `0012` and leaving a hole) would have `0011`
-- applied *after* `0012` whenever materials lands, since the runner applies unapplied files in
-- version order regardless of when they appeared.
--
-- Why a column and not a status table: the failure is one fact about one row, it is cleared by the
-- next successful schedule, and it has no history worth keeping. A `reminder_deliveries` table
-- would be the right shape if reminders ever needed an audit trail — which is the same future
-- tickler work that would also give reminders a `sent_at`.

-- ---------------------------------------------------------------------------
-- follow_ups.reminder_failed_at — when this follow-up's reminder was dead-lettered.
--
-- NULL means "nothing has gone wrong", which covers both a reminder that sent and one that has not
-- fired yet. That ambiguity is deliberate and load-bearing: the app has no `reminder_sent_at` and
-- deliberately does not write one (`followup_notify` never touches the database, which is what
-- lets it run outside the VPC with no RDS handshake). So this column answers "did it fail?", never
-- "did it arrive?" — and it is written by a *different* function, the dead-letter consumer, which
-- runs only on failure and is the only part of the reminder path that holds database access.
--
-- Cleared whenever a new schedule is successfully put, so a reminder that failed on Monday and was
-- rescheduled to Friday does not keep showing as failed after Friday's send works.
--
-- No index: it is read as part of the row the Dashboard and Follow-ups page already select, never
-- filtered or ranged on. An index here would cost writes to serve no query.
-- ---------------------------------------------------------------------------

-- MySQL has no `ADD COLUMN IF NOT EXISTS`, so the ALTER is guarded on information_schema — the
-- idiom 0008 and 0009 use, and what keeps the forward-only recovery path (DELETE the
-- schema_migrations row, redeploy) able to re-run this file cleanly.

SET @column_exists := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'follow_ups'
    AND COLUMN_NAME = 'reminder_failed_at'
);

SET @add_column := IF(@column_exists = 0,
  'ALTER TABLE follow_ups ADD COLUMN reminder_failed_at TIMESTAMP NULL DEFAULT NULL AFTER completed_at',
  'DO 0'
);

PREPARE stmt FROM @add_column;

EXECUTE stmt;

DEALLOCATE PREPARE stmt;
