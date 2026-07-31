import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import { useApi } from './client';

// Mirrors backend models/follow_ups.py. Entities are referenced by id; `due_date` is a calendar
// date ('YYYY-MM-DD') and never a timestamp, so it cannot be shifted by a timezone conversion on
// the way in or out. The *time* a reminder fires is not client-controlled — the server applies
// 07:00 in the user's own zone when it builds the EventBridge schedule.

export interface FollowUpInput {
  due_date: string; // YYYY-MM-DD
  note: string;
  /** At least one link is required — a follow-up attached to neither is rejected as a 400. */
  contact_id?: number | null;
  opportunity_id?: number | null;
  /** False makes the row dashboard-only: it still appears, but no reminder email is scheduled. */
  remind_by_email?: boolean;
}

/** A partial edit. Every field is optional and omitting one leaves it unchanged. */
export interface FollowUpPatch {
  due_date?: string;
  note?: string;
  remind_by_email?: boolean;
  /** True marks it done (the server stamps completed_at and cancels the reminder); false reopens. */
  completed?: boolean;
}

export interface FollowUp {
  id: number;
  due_date: string;
  note: string;
  contact_id: number | null;
  contact_name: string | null;
  opportunity_id: number | null;
  opportunity_title: string | null;
  remind_by_email: boolean;
  /** Null is pending. This is the only done-state — there is no separate status field. */
  completed_at: string | null;
  created_at: string;
}

export interface FollowUpFilters {
  contactId?: number;
  opportunityId?: number;
  pendingOnly?: boolean;
}

/** Exported so surfaces that create follow-ups as a side effect (the outreach and email riders)
 *  invalidate through these factories rather than re-typing the key — a rename then fails to
 *  compile instead of quietly leaving a stale list. */
export const followUpKeys = {
  all: ['follow-ups'] as const,
  list: (filters: FollowUpFilters = {}) => ['follow-ups', filters] as const,
};

function toQuery(filters: FollowUpFilters): string {
  const params = new URLSearchParams();
  if (filters.contactId !== undefined) params.set('contact_id', String(filters.contactId));
  if (filters.opportunityId !== undefined)
    params.set('opportunity_id', String(filters.opportunityId));
  if (filters.pendingOnly) params.set('pending_only', 'true');
  const query = params.toString();
  return query ? `?${query}` : '';
}

/** List follow-ups, soonest first. Unfiltered is the Follow-ups page, which shows history too. */
export function useFollowUps(filters: FollowUpFilters = {}): UseQueryResult<FollowUp[]> {
  const api = useApi();
  return useQuery({
    queryKey: followUpKeys.list(filters),
    queryFn: async () =>
      (await api<{ follow_ups: FollowUp[] }>(`/follow-ups${toQuery(filters)}`)).follow_ups,
  });
}

/** Every write invalidates the dashboard as well as the lists.
 *
 *  The Dashboard's due card is served by the same rows, so an edit that does not refresh it leaves
 *  a completed follow-up visibly outstanding on the home screen — which is the one thing this
 *  feature exists to stop. Invalidating the whole `follow-ups` key rather than a specific filter
 *  is deliberate: a single row appears in the page list, a contact panel and an opportunity panel
 *  at once, and working out which of those a given edit touched is more ways to be wrong than a
 *  refetch of a small list is worth. */
function useFollowUpInvalidation() {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: followUpKeys.all });
    queryClient.invalidateQueries({ queryKey: ['dashboard'] });
  };
}

/** Create a follow-up. The reminder is scheduled server-side unless remind_by_email is false. */
export function useCreateFollowUp() {
  const api = useApi();
  const invalidate = useFollowUpInvalidation();
  return useMutation({
    mutationFn: (data: FollowUpInput) =>
      api<FollowUp>('/follow-ups', { method: 'POST', body: JSON.stringify(data) }),
    onSuccess: invalidate,
  });
}

/** Edit a follow-up — including marking it done, which is `{ completed: true }` rather than a
 *  separate endpoint, so one server path reconciles the reminder for every kind of change. */
export function usePatchFollowUp() {
  const api = useApi();
  const invalidate = useFollowUpInvalidation();
  return useMutation({
    mutationFn: ({ id, ...patch }: FollowUpPatch & { id: number }) =>
      api<FollowUp>(`/follow-ups/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
    onSuccess: invalidate,
  });
}

/** Delete a follow-up; the server cancels its reminder. */
export function useDeleteFollowUp() {
  const api = useApi();
  const invalidate = useFollowUpInvalidation();
  return useMutation({
    mutationFn: ({ id }: { id: number }) =>
      api<{ deleted: boolean }>(`/follow-ups/${id}`, { method: 'DELETE' }),
    onSuccess: invalidate,
  });
}