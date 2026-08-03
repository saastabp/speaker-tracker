-- 0012_talks_materials.sql — the materials library, and a talk duration a human can write.
--
-- **Takes the number `0011` reserved for materials**, but carries the `talks` change too: both
-- belong to slice 9, and the runner applies files in version order, so a second file for one column
-- would only add a hole to reason about. `DATABASE.md`'s migration table is updated to match.
--
-- ---------------------------------------------------------------------------
-- talks.length_minutes -> talks.duration
-- ---------------------------------------------------------------------------
-- `length_minutes` was an INT, and the approved card shows "45–60 min" and "flexible length".
-- Neither is a number. Nothing in the app computes on a duration — it is never summed, sorted or
-- compared — so the column becomes free text and the user writes what they mean.
--
-- The existing values are preserved rather than dropped: an INT of 45 becomes "45 min", which is
-- what it always meant. Done before the column is removed, so the migration is not lossy.

ALTER TABLE talks ADD COLUMN duration VARCHAR(64) NULL AFTER title;

UPDATE talks SET duration = CONCAT(length_minutes, ' min') WHERE length_minutes IS NOT NULL;

ALTER TABLE talks DROP COLUMN length_minutes;

-- ---------------------------------------------------------------------------
-- materials — the reusable file library (DATABASE.md §"talks / materials")
-- ---------------------------------------------------------------------------
-- One row per uploaded file. The bytes live in S3 under `materials/`; this table is the index, so
-- a listing never has to walk the bucket and a file keeps a display name independent of its key.
--
-- `talk_id` is nullable so a general one-sheet can exist without belonging to a specific talk —
-- the page lists them flat, and the link is what lets a later view group them by talk.
--
-- **A sent email does not depend on this row.** Attaching a material copies its bytes into the
-- message: `mail.build_raw_message` takes attachment *bytes* and embeds them as MIME parts, and
-- the assembled message is stored whole under `email/raw/`. So a material can be renamed, replaced
-- or removed freely — nothing already sent changes, because nothing already sent points here.
-- That is what makes the library editable rather than append-only.
--
-- Soft delete is therefore about **recovering a mistake**, not protecting history: a removed
-- material is a file the user may not easily re-source, and every other entity in this app hides
-- rather than destroys. The object stays in S3 precisely so undelete remains possible.
--
-- `s3_key` is UNIQUE per user: two rows pointing at one object would let a delete of either strand
-- the other. Replacing a material's file writes a **new** key rather than overwriting, so an
-- upload that fails half-way cannot leave the row pointing at a truncated object.

CREATE TABLE IF NOT EXISTS materials (
  id           BIGINT       NOT NULL AUTO_INCREMENT,
  user_id      BIGINT       NOT NULL,
  talk_id      BIGINT       NULL,
  name         VARCHAR(255) NOT NULL,
  s3_key       VARCHAR(512) NOT NULL,
  content_type VARCHAR(255) NOT NULL,
  size_bytes   BIGINT       NOT NULL,
  sort_order   INT          NOT NULL DEFAULT 0,
  created_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted_at   TIMESTAMP    NULL DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_materials_user_key (user_id, s3_key),
  KEY ix_materials_user_sort (user_id, sort_order),
  KEY ix_materials_talk (talk_id),
  CONSTRAINT fk_materials_user FOREIGN KEY (user_id) REFERENCES users (id),
  CONSTRAINT fk_materials_talk FOREIGN KEY (talk_id) REFERENCES talks (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;