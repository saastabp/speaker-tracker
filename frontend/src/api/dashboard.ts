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
}

export interface FunnelCount {
  status: string; // opportunity_statuses short_name
  count: number;
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

export interface ComingUpEvent {
  id: number;
  title: string;
  organization_name: string;
  event_date: string; // ISO date (YYYY-MM-DD)
  current_status: string;
}

export interface Dashboard {
  targets: TargetTile[];
  funnel: FunnelCount[];
  money: MoneyRollup;
  needs_attention: NeedsAttentionItem[];
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
};

/** Load the composite dashboard payload. */
export function useDashboard(): UseQueryResult<Dashboard> {
  const api = useApi();
  return useQuery({
    queryKey: dashboardKeys.all,
    queryFn: () => api<Dashboard>('/dashboard'),
  });
}