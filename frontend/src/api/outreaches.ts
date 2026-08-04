import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import { useApi } from './client';
import { dashboardKeys } from './dashboard';

// Mirrors backend models/outreach.py and models/timeline.py. Entities are referenced by id and
// catalogs by short_name (Option A); timestamps are ISO strings over the wire.


/** Opt-in request to schedule a follow-up alongside another action. Omit or send `null` to leave
 *  it off — sending or logging must never silently schedule anything (acceptance #6). */
export interface FollowUpRiderInput {
  due_date: string;
  /** Blank lets the server derive one from the parent action's context. */
  note?: string | null;
}

export interface OutreachInput {
  contact_id: number;
  channel: string; // outreach_channels short_name
  /** Omit to accept the server-inferred default (initial / correspondence); set to override. */
  kind?: string | null;
  opportunity_id?: number | null;
  message_template_id?: number | null;
  note?: string | null;
  occurred_at?: string | null;
  follow_up?: FollowUpRiderInput | null;
}

/** A partial edit to a logged touch. Omitting a key leaves it unchanged; sending `opportunity_id`
 *  or `note` as null **clears** it. The contact is deliberately absent — a touch cannot be moved to
 *  another person (the server does not accept it), because that would re-open the kind inference
 *  that ran when it was logged. */
export interface OutreachPatch {
  channel?: string;
  kind?: string;
  opportunity_id?: number | null;
  note?: string | null;
  occurred_at?: string;
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

/** Exported so other modules whose writes touch the journal (e.g. sending an email, which logs an
 *  outreach) invalidate via these factories rather than duplicating the key as a string literal —
 *  a rename then fails to compile instead of silently leaving a stale timeline. */
export const outreachKeys = {
  all: ['outreaches'] as const,
  forContact: (contactId: number) => ['outreaches', 'contact', contactId] as const,
};

export const timelineKeys = {
  all: ['timeline'] as const,
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

/** Refresh everything a touch is visible in: the contact's outreach list, their timeline, and the
 *  dashboard.
 *
 *  The dashboard was missing here until slice 11. A touch counts toward the week's outreach target,
 *  so logging one from a contact page left the tile reading one too low until something else
 *  happened to refetch it — while the same write through the email composer *did* refresh it
 *  (`api/emails.ts`), which is what kept the gap out of sight. */
function useOutreachInvalidation() {
  const queryClient = useQueryClient();
  return (contactId: number) => {
    queryClient.invalidateQueries({ queryKey: outreachKeys.forContact(contactId) });
    queryClient.invalidateQueries({ queryKey: timelineKeys.forContact(contactId) });
    queryClient.invalidateQueries({ queryKey: dashboardKeys.all });
  };
}

/** Log an outbound touch. Both the contact's outreach list and its timeline refresh on success. */
export function useCreateOutreach() {
  const api = useApi();
  const invalidate = useOutreachInvalidation();
  return useMutation({
    mutationFn: (data: OutreachInput) =>
      api<Outreach>('/outreaches', { method: 'POST', body: JSON.stringify(data) }),
    onSuccess: (created) => invalidate(created.contact_id),
  });
}

/** Correct a mis-logged touch. `contactId` scopes which contact's caches to refresh. */
export function usePatchOutreach() {
  const api = useApi();
  const invalidate = useOutreachInvalidation();
  return useMutation({
    mutationFn: ({
      id,
      contactId: _contactId,
      ...patch
    }: OutreachPatch & { id: number; contactId: number }) =>
      api<Outreach>(`/outreaches/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
    onSuccess: (_result, { contactId }) => invalidate(contactId),
  });
}

/** Retract a mis-logged touch. `contactId` scopes which contact's caches to refresh. */
export function useDeleteOutreach() {
  const api = useApi();
  const invalidate = useOutreachInvalidation();
  return useMutation({
    mutationFn: ({ id }: { id: number; contactId: number }) =>
      api<{ deleted: boolean }>(`/outreaches/${id}`, { method: 'DELETE' }),
    onSuccess: (_result, { contactId }) => invalidate(contactId),
  });
}