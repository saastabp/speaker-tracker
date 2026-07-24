import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { useApi } from './client';
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
  pro_bono_count: number;
}

export interface StaleOpportunity {
  id: number;
  title: string;
  organization_name: string;
  current_status: string;
  last_activity_at: string | null;
}

export interface NeedsAttentionItem {
  id: number;
  title: string;
  organization_name: string;
  reason: 'awaiting_payment' | 'overdue_unbooked';
  event_date: string | null;
}

export interface Dashboard {
  targets: TargetTile[];
  funnel: FunnelCount[];
  money: MoneyRollup;
  stale: StaleOpportunity[];
  needs_attention: NeedsAttentionItem[];
}

/** Load the composite dashboard payload. */
export function useDashboard(): UseQueryResult<Dashboard> {
  const api = useApi();
  return useQuery({
    queryKey: ['dashboard'],
    queryFn: () => api<Dashboard>('/dashboard'),
  });
}