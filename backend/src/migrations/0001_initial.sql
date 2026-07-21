-- 0001_initial.sql — Speaker Tracker schema slice 1.
--
-- Creates the tenant root (users) and every catalog vocabulary with its seed rows.
-- Entity tables whose catalogs live here (organizations, opportunities, outreaches, …)
-- arrive in later migrations; their vocabularies are seeded now so catalog changes stay
-- in one place (DATABASE.md §6). schema_migrations is intentionally absent — the runner
-- bootstraps it, since it must query that table to decide whether this file has run.
--
-- Every statement is idempotent: re-running the file is a no-op. Reference data only —
-- no venues/contacts/opportunities are seeded in any environment.

-- ---------------------------------------------------------------------------
-- Tenant root
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
  id           BIGINT       NOT NULL AUTO_INCREMENT,
  cognito_sub  VARCHAR(255) NOT NULL,
  email        VARCHAR(320) NOT NULL,
  display_name VARCHAR(255) NULL,
  timezone     VARCHAR(64)  NOT NULL DEFAULT 'Pacific/Honolulu',
  created_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted_at   TIMESTAMP    NULL DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_users_cognito_sub (cognito_sub)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- Catalog tables. Shared shape: id / short_name UK / description / sort_order /
-- created_at / updated_at / deleted_at. Extra columns noted per table (DATABASE.md §3).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS organization_types (
  id          BIGINT       NOT NULL AUTO_INCREMENT,
  short_name  VARCHAR(64)  NOT NULL,
  description VARCHAR(255) NOT NULL,
  sort_order  INT          NOT NULL DEFAULT 0,
  created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted_at  TIMESTAMP    NULL DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_organization_types_short_name (short_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS warmth_tiers (
  id          BIGINT       NOT NULL AUTO_INCREMENT,
  short_name  VARCHAR(64)  NOT NULL,
  description VARCHAR(255) NOT NULL,
  sort_order  INT          NOT NULL DEFAULT 0,
  created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted_at  TIMESTAMP    NULL DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_warmth_tiers_short_name (short_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS contact_roles (
  id          BIGINT       NOT NULL AUTO_INCREMENT,
  short_name  VARCHAR(64)  NOT NULL,
  description VARCHAR(255) NOT NULL,
  sort_order  INT          NOT NULL DEFAULT 0,
  created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted_at  TIMESTAMP    NULL DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_contact_roles_short_name (short_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS opportunity_formats (
  id          BIGINT       NOT NULL AUTO_INCREMENT,
  short_name  VARCHAR(64)  NOT NULL,
  description VARCHAR(255) NOT NULL,
  sort_order  INT          NOT NULL DEFAULT 0,
  created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted_at  TIMESTAMP    NULL DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_opportunity_formats_short_name (short_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Extra: is_terminal (gates the closed_at predicate, DATABASE.md §4).
CREATE TABLE IF NOT EXISTS opportunity_statuses (
  id          BIGINT       NOT NULL AUTO_INCREMENT,
  short_name  VARCHAR(64)  NOT NULL,
  description VARCHAR(255) NOT NULL,
  sort_order  INT          NOT NULL DEFAULT 0,
  is_terminal BOOLEAN      NOT NULL DEFAULT FALSE,
  created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted_at  TIMESTAMP    NULL DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_opportunity_statuses_short_name (short_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS comp_types (
  id          BIGINT       NOT NULL AUTO_INCREMENT,
  short_name  VARCHAR(64)  NOT NULL,
  description VARCHAR(255) NOT NULL,
  sort_order  INT          NOT NULL DEFAULT 0,
  created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted_at  TIMESTAMP    NULL DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_comp_types_short_name (short_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Extra: is_settled (catalog-drives the closed_at money gate, DATABASE.md §4).
CREATE TABLE IF NOT EXISTS payment_statuses (
  id          BIGINT       NOT NULL AUTO_INCREMENT,
  short_name  VARCHAR(64)  NOT NULL,
  description VARCHAR(255) NOT NULL,
  sort_order  INT          NOT NULL DEFAULT 0,
  is_settled  BOOLEAN      NOT NULL DEFAULT FALSE,
  created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted_at  TIMESTAMP    NULL DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_payment_statuses_short_name (short_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Extra: counts_toward_target (metric SQL joins this; never hardcodes short_name — §3).
CREATE TABLE IF NOT EXISTS outreach_kinds (
  id                   BIGINT       NOT NULL AUTO_INCREMENT,
  short_name           VARCHAR(64)  NOT NULL,
  description          VARCHAR(255) NOT NULL,
  sort_order           INT          NOT NULL DEFAULT 0,
  counts_toward_target BOOLEAN      NOT NULL DEFAULT FALSE,
  created_at           TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at           TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted_at           TIMESTAMP    NULL DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_outreach_kinds_short_name (short_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS outreach_channels (
  id          BIGINT       NOT NULL AUTO_INCREMENT,
  short_name  VARCHAR(64)  NOT NULL,
  description VARCHAR(255) NOT NULL,
  sort_order  INT          NOT NULL DEFAULT 0,
  created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted_at  TIMESTAMP    NULL DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_outreach_channels_short_name (short_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS target_types (
  id          BIGINT       NOT NULL AUTO_INCREMENT,
  short_name  VARCHAR(64)  NOT NULL,
  description VARCHAR(255) NOT NULL,
  sort_order  INT          NOT NULL DEFAULT 0,
  created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted_at  TIMESTAMP    NULL DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_target_types_short_name (short_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- Catalog seeds. Idempotent on short_name; re-running refreshes description/flags
-- but never touches id, so foreign keys assigned later stay stable.
-- ---------------------------------------------------------------------------
INSERT INTO organization_types (short_name, description, sort_order) VALUES
  ('retreat_venue',  'Retreat Venue',   10),
  ('resort',         'Resort',          20),
  ('yoga_studio',    'Yoga Studio',     30),
  ('spa',            'Spa',             40),
  ('womens_network', 'Women''s Network', 50),
  ('podcast',        'Podcast',         60),
  ('expo',           'Expo',            70),
  ('corporate',      'Corporate',       80),
  ('other',          'Other',           90)
AS v ON DUPLICATE KEY UPDATE description = v.description, sort_order = v.sort_order;

INSERT INTO warmth_tiers (short_name, description, sort_order) VALUES
  ('cold',     'Cold',     10),
  ('lukewarm', 'Lukewarm', 20),
  ('warm',     'Warm',     30)
AS v ON DUPLICATE KEY UPDATE description = v.description, sort_order = v.sort_order;

INSERT INTO contact_roles (short_name, description, sort_order) VALUES
  ('primary',     'Primary',     10),
  ('introducer',  'Introducer',  20),
  ('coordinator', 'Coordinator', 30),
  ('backup',      'Backup',      40)
AS v ON DUPLICATE KEY UPDATE description = v.description, sort_order = v.sort_order;

INSERT INTO opportunity_formats (short_name, description, sort_order) VALUES
  ('workshop',     'Workshop',     10),
  ('keynote',      'Keynote',      20),
  ('podcast_spot', 'Podcast Spot', 30),
  ('expo_table',   'Expo Table',   40),
  ('panel',        'Panel',        50),
  ('other',        'Other',        60)
AS v ON DUPLICATE KEY UPDATE description = v.description, sort_order = v.sort_order;

INSERT INTO opportunity_statuses (short_name, description, sort_order, is_terminal) VALUES
  ('researching',     'Researching',    10, FALSE),
  ('outreach_sent',   'Outreach Sent',  20, FALSE),
  ('in_conversation', 'In Conversation', 30, FALSE),
  ('pitched',         'Pitched',        40, FALSE),
  ('booked',          'Booked',         50, FALSE),
  ('delivered',       'Delivered',      60, TRUE),
  ('nurture',         'Nurture',        70, FALSE),
  ('cancelled',       'Cancelled',      80, TRUE),
  ('lost',            'Lost / Passed',  90, TRUE)
AS v ON DUPLICATE KEY UPDATE
  description = v.description, sort_order = v.sort_order, is_terminal = v.is_terminal;

INSERT INTO comp_types (short_name, description, sort_order) VALUES
  ('paid',     'Paid',     10),
  ('pro_bono', 'Pro Bono', 20),
  ('trade',    'Trade',    30)
AS v ON DUPLICATE KEY UPDATE description = v.description, sort_order = v.sort_order;

INSERT INTO payment_statuses (short_name, description, sort_order, is_settled) VALUES
  ('unbilled', 'Unbilled', 10, FALSE),
  ('invoiced', 'Invoiced', 20, FALSE),
  ('partial',  'Partial',  30, FALSE),
  ('paid',     'Paid',     40, TRUE),
  ('n_a',      'N/A',      50, TRUE)
AS v ON DUPLICATE KEY UPDATE
  description = v.description, sort_order = v.sort_order, is_settled = v.is_settled;

INSERT INTO outreach_kinds (short_name, description, sort_order, counts_toward_target) VALUES
  ('initial',        'Initial',        10, TRUE),
  ('follow_up',      'Follow-up',      20, TRUE),
  ('correspondence', 'Correspondence', 30, FALSE)
AS v ON DUPLICATE KEY UPDATE
  description = v.description, sort_order = v.sort_order,
  counts_toward_target = v.counts_toward_target;

INSERT INTO outreach_channels (short_name, description, sort_order) VALUES
  ('email',     'Email',     10),
  ('dm',        'DM',        20),
  ('call',      'Call',      30),
  ('in_person', 'In Person', 40),
  ('text',      'Text',      50)
AS v ON DUPLICATE KEY UPDATE description = v.description, sort_order = v.sort_order;

INSERT INTO target_types (short_name, description, sort_order) VALUES
  ('venues_researched', 'Venues Researched', 10),
  ('outreaches',        'Outreaches',        20),
  ('pitches',           'Pitches',           30),
  ('bookings',          'Bookings',          40)
AS v ON DUPLICATE KEY UPDATE description = v.description, sort_order = v.sort_order;
