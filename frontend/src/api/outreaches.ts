import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import { useApi } from './client';

// Mirrors backend models/outreach.py and models/timeline.py. Entities are referenced by id and
// catalogs by short_name (Option A); timestamps are ISO strings over the wire.

export interface OutreachInput {
  contact_id: number;
  channel: string; // outreach_channels short_name
  /** Omit to accept the server-inferred default (initial / correspondence); set to override. */
  kind?: string | null;
  opportunity_id?: number | null;
  message_template_id?: number | null;
  note?: string | null;
  occurred_at?: string | null;
}

export interface Outreach {
  id: number;
  contact_id: number;
  contact_name: string;
  opportunity_id: number | null;
  channel: string;
  kind: string; // the resolved kind (override or inferred)
  message_template_id: number | null;
  note: string | null;
  occurred_at: string;
  created_at: string;
}

/** One entry in a contact's unified timeline (outreaches + gig notes + status events, #5). */
export interface TimelineItem {
  item_type: 'outreach' | 'note' | 'status_event';
  source_id: number;
  occurred_at: string;
  text: string | null;
  opportunity_id: number | null;
  opportunity_title: string | null;
  channel: string | null; // outreach items only
  kind: string | null; // outreach items only
  status: string | null; // status_event items only
}

const outreachKeys = {
  forContact: (contactId: number) => ['outreaches', 'contact', contactId] as const,
};

const timelineKeys = {
  forContact: (contactId: number) => ['timeline', 'contact', contactId] as const,
};

/** List a contact's outbound touches, newest first. */
export function useContactOutreaches(contactId: number): UseQueryResult<Outreach[]> {
  const api = useApi();
  return useQuery({
    queryKey: outreachKeys.forContact(contactId),
    queryFn: async () =>
      (await api<{ outreaches: Outreach[] }>(`/contacts/${contactId}/outreaches`)).outreaches,
  });
}

/** Load a contact's unified timeline, newest first. */
export function useContactTimeline(contactId: number): UseQueryResult<TimelineItem[]> {
  const api = useApi();
  return useQuery({
    queryKey: timelineKeys.forContact(contactId),
    queryFn: async () =>
      (await api<{ timeline: TimelineItem[] }>(`/contacts/${contactId}/timeline`)).timeline,
  });
}

/** Log an outbound touch. Both the contact's outreach list and its timeline refresh on success. */
export function useCreateOutreach() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: OutreachInput) =>
      api<Outreach>('/outreaches', { method: 'POST', body: JSON.stringify(data) }),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: outreachKeys.forContact(created.contact_id) });
      queryClient.invalidateQueries({ queryKey: timelineKeys.forContact(created.contact_id) });
    },
  });
}

/** Retract a mis-logged touch. `contactId` scopes which contact's caches to refresh. */
export function useDeleteOutreach() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id }: { id: number; contactId: number }) =>
      api<{ deleted: boolean }>(`/outreaches/${id}`, { method: 'DELETE' }),
    onSuccess: (_result, { contactId }) => {
      queryClient.invalidateQueries({ queryKey: outreachKeys.forContact(contactId) });
      queryClient.invalidateQueries({ queryKey: timelineKeys.forContact(contactId) });
    },
  });
}