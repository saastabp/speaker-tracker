-- 0003_pipeline.sql — Speaker Tracker schema slice 3: the opportunity pipeline and its
-- status journal.
--
-- Five tables, created in FK dependency order: talks first (opportunities.talk_id references it,
-- so it cannot come in a later migration — DEV-PLAN slice 3), then opportunities, then the three
-- child tables (opportunity_contacts, opportunity_notes, status_events).
--
-- All pipeline CATALOGS (opportunity_statuses, opportunity_formats, comp_types, payment_statuses,
-- contact_roles) already exist and are seeded in 0001 — this migration is entity tables only.
--
-- current_status_id and closed_at on opportunities are DENORMALIZED: the API writes them in the
-- same transaction as the status/payment change and never recomputes them on read (DATABASE.md §4).
-- No triggers, no generated columns — the invariant lives in core/opportunities.py, not the schema.
--
-- Every statement is idempotent (CREATE TABLE IF NOT EXISTS). Nothing is seeded — Donna creates
-- talks and opportunities as she works the pipeline.

-- ---------------------------------------------------------------------------
-- talks — the reusable offers Donna pitches (workshop, keynote, podcast topic). opportunities.talk_id
-- is a nullable FK ("which talk was offered"), so talks must exist before opportunities.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS talks (
  id             BIGINT       NOT NULL AUTO_INCREMENT,
  user_id        BIGINT       NOT NULL,
  title          VARCHAR(255) NOT NULL,
  length_minutes INT          NULL,
  one_liner      TEXT         NULL,
  sort_order     INT          NOT NULL DEFAULT 0,
  created_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted_at     TIMESTAMP    NULL DEFAULT NULL,
  PRIMARY KEY (id),
  KEY ix_talks_user_sort (user_id, sort_order),
  CONSTRAINT fk_talks_user FOREIGN KEY (user_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- opportunities — one row per gig / podcast spot. current_status_id and closed_at are denormalized
-- (see file header + DATABASE.md §4). Money is first-class incl. pro bono: comp_type_id +
-- fee_amount/currency + payment_status_id + paid_on. The (user_id, closed_at) index drives the
-- board (closed_at IS NULL) / History (closed_at IS NOT NULL) split and is on nearly every query.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS opportunities (
  id                    BIGINT        NOT NULL AUTO_INCREMENT,
  user_id               BIGINT        NOT NULL,
  organization_id       BIGINT        NOT NULL,
  talk_id               BIGINT        NULL,
  opportunity_format_id BIGINT        NOT NULL,
  current_status_id     BIGINT        NOT NULL,
  comp_type_id          BIGINT        NOT NULL,
  payment_status_id     BIGINT        NOT NULL,
  title                 VARCHAR(255)  NOT NULL,
  event_date            DATE          NULL,
  fee_amount            DECIMAL(10,2) NULL,
  currency              CHAR(3)       NOT NULL DEFAULT 'USD',
  paid_on               DATE          NULL,
  angle                 TEXT          NULL,
  outcome               TEXT          NULL,
  closed_at             TIMESTAMP     NULL DEFAULT NULL,
  created_at            TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at            TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted_at            TIMESTAMP     NULL DEFAULT NULL,
  PRIMARY KEY (id),
  KEY ix_opportunities_user_closed (user_id, closed_at),
  KEY ix_opportunities_user_status (user_id, current_status_id),
  KEY ix_opportunities_user_event_date (user_id, event_date),
  KEY ix_opportunities_org (organization_id),
  CONSTRAINT fk_opportunities_user FOREIGN KEY (user_id) REFERENCES users (id),
  CONSTRAINT fk_opportunities_org FOREIGN KEY (organization_id) REFERENCES organizations (id),
  CONSTRAINT fk_opportunities_talk FOREIGN KEY (talk_id) REFERENCES talks (id),
  CONSTRAINT fk_opportunities_format FOREIGN KEY (opportunity_format_id) REFERENCES opportunity_formats (id),
  CONSTRAINT fk_opportunities_status FOREIGN KEY (current_status_id) REFERENCES opportunity_statuses (id),
  CONSTRAINT fk_opportunities_comp_type FOREIGN KEY (comp_type_id) REFERENCES comp_types (id),
  CONSTRAINT fk_opportunities_payment_status FOREIGN KEY (payment_status_id) REFERENCES payment_statuses (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- opportunity_contacts — many-to-many join between a gig and the people on it, carrying the
-- per-gig role (contact_role_id, optional) and is_primary ("lead on this gig" — unrelated to
-- contact_organizations.is_primary, which is the default contact for a venue). A lean join row,
-- hard-deleted when a person is unlinked, so no deleted_at. UNIQUE (opportunity_id, contact_id).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS opportunity_contacts (
  id              BIGINT    NOT NULL AUTO_INCREMENT,
  opportunity_id  BIGINT    NOT NULL,
  contact_id      BIGINT    NOT NULL,
  contact_role_id BIGINT    NULL,
  is_primary      BOOLEAN   NOT NULL DEFAULT FALSE,
  created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_opportunity_contacts_opp_contact (opportunity_id, contact_id),
  KEY ix_opportunity_contacts_contact (contact_id),
  CONSTRAINT fk_opportunity_contacts_opp FOREIGN KEY (opportunity_id) REFERENCES opportunities (id),
  CONSTRAINT fk_opportunity_contacts_contact FOREIGN KEY (contact_id) REFERENCES contacts (id),
  CONSTRAINT fk_opportunity_contacts_role FOREIGN KEY (contact_role_id) REFERENCES contact_roles (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- opportunity_notes — dated free-text notes on a gig. occurred_at is user-settable (the note can
-- record something that happened earlier) and is distinct from created_at (row insert time); the
-- unified contact timeline (slice 4) orders on occurred_at. Soft-deleted.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS opportunity_notes (
  id             BIGINT     NOT NULL AUTO_INCREMENT,
  user_id        BIGINT     NOT NULL,
  opportunity_id BIGINT     NOT NULL,
  body           MEDIUMTEXT NOT NULL,
  occurred_at    TIMESTAMP  NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_at     TIMESTAMP  NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     TIMESTAMP  NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted_at     TIMESTAMP  NULL DEFAULT NULL,
  PRIMARY KEY (id),
  KEY ix_opportunity_notes_opp_occurred (opportunity_id, occurred_at),
  CONSTRAINT fk_opportunity_notes_user FOREIGN KEY (user_id) REFERENCES users (id),
  CONSTRAINT fk_opportunity_notes_opp FOREIGN KEY (opportunity_id) REFERENCES opportunities (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- status_events — the append-only status journal. Exactly one row is inserted per real move (the
-- API inserts only when the new status differs from opportunities.current_status_id — acceptance
-- #1). note carries the close reason on terminal transitions (acceptance #8). Immutable: no
-- updated_at, no deleted_at. occurred_at is user-settable; created_at is the insert time.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS status_events (
  id             BIGINT    NOT NULL AUTO_INCREMENT,
  user_id        BIGINT    NOT NULL,
  opportunity_id BIGINT    NOT NULL,
  status_id      BIGINT    NOT NULL,
  note           TEXT      NULL,
  occurred_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY ix_status_events_opp_occurred (opportunity_id, occurred_at),
  KEY ix_status_events_user_status_occurred (user_id, status_id, occurred_at),
  CONSTRAINT fk_status_events_user FOREIGN KEY (user_id) REFERENCES users (id),
  CONSTRAINT fk_status_events_opp FOREIGN KEY (opportunity_id) REFERENCES opportunities (id),
  CONSTRAINT fk_status_events_status FOREIGN KEY (status_id) REFERENCES opportunity_statuses (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;