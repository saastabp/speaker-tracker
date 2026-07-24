-- 0007_target_labels.sql — UX reconciliation: align target_types display labels with the
-- Donna-approved mockup. 0001 seeded short catalog labels ('Venues Researched', ...); the
-- dashboard tiles and Targets page should show the approved wording. Editing the applied 0001
-- file would change its checksum (forward-only rule, DATABASE.md §6), so this is a new forward
-- migration. Idempotent — re-running restates the same descriptions.
INSERT INTO target_types (short_name, description, sort_order) VALUES
  ('venues_researched', 'New venues researched', 10),
  ('outreaches',        'Outreach touches',      20),
  ('pitches',           'Pitches sent',          30),
  ('bookings',          'Gigs booked',           40)
AS v ON DUPLICATE KEY UPDATE description = v.description, sort_order = v.sort_order;