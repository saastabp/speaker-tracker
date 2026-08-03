-- Slice 10 follow-up: make "new venues researched" a monthly *flow* instead of a running total.
--
-- Research-readiness has always been computed on read (core/research.py, and its SQL mirror in the
-- dashboard), so nothing recorded *when* a venue crossed the bar. That was invisible while the
-- dashboard only ever showed today; the week picker exposed it, because the tile reported today's
-- count under April's label. A target named "new venues researched" with a monthly cadence is a
-- flow, and a flow needs a date.
--
-- Stamped once, on the first crossing, and never cleared: a venue that loses its last contact and
-- regains one was not researched twice. That is also why this is a timestamp and not a flag.

ALTER TABLE organizations
  ADD COLUMN research_ready_at TIMESTAMP NULL DEFAULT NULL
    COMMENT 'First time this venue met the research-ready bar; NULL until it does',
  ADD KEY ix_organizations_user_research_ready (user_id, research_ready_at);

-- Backfill: existing ready venues are credited to the month they were created. An approximation,
-- and knowingly so — the real crossing date was never recorded, and leaving them NULL would credit
-- them to no month at all, which reads as "never researched anything" on every past window.
UPDATE organizations o
   SET o.research_ready_at = o.created_at
 WHERE o.deleted_at IS NULL
   AND o.research_ready_at IS NULL
   AND TRIM(COALESCE(o.what_it_is, '')) <> ''
   AND TRIM(COALESCE(o.why_it_fits, '')) <> ''
   AND TRIM(COALESCE(o.how_to_approach, '')) <> ''
   AND EXISTS (SELECT 1 FROM contact_organizations co
               JOIN contacts c ON c.id = co.contact_id AND c.deleted_at IS NULL
               WHERE co.organization_id = o.id);