import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { useApi } from './client';
import type { FollowUp } from './followUps';
import type { Cadence } from './targets';

// Mirrors backend models/dashboard.py — one composite GET /dashboard. Money amounts are Decimals
// serialized as strings; timestamps/dates are ISO strings.

export interface TargetTile {
  target_type: string;
  cadence: Cadence;
  goal: number;
  actual: number;
  /** The window `actual` was counted over, `[start, end)`. Sent by the server rather than derived
   *  here: the period maths (Sunday-start weeks, quarter edges) lives in `core/periods.py`, and
   *  recomputing it in TS would be the same rules written twice. The tile's link hands these
   *  straight to the list, which is the shape a date-range picker would produce later too. */
  period_start: string;
  period_end: string;
}

export interface FunnelCount {
  status: string; // opportunity_statuses short_name
  /** Reached-or-beyond — drives the conversion percentages, and narrows monotonically. */
  count: number;
  /** Gigs sitting at this stage today. What the row links to, so the number clicked and the list
   *  opened are the same size; the gap against `count` is the stage's drop-off. */
  current: number;
}

export interface MoneyRollup {
  currency: string;
  booked: string;
  received: string;
  outstanding: string;
  booked_count: number;
  received_count: number;
  invoiced_count: number;
  pro_bono_count: number;
}

export interface NeedsAttentionItem {
  id: number;
  title: string;
  organization_name: string;
  /** Also says which id-space `id` belongs to: the three gig reasons → opportunity, research →
   *  organization, awaiting_reply → email thread. Adding one means teaching Dashboard a link. */
  reason:
    | 'awaiting_payment'
    | 'overdue_unbooked'
    | 'research_incomplete'
    | 'awaiting_reply'
    | 'stale';
  event_date: string | null;
  /** The date the condition began — last activity for `stale`, last outbound message for
   *  `awaiting_reply`. Null where the urgency is not a duration, so the row shows no age chip. */
  since: string | null;
}

/** One dated thing on the near horizon. Two shapes in one list, discriminated by `item_type`: a gig
 *  has `organization_name` and `current_status` but no time of day, while an appointment has
 *  `contact_name` and an `event_time`. `id` is unique **per `item_type`**, not across the list, so
 *  a React key has to combine the two. */
export interface ComingUpEvent {
  item_type: 'gig' | 'appointment';
  id: number;
  title: string;
  organization_name: string | null;
  contact_name: string | null;
  event_date: string; // ISO date (YYYY-MM-DD)
  event_time: string | null; // HH:mm:ss, appointments only
  current_status: string | null;
}

export interface Week {
  /** Inclusive Sunday. */
  start: string;
  /** Exclusive — the following Sunday. */
  end: string;
}

export interface Dashboard {
  /** The week the tiles report on. Server-resolved, so the navigator labels itself without
   *  reimplementing Sunday-start boundaries in TS — the same reason `TargetTile.period_start`
   *  is sent. Always present, even when no weekly target is set. */
  week: Week;
  targets: TargetTile[];
  funnel: FunnelCount[];
  money: MoneyRollup;
  needs_attention: NeedsAttentionItem[];
  /** How many gigs produced at least one response — the funnel card's final row (slice 12). A bare
   *  count rather than a sixth `FunnelCount`: "responses" is not an opportunity status, and there
   *  is no "how many sit here now" for something a gig produces rather than occupies. All-time,
   *  like the rest of the funnel. */
  responses_reached: number;
  coming_up: ComingUpEvent[];
  /** Pending reminders due today **or earlier** — overdue ones must get louder, not scroll off a
   *  future-facing list, which is why they are their own card rather than part of `coming_up`. */
  follow_ups: FollowUp[];
}

/** Exported so writes elsewhere that move dashboard numbers (sending an email logs an outreach,
 *  which counts toward the touch targets) invalidate via this factory rather than a string
 *  literal — a rename then fails to compile instead of silently leaving stale counts. */
export const dashboardKeys = {
  all: ['dashboard'] as const,
  /** One cache entry per week viewed. Prefix-matched by `all`, so the existing invalidations after
   *  a write still clear every week, not just the one on screen. */
  week: (weekOf: string) => ['dashboard', weekOf] as const,
};

/** Load the composite dashboard payload.
 *
 * `weekOf` is any day in the week the target tiles should report on; omitted means the current
 * week. Only the tiles move with it — money, funnel, Needs attention and Coming up always describe
 * now, whichever week is being viewed.
 */
export function useDashboard(weekOf?: string): UseQueryResult<Dashboard> {
  const api = useApi();
  return useQuery({
    queryKey: weekOf ? dashboardKeys.week(weekOf) : dashboardKeys.all,
    queryFn: () =>
      api<Dashboard>(weekOf ? `/dashboard?week_of=${encodeURIComponent(weekOf)}` : '/dashboard'),
  });
}