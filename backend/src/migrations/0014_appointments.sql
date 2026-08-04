-- 0014_appointments.sql — Speaker Tracker: logged appointments (not a calendar).
--
-- One table. An appointment is a *scheduled meeting with a person* that Donna records so it shows
-- up on the Dashboard's "Coming up" card. It is deliberately NOT a calendaring feature: nothing
-- syncs, nothing invites, nothing emails. That is what separates it from the two tables it would
-- otherwise resemble — `follow_ups` is a reminder the app sends *to Donna*, and `outreaches`
-- records a touch that already happened. An appointment is neither: it is a future commitment to
-- another person, and the only thing the app does with it is show it.
--
-- Four decisions are baked into the columns below:
--
--   1. `scheduled_at` IS A DATETIME, NOT A TIMESTAMP. Every other instant in this schema is a
--      TIMESTAMP, which MySQL converts to UTC on write and back on read using the session zone.
--      That is right for "when did this happen", and wrong here: an appointment is a wall-clock
--      commitment — 2pm Tuesday is 2pm Tuesday — and a DATETIME is stored and returned verbatim,
--      so no session-zone change can slide it. Same reasoning that made `follow_ups.due_date` a
--      DATE rather than a timestamp.
--   2. `contact_id` IS NOT NULL. An appointment is with someone; that is the whole shape of it.
--      Unlike `follow_ups`, which needs both links nullable and a CHECK to keep at least one, there
--      is exactly one link here and it is required, so the FK alone is the guarantee.
--   3. `title` IS REQUIRED, `details` IS NOT. The title is the label every surface renders — the
--      "Coming up" row, the list row, the contact panel — so a row without one would appear as a
--      blank line on the Dashboard. The free-text block is where the substance goes, but an
--      appointment with a person, a time and a name is already useful, so an empty `details` is
--      recorded rather than rejected.
--   4. NO STATUS OR COMPLETION COLUMN. Past-versus-upcoming is `scheduled_at` against now, and
--      nothing else. An appointment is not marked done — it simply stops being upcoming, which is
--      why the Dashboard needs no filter beyond the date and the page's toggle is a pure read.
--
-- No ON DELETE behaviour on the contact link: contacts are soft-deleted (deleted_at), never
-- removed, so a cascade would be dead code that quietly becomes load-bearing if anyone ever
-- hard-deletes.
--
-- Idempotent (CREATE TABLE IF NOT EXISTS), so the forward-only recovery path — DELETE the
-- `schema_migrations` row, redeploy — re-runs this file cleanly.

CREATE TABLE IF NOT EXISTS appointments (
  id           BIGINT       NOT NULL AUTO_INCREMENT,
  user_id      BIGINT       NOT NULL,
  contact_id   BIGINT       NOT NULL,        -- an appointment is always with someone
  title        VARCHAR(255) NOT NULL,        -- the display label on every surface (decision 3)
  scheduled_at DATETIME     NOT NULL,        -- wall clock, never zone-converted (decision 1)
  details      TEXT         NULL,            -- free-text; optional (decision 3)
  created_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted_at   TIMESTAMP    NULL DEFAULT NULL,
  PRIMARY KEY (id),
  -- The two reads this table has: the page's list (this user, in time order, sliced upcoming or
  -- past) and the Dashboard's "Coming up" branch, which asks the same question with a LIMIT.
  KEY ix_appointments_user_scheduled (user_id, scheduled_at),
  -- InnoDB requires an index on every referencing column and auto-creates an unnamed one if it is
  -- absent; naming it means the contact panel's lookup comes from an index we chose.
  KEY ix_appointments_contact_scheduled (contact_id, scheduled_at),
  CONSTRAINT fk_appointments_user FOREIGN KEY (user_id) REFERENCES users (id),
  CONSTRAINT fk_appointments_contact FOREIGN KEY (contact_id) REFERENCES contacts (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;