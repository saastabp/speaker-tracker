-- 0009_external_message_id.sql — record the Message-ID the *recipient* sees, which is not the one
-- we mint.
--
-- Found in live testing 2026-07-29 (slice 6b checkpoint K). SES **replaces** the RFC 5322
-- `Message-ID` header on the way out:
--
--     we minted:      <72b96991f1a84dc9aa661903fb907f50@360balancedliving.com>
--     gmail received: <0100019fb1ccf4d8-...-000000@email.amazonses.com>
--
-- Every external reply therefore points `In-Reply-To` at an identifier that existed nowhere in our
-- database, so `repositories.email_matching.threads_by_message_id` never matched and the header
-- chain — which `core/email_threading.py` calls the only strategy DESIGN.md §3 relies on for
-- correctness — has never once worked for a thread the app originated. Acceptance #1 passed only
-- because a *tracked contact's* reply is ingested by the sender match instead; a reply from anyone
-- not already on file was silently dropped.
--
-- Two identifiers, two columns, because they are two different facts:
--
--   message_id           The id we mint BEFORE the send. Stays the UNIQUE(user_id, message_id)
--                        idempotency key, so the intent-first write is still durable before SES is
--                        contacted, and it is what the Sent-folder APPEND carries.
--   external_message_id  The header as actually sent. NULL for inbound mail and for anything we
--                        did not originate. Populated at confirm time from the provider's id.
--
-- Deliberately NOT named `ses_message_id`. The concept is "the Message-ID the recipient saw" — a
-- host-agnostic fact. Only its derivation is SES-specific, and that lives in one function in
-- `common/mail.py` for a future mail host to replace. Naming the column after the vendor would
-- bake this deployment into the schema.
--
-- Not UNIQUE: a provider could in principle reuse or omit one, and a duplicate here should never be
-- able to block a send. The uniqueness guarantee stays on `message_id`, which we control.
--
-- MySQL has no `ADD COLUMN IF NOT EXISTS`, so the ALTER is guarded on information_schema — the same
-- idiom 0008 uses for its deferred FK, and what keeps the runner's forward-only recovery (DELETE
-- the schema_migrations row, redeploy) able to re-run this file cleanly.

SET @column_exists := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'email_messages'
    AND COLUMN_NAME = 'external_message_id'
);

SET @add_column := IF(@column_exists = 0,
  'ALTER TABLE email_messages ADD COLUMN external_message_id VARCHAR(255) NULL AFTER message_id',
  'DO 0'
);

PREPARE stmt FROM @add_column;

EXECUTE stmt;

DEALLOCATE PREPARE stmt;

-- Indexed because it is looked up on every polled message that carries a threading header —
-- `threads_by_message_id` now matches an incoming In-Reply-To/References against either column.
SET @index_exists := (
  SELECT COUNT(*) FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'email_messages'
    AND INDEX_NAME = 'ix_email_messages_external_message_id'
);

SET @add_index := IF(@index_exists = 0,
  'CREATE INDEX ix_email_messages_external_message_id ON email_messages (external_message_id)',
  'DO 0'
);

PREPARE stmt FROM @add_index;

EXECUTE stmt;

DEALLOCATE PREPARE stmt;