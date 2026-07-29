import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import { useApi } from './client';
import { contactKeys, type Contact, type ContactInput } from './contacts';
import { emailKeys } from './emails';

// Mirrors backend models/email_inbound.py.
//
// A "pending import" is not a table: it is an email thread with no contact yet — mail the poller
// was authorized to ingest (Donna dragged it into the Import folder, or its header chain joined a
// thread that itself has no contact) but which is not yet attached to anyone she tracks. The queue
// is where the poller's work becomes hers.

/** One thread awaiting import — a row in the "N emails awaiting import" queue. */
export interface PendingImport {
  thread_id: number;
  /** `email_messages.id` of the message whose `From` prefills Add Contact — the thread's earliest
   *  inbound one. Named as the backend column is; a bare `message_id` means the RFC 5322 header. */
  email_message_id: number;
  /** Bare, lowercased sender address. */
  from_addr: string;
  /** Display name from the `From` header; null when the sender sent a bare address. */
  from_name: string | null;
  subject: string | null;
  received_at: string | null;
  /**
   * Venue whose `email_domain` matches the sender's domain, or null.
   *
   * Null is the ordinary case, not an error: it means no venue claims that domain — or that
   * *more than one* does, in which case the backend withholds the suggestion rather than guessing,
   * because a shared domain identifies nobody. A suggestion is a prefill Donna can change, never
   * something applied on her behalf.
   */
  suggested_organization_id: number | null;
  suggested_organization_name: string | null;
}

const importKeys = {
  pending: ['emails', 'imports'] as const,
};

/**
 * Threads awaiting import, newest first.
 *
 * The badge count is this list's length. There is deliberately no count endpoint — the queue holds
 * a handful of rows, and a separate count could disagree with the list it labels.
 */
export function usePendingImports(): UseQueryResult<PendingImport[]> {
  const api = useApi();
  return useQuery({
    queryKey: importKeys.pending,
    queryFn: async () =>
      (await api<{ imports: PendingImport[] }>('/emails/imports')).imports,
  });
}

/**
 * Attach an existing contact to a thread, or detach by passing `null`.
 *
 * `PUT`, not `POST`, because it sets a property rather than performing a verb: sending the same
 * contact twice succeeds, unlike `/close`, whose second call is a 404.
 *
 * Detaching returns the thread to the queue, which is the correction for having linked the wrong
 * person. It clears only what the link itself filled — a message whose contact the poller derived
 * from who actually sent it keeps that attribution.
 *
 * Creating a contact is **not** part of this. The import flow saves through `POST /contacts` so it
 * goes past slice 2's dedupe; offering to attach someone we already know rather than making a
 * duplicate *is* that dedupe.
 */
export function useLinkThreadContact() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ threadId, contactId }: { threadId: number; contactId: number | null }) =>
      api<{ thread_id: number; contact_id: number | null }>(
        `/emails/threads/${threadId}/contact`,
        { method: 'PUT', body: JSON.stringify({ contact_id: contactId }) },
      ),
    onSuccess: (_result, { threadId }) => {
      // Both change: the thread leaves (or rejoins) the queue, and the inbox row gains or loses
      // its contact name.
      void queryClient.invalidateQueries({ queryKey: importKeys.pending });
      void queryClient.invalidateQueries({ queryKey: emailKeys.threads });
      void queryClient.invalidateQueries({ queryKey: emailKeys.thread(threadId) });
    },
  });
}

/**
 * Import one pending thread: create the contact, optionally attach the suggested venue, link.
 *
 * Three calls rather than one endpoint, deliberately. The contact is created through
 * `POST /contacts` so it goes past slice 2's dedupe — the link endpoint refuses to create one
 * precisely so that dedupe lives in exactly one place, and an import endpoint that created a
 * contact itself would put it in two.
 *
 * **Not atomic, and it does not need to be.** If the link fails after the contact is created, the
 * contact exists and the thread stays in the queue; retrying finds the contact she just made
 * through the dedupe search and links it. The failure is visible and self-correcting, which is
 * worth more here than a transaction spanning three HTTP calls.
 *
 * The venue step is skipped when `organizationId` is null — which is the ordinary case, since a
 * suggestion only exists when exactly one venue claims the sender's domain.
 */
export function useImportPendingThread() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      threadId,
      contact,
      organizationId,
    }: {
      threadId: number;
      contact: ContactInput;
      organizationId: number | null;
    }) => {
      const created = await api<Contact>('/contacts', {
        method: 'POST',
        body: JSON.stringify(contact),
      });

      if (organizationId !== null) {
        await api<Contact>(`/contacts/${created.id}/organizations`, {
          method: 'POST',
          body: JSON.stringify({ organization_id: organizationId }),
        });
      }

      await api<{ thread_id: number; contact_id: number | null }>(
        `/emails/threads/${threadId}/contact`,
        { method: 'PUT', body: JSON.stringify({ contact_id: created.id }) },
      );
      return created;
    },
    onSuccess: (_created, { threadId }) => {
      void queryClient.invalidateQueries({ queryKey: contactKeys.all });
      void queryClient.invalidateQueries({ queryKey: importKeys.pending });
      void queryClient.invalidateQueries({ queryKey: emailKeys.threads });
      void queryClient.invalidateQueries({ queryKey: emailKeys.thread(threadId) });
    },
  });
}

/**
 * Attach a thread to a gig, or detach by passing `null`.
 *
 * This is the only way an inbound-first thread ever reaches an opportunity. Nothing infers one: a
 * contact having exactly one open gig is not evidence that a given email concerns it, and filing
 * side-channel mail against the wrong gig is worse than leaving it unattached.
 */
export function useLinkThreadOpportunity() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      threadId,
      opportunityId,
    }: {
      threadId: number;
      opportunityId: number | null;
    }) =>
      api<{ thread_id: number; opportunity_id: number | null }>(
        `/emails/threads/${threadId}/opportunity`,
        { method: 'PUT', body: JSON.stringify({ opportunity_id: opportunityId }) },
      ),
    onSuccess: (_result, { threadId }) => {
      // The queue is untouched — it is about attribution to a *person*, and a gig link is a
      // separate axis — so only the thread views are invalidated.
      void queryClient.invalidateQueries({ queryKey: emailKeys.threads });
      void queryClient.invalidateQueries({ queryKey: emailKeys.thread(threadId) });
    },
  });
}