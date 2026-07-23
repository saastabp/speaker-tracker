-- 0005_outreach.sql — Speaker Tracker schema slice 4: the outbound outreach journal and the
-- message-template library.
--
-- Three things, created in FK dependency order:
--   1. message_template_kinds — the *purpose* catalog deferred from 0001 (DATABASE.md §5). Its
--      values (a purpose/audience axis) weren't settled until message_templates was designed, so
--      it lands here rather than alongside the other catalogs. This is the ONE catalog not in 0001.
--   2. message_templates — the reusable outreach copy. Two orthogonal axes in separate columns:
--      message_template_kind_id (purpose) and channel_id -> outreach_channels (how it is sent).
--      Seeded with the three strategy-doc templates as SHARED rows (user_id IS NULL) — reference
--      content Donna actually sends, the one seeding exception alongside the catalogs (DEV-PLAN
--      slice 4). Everything else in this file is entity tables only.
--   3. outreaches — append-only journal of OUTBOUND touches, logged against the contact and
--      decoupled from pipeline stage (DATABASE.md §"outreaches").
--
-- email_message_id on outreaches is a nullable column with NO foreign key yet: email_messages does
-- not exist until 0007. The FK is added by an ALTER in 0007 when its target table is created; until
-- then the column is always NULL (only the email poller sets it). Matches the documented ERD.
--
-- Every statement is idempotent — CREATE TABLE IF NOT EXISTS, catalog seed via ON DUPLICATE KEY,
-- and the template seeds guarded by NOT EXISTS — so the forward-only recovery path (DELETE the
-- schema_migrations row, redeploy) never double-seeds.

-- ---------------------------------------------------------------------------
-- message_template_kinds — the template PURPOSE catalog (deferred from 0001). Standard catalog
-- shape. `power_partner` is an audience here, never a channel: the channel/purpose split lives in
-- message_templates below (DATABASE.md §5).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS message_template_kinds (
  id          BIGINT       NOT NULL AUTO_INCREMENT,
  short_name  VARCHAR(64)  NOT NULL,
  description VARCHAR(255) NOT NULL,
  sort_order  INT          NOT NULL DEFAULT 0,
  created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted_at  TIMESTAMP    NULL DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_message_template_kinds_short_name (short_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT INTO message_template_kinds (short_name, description, sort_order) VALUES
  ('cold_pitch',          'Cold Pitch',          10),
  ('power_partner_intro', 'Power-Partner Intro', 20)
AS v ON DUPLICATE KEY UPDATE description = v.description, sort_order = v.sort_order;

-- ---------------------------------------------------------------------------
-- message_templates — reusable outreach copy. user_id NULL = shared/reference (editable in place;
-- Duplicate writes a personal copy with user_id set). subject is NULL for DM templates. body holds
-- merge fields (e.g. [Name]) resolved client-side for the copy-to-clipboard flow.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS message_templates (
  id                       BIGINT       NOT NULL AUTO_INCREMENT,
  user_id                  BIGINT       NULL,          -- NULL = shared reference template
  message_template_kind_id BIGINT       NOT NULL,      -- purpose/audience
  channel_id               BIGINT       NOT NULL,      -- outreach_channels: how it is sent
  name                     VARCHAR(255) NOT NULL,
  subject                  VARCHAR(255) NULL,           -- email subject; NULL for DM templates
  body                     MEDIUMTEXT   NOT NULL,       -- merge fields like [Name]
  created_at               TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at               TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted_at               TIMESTAMP    NULL DEFAULT NULL,
  PRIMARY KEY (id),
  KEY ix_message_templates_user (user_id),
  KEY ix_message_templates_kind (message_template_kind_id),
  KEY ix_message_templates_channel (channel_id),
  CONSTRAINT fk_message_templates_user FOREIGN KEY (user_id) REFERENCES users (id),
  CONSTRAINT fk_message_templates_kind FOREIGN KEY (message_template_kind_id) REFERENCES message_template_kinds (id),
  CONSTRAINT fk_message_templates_channel FOREIGN KEY (channel_id) REFERENCES outreach_channels (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Seed the three strategy-doc templates as shared rows. kind_id/channel_id are resolved from their
-- short_names (auto-increment ids aren't known here). The NOT EXISTS guard is wrapped in a derived
-- table so selecting from the insert target doesn't trip MySQL error 1093, keeping each seed
-- idempotent for the DELETE-row-and-redeploy recovery path. Bracketed placeholders that don't
-- resolve from a contact (e.g. [Your signature], [SPECIFIC THING ...]) are Donna's to fill on paste.
INSERT INTO message_templates (user_id, message_template_kind_id, channel_id, name, subject, body)
SELECT NULL, k.id, c.id, 'Cold DM', NULL,
'Hi [Name],

I''ve been following [your studio / your retreat / your property] and really love [SPECIFIC THING you genuinely appreciate about their content].

I''m a local Kauai-based legacy and wellness coach. I offer guest wellness workshops for women''s retreats and groups. My signature talk is called "I''m Fine Is a Lie." It''s built for women in their 40s and up who are ready to stop putting themselves last and start living the life they''ve been waiting for.

If you ever bring in local speakers or wellness experts for guest experiences, I''d love to be on your radar.

Is that worth a quick conversation?

[Your signature]'
FROM message_template_kinds k
JOIN outreach_channels c ON c.short_name = 'dm'
WHERE k.short_name = 'cold_pitch'
  AND NOT EXISTS (
    SELECT 1 FROM (
      SELECT id FROM message_templates WHERE user_id IS NULL AND name = 'Cold DM'
    ) existing
  );

INSERT INTO message_templates (user_id, message_template_kind_id, channel_id, name, subject, body)
SELECT NULL, k.id, c.id, 'Cold Email',
'Local Kauai Speaker for Your Women''s Retreats & Wellness Events',
'Hi [Name],

My name is Donna King. I''m a legacy and wellness coach based in Kauai, and I work with women in the last third of their lives who are ready to stop saying "I''m fine" and start living the life they''ve quietly been waiting for.

I offer guest wellness workshops designed specifically for women''s retreats, resort wellness programs, and women''s gatherings. My three talks are:

- I''m Fine Is a Lie: permission to stop hiding and start living (45-60 min)
- Legacy in Motion: what does your next chapter actually look like? (45-60 min)
- The Wellness Wheel for Women: an interactive workshop for the whole self (flexible)

If you''re ever looking for a local Kauai speaker to add a meaningful, engaging experience for your guests, I''d love to be considered. I''d be happy to send a brief overview or hop on a 15-minute call to see if there''s a fit.

Thank you for what you do.

Warmly,
Donna King
Legacy & Wellness Coach | 360 Balanced Living
[Website] | [Email] | [Podcast: Legacy in Motion on Spotify]'
FROM message_template_kinds k
JOIN outreach_channels c ON c.short_name = 'email'
WHERE k.short_name = 'cold_pitch'
  AND NOT EXISTS (
    SELECT 1 FROM (
      SELECT id FROM message_templates WHERE user_id IS NULL AND name = 'Cold Email'
    ) existing
  );

INSERT INTO message_templates (user_id, message_template_kind_id, channel_id, name, subject, body)
SELECT NULL, k.id, c.id, 'Power-Partner DM', NULL,
'Hi [Name],

I''ve been thinking about our connection at [BNI / Alignable / wherever] and wanted to reach out directly.

I work with women in their 40s and up who are burned out, running on empty, and quietly putting themselves last. I think the women you work with may be feeling the same way. They''re just not saying it out loud.

Would you be open to a 15-minute chat to see if there''s a natural referral relationship or even a collaboration between what you do and what I do?

[Your signature]'
FROM message_template_kinds k
JOIN outreach_channels c ON c.short_name = 'dm'
WHERE k.short_name = 'power_partner_intro'
  AND NOT EXISTS (
    SELECT 1 FROM (
      SELECT id FROM message_templates WHERE user_id IS NULL AND name = 'Power-Partner DM'
    ) existing
  );

-- ---------------------------------------------------------------------------
-- outreaches — append-only journal of OUTBOUND touches. OUTBOUND ONLY is load-bearing for metrics:
-- inbound mail never creates a row here (it stays visible via the read-time contact-timeline union).
-- occurred_at is user-settable (a touch can be backdated); created_at is not. opportunity_id is
-- nullable (a touch need not belong to a gig). Indexes per DATABASE.md §"outreaches" — the last one
-- (user_id, outreach_kind_id, occurred_at) serves the target rollups.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS outreaches (
  id                  BIGINT    NOT NULL AUTO_INCREMENT,
  user_id             BIGINT    NOT NULL,
  contact_id          BIGINT    NOT NULL,
  opportunity_id      BIGINT    NULL,               -- optional attribution to a gig
  outreach_kind_id    BIGINT    NOT NULL,           -- initial / follow_up / correspondence
  outreach_channel_id BIGINT    NOT NULL,           -- email / dm / call / in_person / text
  message_template_id BIGINT    NULL,               -- template used, if any
  email_message_id    BIGINT    NULL,               -- FK deferred to 0007 (email_messages)
  note                TEXT      NULL,
  occurred_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted_at          TIMESTAMP NULL DEFAULT NULL,
  PRIMARY KEY (id),
  KEY ix_outreaches_user_occurred (user_id, occurred_at),
  KEY ix_outreaches_contact_occurred (contact_id, occurred_at),
  KEY ix_outreaches_user_kind_occurred (user_id, outreach_kind_id, occurred_at),
  CONSTRAINT fk_outreaches_user FOREIGN KEY (user_id) REFERENCES users (id),
  CONSTRAINT fk_outreaches_contact FOREIGN KEY (contact_id) REFERENCES contacts (id),
  CONSTRAINT fk_outreaches_opportunity FOREIGN KEY (opportunity_id) REFERENCES opportunities (id),
  CONSTRAINT fk_outreaches_kind FOREIGN KEY (outreach_kind_id) REFERENCES outreach_kinds (id),
  CONSTRAINT fk_outreaches_channel FOREIGN KEY (outreach_channel_id) REFERENCES outreach_channels (id),
  CONSTRAINT fk_outreaches_template FOREIGN KEY (message_template_id) REFERENCES message_templates (id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;