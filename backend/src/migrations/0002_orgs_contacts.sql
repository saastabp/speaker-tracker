-- 0002_orgs_contacts.sql — Speaker Tracker schema slice 2: organizations, contacts, and
-- their many-to-many affiliation.
--
-- First entity tables with foreign keys. FK constraints are declared; rows are soft-deleted
-- via deleted_at, never hard-deleted, so the default RESTRICT never fires in normal use but
-- guards against orphaning bugs. contact_organizations is the exception — a lean join row that
-- is genuinely deleted when an affiliation is removed, so it carries no deleted_at.
--
-- Every statement is idempotent (CREATE TABLE IF NOT EXISTS). No venues or contacts are seeded
-- in any environment — Donna enters them as she researches (DEV-PLAN slice 2).

-- ---------------------------------------------------------------------------
-- organizations — venues, podcasts, expos. Kindling research columns are structured, not a
-- notes blob; research-readiness (core/research.py) is computed from them.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS organizations (
  id                   BIGINT       NOT NULL AUTO_INCREMENT,
  user_id              BIGINT       NOT NULL,
  organization_type_id BIGINT       NOT NULL,
  name                 VARCHAR(255) NOT NULL,
  location             VARCHAR(255) NULL,
  website_url          VARCHAR(512) NULL,
  email_domain         VARCHAR(255) NULL,
  what_it_is           TEXT         NULL,
  why_it_fits          TEXT         NULL,
  how_to_approach      TEXT         NULL,
  notes                TEXT         NULL,
  created_at           TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at           TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted_at           TIMESTAMP    NULL DEFAULT NULL,
  -- Active-name uniqueness per user: name_key is NULL for soft-deleted rows (multiple NULLs are
  -- allowed), so a deleted org's name can be reused while two live orgs cannot share a name.
  name_key             VARCHAR(255) GENERATED ALWAYS AS (IF(deleted_at IS NULL, name, NULL)) STORED,
  PRIMARY KEY (id),
  UNIQUE KEY uq_organizations_user_active_name (user_id, name_key),
  KEY ix_organizations_user_name (user_id, name),
  KEY ix_organizations_user_type (user_id, organization_type_id),
  KEY ix_organizations_user_email_domain (user_id, email_domain),
  CONSTRAINT fk_organizations_user FOREIGN KEY (user_id) REFERENCES users (id),
  CONSTRAINT fk_organizations_type FOREIGN KEY (organization_type_id) REFERENCES organization_types (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- contacts — the person. No organization_id: affiliation lives in contact_organizations,
-- because one person is frequently the contact for several venues. (user_id, email) is a
-- load-bearing index — the IMAP poller (slice 6b) resolves every inbound From against it.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS contacts (
  id               BIGINT       NOT NULL AUTO_INCREMENT,
  user_id          BIGINT       NOT NULL,
  warmth_tier_id   BIGINT       NULL,
  name             VARCHAR(255) NOT NULL,
  email            VARCHAR(320) NULL,
  phone            VARCHAR(64)  NULL,
  source           VARCHAR(255) NULL,
  how_you_know     TEXT         NULL,
  notes            TEXT         NULL,
  created_at       TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at       TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted_at       TIMESTAMP    NULL DEFAULT NULL,
  PRIMARY KEY (id),
  KEY ix_contacts_user_email (user_id, email),
  KEY ix_contacts_user_name (user_id, name),
  CONSTRAINT fk_contacts_user FOREIGN KEY (user_id) REFERENCES users (id),
  CONSTRAINT fk_contacts_warmth FOREIGN KEY (warmth_tier_id) REFERENCES warmth_tiers (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- contact_organizations — many-to-many affiliation carrying the per-venue role attributes:
-- title, the primary-contact flag, and the power-partner flag (power-partnership is scoped to a
-- specific venue, not the person). UNIQUE (contact_id, organization_id) is what makes the
-- add-contact dedupe safe: a second venue for an existing person is a new affiliation, never a
-- duplicate contact. Hard-deleted on removal.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS contact_organizations (
  id               BIGINT       NOT NULL AUTO_INCREMENT,
  contact_id       BIGINT       NOT NULL,
  organization_id  BIGINT       NOT NULL,
  title            VARCHAR(255) NULL,
  is_primary       BOOLEAN      NOT NULL DEFAULT FALSE,
  is_power_partner BOOLEAN      NOT NULL DEFAULT FALSE,
  created_at       TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at       TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_contact_organizations_contact_org (contact_id, organization_id),
  KEY ix_contact_organizations_org (organization_id),
  CONSTRAINT fk_contact_organizations_contact FOREIGN KEY (contact_id) REFERENCES contacts (id),
  CONSTRAINT fk_contact_organizations_org FOREIGN KEY (organization_id) REFERENCES organizations (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;