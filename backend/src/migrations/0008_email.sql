-- 0008_email.sql — Speaker Tracker schema slice 6a: the email send path (threads, messages, the
-- IMAP poll cursors that 6b shares), the per-user styled email signature, and the deferred
-- outreaches -> email_messages foreign key.
--
-- Five things, created in FK dependency order:
--   1. email_threads — thread identity, assigned once at ingest (DATABASE.md §"email_threads").
--      contact_id / opportunity_id are both NULLABLE (unknown-sender, side-channel, or gig mail).
--   2. email_messages — one row per sent/received message. UNIQUE(user_id, message_id) is the
--      idempotency key for the whole poller; `references` is reserved so the column is
--      message_references; direction is an ENUM (§5). No deleted_at — messages are immutable
--      records, not soft-deletable entities.
--   3. imap_folder_cursors — per-folder poll watermark, UNIQUE(user_id, folder_name). Created here
--      (6a) though the poller that advances it is 6b (DATABASE.md §6 migration plan).
--   4. signatures — the per-user, fully-styled (HTML) email signature composed in the Tiptap editor
--      and appended by the composer. is_default marks the one used by default; a single default is
--      enforced in the repo (setting one clears the others), so name + is_default already support
--      multiple signatures later with no further migration.
--   5. outreaches.email_message_id FK — the ALTER deferred from 0005 (the column has existed since
--      then with no constraint, because email_messages did not exist until now). ON DELETE SET NULL,
--      per the 0005 header and the ERD.
--
-- Every table statement is idempotent (CREATE TABLE IF NOT EXISTS). The FK ALTER is not
-- expressible with IF NOT EXISTS, so it is added LAST and guarded by an information_schema check
-- (MySQL's standard idempotent-ALTER idiom) so the forward-only recovery path (DELETE the
-- schema_migrations row, redeploy) re-runs the whole file cleanly.

-- ---------------------------------------------------------------------------
-- email_threads — thread identity assigned once at ingest. subject_normalized (Re:/Fwd: stripped)
-- is the fallback grouping key. Threads close explicitly; nothing infers an owed reply.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS email_threads (
  id                 BIGINT           NOT NULL AUTO_INCREMENT,
  user_id            BIGINT           NOT NULL,
  contact_id         BIGINT           NULL,
  opportunity_id     BIGINT           NULL,
  subject_normalized VARCHAR(255)     NOT NULL,
  last_direction     ENUM('out','in') NOT NULL,
  last_message_at    TIMESTAMP        NULL DEFAULT NULL,
  last_read_at       TIMESTAMP        NULL DEFAULT NULL,
  closed_at          TIMESTAMP        NULL DEFAULT NULL,
  created_at         TIMESTAMP        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at         TIMESTAMP        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted_at         TIMESTAMP        NULL DEFAULT NULL,
  PRIMARY KEY (id),
  KEY ix_email_threads_user_closed (user_id, closed_at, last_message_at),
  KEY ix_email_threads_contact (contact_id),
  KEY ix_email_threads_opportunity (opportunity_id),
  CONSTRAINT fk_email_threads_user FOREIGN KEY (user_id) REFERENCES users (id),
  CONSTRAINT fk_email_threads_contact FOREIGN KEY (contact_id) REFERENCES contacts (id),
  CONSTRAINT fk_email_threads_opportunity FOREIGN KEY (opportunity_id) REFERENCES opportunities (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- email_messages — one row per message. UNIQUE(user_id, message_id) makes a re-dragged email or an
-- overlapping poll structurally incapable of double-inserting. message_references (reserved word).
-- direction ENUM. s3_key points at the raw MIME. Immutable — no deleted_at.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS email_messages (
  id                 BIGINT           NOT NULL AUTO_INCREMENT,
  user_id            BIGINT           NOT NULL,
  thread_id          BIGINT           NOT NULL,
  contact_id         BIGINT           NULL,
  opportunity_id     BIGINT           NULL,
  message_id         VARCHAR(255)     NOT NULL,
  in_reply_to        VARCHAR(255)     NULL,
  message_references TEXT             NULL,
  direction          ENUM('out','in') NOT NULL,
  subject            VARCHAR(255)     NULL,
  from_addr          VARCHAR(255)     NOT NULL,
  to_addr            TEXT             NULL,
  cc_addr            TEXT             NULL,
  s3_key             VARCHAR(255)     NULL,
  imap_folder        VARCHAR(255)     NULL,
  imap_uid           BIGINT           NULL,
  sent_at            TIMESTAMP        NULL DEFAULT NULL,
  received_at        TIMESTAMP        NULL DEFAULT NULL,
  created_at         TIMESTAMP        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at         TIMESTAMP        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_email_messages_user_message (user_id, message_id),
  KEY ix_email_messages_thread (thread_id, (COALESCE(sent_at, received_at))),
  KEY ix_email_messages_in_reply_to (in_reply_to),
  CONSTRAINT fk_email_messages_user FOREIGN KEY (user_id) REFERENCES users (id),
  CONSTRAINT fk_email_messages_thread FOREIGN KEY (thread_id) REFERENCES email_threads (id),
  CONSTRAINT fk_email_messages_contact FOREIGN KEY (contact_id) REFERENCES contacts (id),
  CONSTRAINT fk_email_messages_opportunity FOREIGN KEY (opportunity_id) REFERENCES opportunities (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- imap_folder_cursors — per-folder poll watermark. Each poll fetches only UIDs above last_seen_uid.
-- uid_validity guards against the server recreating a folder (the cursor must be reset, not trusted).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS imap_folder_cursors (
  id             BIGINT       NOT NULL AUTO_INCREMENT,
  user_id        BIGINT       NOT NULL,
  folder_name    VARCHAR(255) NOT NULL,
  uid_validity   BIGINT       NULL,
  last_seen_uid  BIGINT       NOT NULL DEFAULT 0,
  last_polled_at TIMESTAMP    NULL DEFAULT NULL,
  created_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_imap_folder_cursors_user_folder (user_id, folder_name),
  CONSTRAINT fk_imap_folder_cursors_user FOREIGN KEY (user_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- signatures — per-user styled (HTML) email signature. body_html is the Tiptap editor output.
-- is_default marks the composer's default; the repo enforces a single default per user (setting a
-- new one clears the others). name + is_default keep the door open to multiple signatures later.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signatures (
  id         BIGINT       NOT NULL AUTO_INCREMENT,
  user_id    BIGINT       NOT NULL,
  name       VARCHAR(255) NOT NULL,
  body_html  MEDIUMTEXT   NOT NULL,
  is_default BOOLEAN      NOT NULL DEFAULT FALSE,
  created_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted_at TIMESTAMP    NULL DEFAULT NULL,
  PRIMARY KEY (id),
  KEY ix_signatures_user (user_id),
  CONSTRAINT fk_signatures_user FOREIGN KEY (user_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- outreaches.email_message_id FK (deferred from 0005). MySQL has no ADD CONSTRAINT IF NOT EXISTS,
-- so guard on information_schema and run the ALTER only when the constraint is absent — idempotent
-- for the runner's forward-only recovery. Kept LAST so nothing follows a non-CREATE statement.
-- ---------------------------------------------------------------------------
SET @fk_exists := (
  SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
  WHERE CONSTRAINT_SCHEMA = DATABASE()
    AND TABLE_NAME = 'outreaches'
    AND CONSTRAINT_NAME = 'fk_outreaches_email_message'
);

SET @add_fk := IF(@fk_exists = 0,
  'ALTER TABLE outreaches ADD CONSTRAINT fk_outreaches_email_message FOREIGN KEY (email_message_id) REFERENCES email_messages (id) ON DELETE SET NULL',
  'DO 0'
);

PREPARE stmt FROM @add_fk;

EXECUTE stmt;

DEALLOCATE PREPARE stmt;