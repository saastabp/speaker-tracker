-- 0006_targets.sql — Speaker Tracker schema slice 5: activity targets.
--
-- One entity table. `targets` holds Donna's goals — a `goal_count` per (target_type, cadence),
-- e.g. outreaches/week, pitches/month, bookings/quarter. The `target_types` catalog
-- (venues_researched, outreaches, pitches, bookings) is already seeded in 0001, so this migration
-- is the entity table only. Nothing is seeded — Donna sets her own targets.
--
-- The dashboard (slice 5) compares these goals against actuals computed on the fly (DATABASE.md §4):
-- outreaches filtered by `outreach_kinds.counts_toward_target`, research-ready organizations,
-- and status_events, all bucketed by cadence in the user's timezone.
--
-- Design notes:
--   * `cadence` is an ENUM, not a catalog — a fixed 3-value vocabulary with no extra columns and no
--     prospect of user extension (DATABASE.md §5 deliberate deviation; job-tracker made the same call).
--   * UNIQUE(user_id, target_type_id, cadence) is the key the `PUT /targets` upsert
--     (ON DUPLICATE KEY UPDATE) depends on (DATABASE.md §"targets").
--   * No `deleted_at`: a target is a config scalar, not journaled history. Unset = a hard DELETE of
--     the row; soft-delete would collide with the upsert UNIQUE (a tombstone would block re-setting
--     the same target). This is the same call as the other config tables.
--
-- Every statement is idempotent (CREATE TABLE IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS targets (
  id             BIGINT                                   NOT NULL AUTO_INCREMENT,
  user_id        BIGINT                                   NOT NULL,
  target_type_id BIGINT                                   NOT NULL,
  cadence        ENUM('weekly', 'monthly', 'quarterly')  NOT NULL,
  goal_count     INT                                      NOT NULL,
  created_at     TIMESTAMP                                NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     TIMESTAMP                                NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_targets_user_type_cadence (user_id, target_type_id, cadence),
  CONSTRAINT fk_targets_user FOREIGN KEY (user_id) REFERENCES users (id),
  CONSTRAINT fk_targets_target_type FOREIGN KEY (target_type_id) REFERENCES target_types (id),
  CONSTRAINT ck_targets_goal_count_non_negative CHECK (goal_count >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;