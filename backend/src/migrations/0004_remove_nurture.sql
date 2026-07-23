-- 0004_remove_nurture.sql — retire the `nurture` pipeline status.
--
-- Nurture was a post-delivery holding state. It is dropped because keeping a venue relationship warm
-- is a property of the contact/venue (warmth tier, power-partner) and of follow-ups — not a stage of
-- a past gig — and the acquisition boundary reads cleaner as "ends at Delivered". The board's columns
-- are server-owned: the funnel derives them from non-deleted `opportunity_statuses`, so soft-deleting
-- the row removes the column with no code or frontend change (acceptance #9).
--
-- Prod-safe: nurture was never used in prod, so both statements are no-ops there.

-- 1. Re-home any opportunity currently parked in nurture to `booked` — a safe non-terminal stage for
--    re-triage — so the retired column orphans no cards. Runs first, while the join still resolves
--    the nurture row. closed_at is untouched (booked is non-terminal, so nothing should be closed).
UPDATE opportunities o
JOIN opportunity_statuses s_old ON s_old.id = o.current_status_id AND s_old.short_name = 'nurture'
JOIN opportunity_statuses s_new ON s_new.short_name = 'booked'
SET o.current_status_id = s_new.id
WHERE o.deleted_at IS NULL;

-- 2. Soft-delete the catalog row. The funnel query (WHERE deleted_at IS NULL) stops rendering the
--    column, and /catalogs stops returning it. Guarded so a re-run is a no-op.
UPDATE opportunity_statuses
SET deleted_at = CURRENT_TIMESTAMP
WHERE short_name = 'nurture' AND deleted_at IS NULL;