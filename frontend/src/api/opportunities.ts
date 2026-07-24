import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryKey,
  type UseQueryResult,
} from '@tanstack/react-query';
import { useApi } from './client';

// Mirrors backend models/opportunities.py. Entities travel as ids (organization_id, talk_id),
// catalogs as short_names (opportunity_format, comp_type, current_status, payment_status). Money
// (fee_amount) is a Decimal string over the wire; dates/timestamps are ISO strings.

export interface OpportunityInput {
  title: string;
  organization_id: number;
  opportunity_format: string; // opportunity_formats catalog short_name
  comp_type: string; // comp_types catalog short_name
  talk_id?: number | null;
  event_date?: string | null;
  fee_amount?: string | null; // Decimal → string
  currency?: string;
  angle?: string | null;
  outcome?: string | null;
}

/** Create-only superset of OpportunityInput carrying optional lifecycle seeds (backend
 *  OpportunityCreateInput). Omitting each reproduces the default: start in researching, payment
 *  status derived from comp_type, no lead. Not accepted by PUT (update stays lifecycle-free). */
export interface OpportunityCreateInput extends OpportunityInput {
  starting_status?: string | null; // opportunity_statuses short_name (non-terminal)
  payment_status?: string | null; // payment_statuses short_name
  lead_contact_id?: number | null; // contact linked as lead (is_primary) on this gig
}

export interface OpportunitySummary {
  id: number;
  title: string;
  organization_id: number;
  organization_name: string;
  organization_type: string; // organization_types short_name (for the card's venue chip)
  talk_title: string | null; // resolved talk name (null when no talk chosen)
  opportunity_format: string;
  current_status: string; // opportunity_statuses short_name — the SPA buckets by this
  comp_type: string;
  fee_amount: string | null;
  currency: string;
  payment_status: string;
  event_date: string | null;
  paid_on: string | null;
  closed_at: string | null; // null = active board, non-null = History
  created_at: string;
  updated_at: string;
}

export interface OpportunityContact {
  contact_id: number;
  name: string;
  contact_role: string | null; // contact_roles short_name — role on this gig
  is_primary: boolean; // lead on this gig
}

export interface OpportunityNote {
  id: number;
  body: string;
  occurred_at: string;
  created_at: string;
}

export interface StatusEvent {
  id: number;
  status: string; // opportunity_statuses short_name
  note: string | null; // close reason on terminal transitions
  occurred_at: string;
}

export interface Opportunity extends OpportunityInput {
  id: number;
  organization_name: string;
  talk_title: string | null;
  current_status: string;
  payment_status: string;
  paid_on: string | null;
  closed_at: string | null;
  created_at: string;
  updated_at: string;
  contacts: OpportunityContact[];
  notes: OpportunityNote[];
  status_events: StatusEvent[];
}

export interface OpportunityContactInput {
  contact_id: number;
  contact_role?: string | null;
  is_primary?: boolean;
}

export interface OpportunityContactUpdate {
  contact_role?: string | null;
  is_primary?: boolean;
}

export interface OpportunityNoteInput {
  body: string;
  occurred_at?: string | null;
}

const opportunityKeys = {
  all: ['opportunities'] as const,
  lists: () => ['opportunities', 'list'] as const,
  list: (closed?: boolean, status?: string) =>
    ['opportunities', 'list', { closed: closed ?? null, status: status ?? null }] as const,
  detail: (id: number) => ['opportunities', id] as const,
};

/** List opportunities as flat board / History cards. ``closed`` omitted returns both; ``false``
 *  the active board (closed_at IS NULL); ``true`` History. ``status`` filters to one stage. */
export function useOpportunities(
  closed?: boolean,
  status?: string,
): UseQueryResult<OpportunitySummary[]> {
  const api = useApi();
  return useQuery({
    queryKey: opportunityKeys.list(closed, status),
    queryFn: async () => {
      const params = new URLSearchParams();
      if (closed !== undefined) params.set('closed', String(closed));
      if (status) params.set('status', status);
      const qs = params.toString();
      return (await api<{ opportunities: OpportunitySummary[] }>(`/opportunities${qs ? `?${qs}` : ''}`))
        .opportunities;
    },
  });
}

/** Load one opportunity's full detail (linked contacts, notes, status journal). */
export function useOpportunity(id: number): UseQueryResult<Opportunity> {
  const api = useApi();
  return useQuery({
    queryKey: opportunityKeys.detail(id),
    queryFn: () => api<Opportunity>(`/opportunities/${id}`),
  });
}

export function useCreateOpportunity() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: OpportunityCreateInput) =>
      api<Opportunity>('/opportunities', { method: 'POST', body: JSON.stringify(data) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: opportunityKeys.lists() }),
  });
}

export function useUpdateOpportunity(id: number) {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: OpportunityInput) =>
      api<Opportunity>(`/opportunities/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    onSuccess: (updated) => {
      queryClient.setQueryData(opportunityKeys.detail(id), updated);
      queryClient.invalidateQueries({ queryKey: opportunityKeys.lists() });
    },
  });
}

export function useDeleteOpportunity() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      api<{ deleted: boolean }>(`/opportunities/${id}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: opportunityKeys.lists() }),
  });
}

/**
 * Optimistic status move (the board drag). The card's ``current_status`` is flipped immediately in
 * every cached board/History list so it jumps columns without waiting for the server (acceptance
 * #1); a failed PATCH restores the snapshot, rolling the card back (acceptance #2). ``onSettled``
 * invalidates so a move that also *closes* the card (e.g. delivering an already-settled gig) drops
 * it off the board on refetch, even though the optimistic step only moved it.
 */
export function usePatchStatus() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) =>
      api<Opportunity>(`/opportunities/${id}/status`, {
        method: 'PATCH',
        body: JSON.stringify({ status }),
      }),
    onMutate: async ({ id, status }) => {
      await queryClient.cancelQueries({ queryKey: opportunityKeys.lists() });
      const snapshot = queryClient.getQueriesData<OpportunitySummary[]>({
        queryKey: opportunityKeys.lists(),
      });
      for (const [key, list] of snapshot) {
        if (!list) continue;
        queryClient.setQueryData<OpportunitySummary[]>(
          key,
          list.map((o) => (o.id === id ? { ...o, current_status: status } : o)),
        );
      }
      return { snapshot };
    },
    onError: (_err, _vars, ctx) => {
      ctx?.snapshot.forEach(([key, list]: [QueryKey, OpportunitySummary[] | undefined]) =>
        queryClient.setQueryData(key, list),
      );
    },
    onSuccess: (updated) => {
      queryClient.setQueryData(opportunityKeys.detail(updated.id), updated);
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: opportunityKeys.all });
    },
  });
}

/** Update payment state (recomputes closed_at server-side); marking paid moves a delivered gig to
 *  History, correcting it back reopens it. Refreshes every list plus the detail. */
export function usePatchPayment() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      payment_status,
      paid_on,
    }: {
      id: number;
      payment_status: string;
      paid_on?: string | null;
    }) =>
      api<Opportunity>(`/opportunities/${id}/payment`, {
        method: 'PATCH',
        body: JSON.stringify({ payment_status, paid_on }),
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData(opportunityKeys.detail(updated.id), updated);
      queryClient.invalidateQueries({ queryKey: opportunityKeys.lists() });
    },
  });
}

/** Close an opportunity (cancelled / lost) with a required reason; moves it to History. */
export function useCloseOpportunity() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, status, reason }: { id: number; status: string; reason: string }) =>
      api<Opportunity>(`/opportunities/${id}/close`, {
        method: 'POST',
        body: JSON.stringify({ status, reason }),
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData(opportunityKeys.detail(updated.id), updated);
      queryClient.invalidateQueries({ queryKey: opportunityKeys.lists() });
    },
  });
}

// The linked-contact and note mutations return the updated opportunity detail and change only its
// nested data (not the board card), so they refresh the detail cache without invalidating lists.
function useOpportunityDetailMutation<TVariables>(
  request: (api: ReturnType<typeof useApi>, variables: TVariables) => Promise<Opportunity>,
) {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (variables: TVariables) => request(api, variables),
    onSuccess: (updated) => queryClient.setQueryData(opportunityKeys.detail(updated.id), updated),
  });
}

export function useAddOpportunityContact() {
  return useOpportunityDetailMutation<{ oppId: number; data: OpportunityContactInput }>(
    (api, { oppId, data }) =>
      api<Opportunity>(`/opportunities/${oppId}/contacts`, {
        method: 'POST',
        body: JSON.stringify(data),
      }),
  );
}

export function useUpdateOpportunityContact() {
  return useOpportunityDetailMutation<{
    oppId: number;
    contactId: number;
    data: OpportunityContactUpdate;
  }>((api, { oppId, contactId, data }) =>
    api<Opportunity>(`/opportunities/${oppId}/contacts/${contactId}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  );
}

export function useRemoveOpportunityContact() {
  return useOpportunityDetailMutation<{ oppId: number; contactId: number }>(
    (api, { oppId, contactId }) =>
      api<Opportunity>(`/opportunities/${oppId}/contacts/${contactId}`, { method: 'DELETE' }),
  );
}

export function useAddOpportunityNote() {
  return useOpportunityDetailMutation<{ oppId: number; data: OpportunityNoteInput }>(
    (api, { oppId, data }) =>
      api<Opportunity>(`/opportunities/${oppId}/notes`, {
        method: 'POST',
        body: JSON.stringify(data),
      }),
  );
}

export function useDeleteOpportunityNote() {
  return useOpportunityDetailMutation<{ oppId: number; noteId: number }>((api, { oppId, noteId }) =>
    api<Opportunity>(`/opportunities/${oppId}/notes/${noteId}`, { method: 'DELETE' }),
  );
}