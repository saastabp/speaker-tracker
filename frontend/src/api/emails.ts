import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import { useApi } from './client';
import { dashboardKeys } from './dashboard';
import { outreachKeys, timelineKeys } from './outreaches';

// Mirrors backend models/emails.py. Everything lives under /emails; entities are referenced by id
// and catalogs by short_name (Option A); timestamps are ISO strings over the wire.

export type EmailDirection = 'out' | 'in';

/** An attachment the composer has already uploaded, referenced by the key the API issued. */
export interface EmailAttachmentInput {
  s3_key: string;
  filename: string;
  content_type: string;
  size_bytes?: number | null;
}

/** An attachment as read back from a stored message — metadata only; the bytes stay in the MIME. */
export interface EmailAttachment {
  filename: string;
  content_type: string;
  size_bytes: number | null;
}

export interface EmailSendInput {
  to: string[];
  subject: string;
  /** Full composer HTML **including the signature** — the server never appends one. */
  body_html: string;
  cc?: string[];
  contact_id?: number | null;
  opportunity_id?: number | null;
  message_template_id?: number | null;
  /** Omit to accept the server-inferred outreach kind (initial / correspondence). */
  outreach_kind?: string | null;
  attachments?: EmailAttachmentInput[];
}

export interface EmailReplyInput {
  body_html: string;
  /** Omit to reply to the thread's most recent *confirmed* message. */
  in_reply_to_message_id?: number | null;
  to?: string[] | null;
  cc?: string[] | null;
  outreach_kind?: string | null;
  attachments?: EmailAttachmentInput[];
}

export interface EmailMessage {
  id: number;
  thread_id: number;
  direction: EmailDirection;
  message_id: string;
  subject: string | null;
  from_addr: string;
  to_addr: string[];
  cc_addr: string[];
  sent_at: string | null;
  received_at: string | null;
}

export interface EmailMessageDetail extends EmailMessage {
  /** Reconstructed from the stored MIME. Null when the object could not be read — the message
   *  still lists rather than failing the whole thread. */
  body_html: string | null;
  attachments: EmailAttachment[];
}

export interface EmailThread {
  id: number;
  subject_normalized: string;
  contact_id: number | null;
  contact_name: string | null;
  opportunity_id: number | null;
  last_direction: EmailDirection;
  last_message_at: string | null;
  last_read_at: string | null;
  closed_at: string | null;
  message_count: number;
  /** Outbound messages still awaiting confirmation — sent but unrecorded-as-sent. Non-zero is a
   *  fault state worth surfacing, not a normal conversation state. */
  pending_count: number;
}

export interface EmailThreadDetail extends EmailThread {
  messages: EmailMessageDetail[];
}

export interface EmailSendResult {
  message: EmailMessage;
  thread_id: number;
  outreach_id: number | null;
}

/** What `POST /emails/attachments` returns: a presigned PUT and the key to send back. */
export interface AttachmentUpload {
  upload_url: string;
  s3_key: string;
  content_type: string;
}

/** Exported because `emailImports.ts` invalidates threads: linking a contact to a pending thread
 *  changes what the inbox shows, not just the import queue. */
export const emailKeys = {
  threads: ['emails', 'threads'] as const,
  thread: (id: number) => ['emails', 'threads', id] as const,
};

/**
 * A thread's state, derived from `last_direction` alone.
 *
 * Deliberately only two values. The mockup also showed "Replied", but that cannot be derived and
 * is wrong for an **inbound-first** thread — a venue that emails Donna before she has ever
 * contacted them (DESIGN.md §3), which is a first-class case here.
 */
export type ThreadState = 'sent' | 'received';

export function threadState(thread: EmailThread): ThreadState {
  return thread.last_direction === 'out' ? 'sent' : 'received';
}

/** Whether a thread has activity the user has not looked at since it arrived. */
export function isUnread(thread: EmailThread): boolean {
  if (thread.last_direction !== 'in' || !thread.last_message_at) return false;
  if (!thread.last_read_at) return true;
  return new Date(thread.last_read_at) < new Date(thread.last_message_at);
}

/** List the caller's threads for the inbox, most recent activity first (pending-only threads last). */
export function useEmailThreads(includeClosed = false): UseQueryResult<EmailThread[]> {
  const api = useApi();
  return useQuery({
    queryKey: [...emailKeys.threads, { includeClosed }],
    queryFn: async () =>
      (
        await api<{ threads: EmailThread[] }>(
          `/emails/threads${includeClosed ? '?include_closed=true' : ''}`,
        )
      ).threads,
  });
}

/** One thread with its full conversation, bodies reconstructed from stored MIME. */
export function useEmailThread(id: number | null): UseQueryResult<EmailThreadDetail> {
  const api = useApi();
  return useQuery({
    queryKey: emailKeys.thread(id ?? 0),
    enabled: id !== null,
    queryFn: async () => api<EmailThreadDetail>(`/emails/threads/${id}`),
  });
}

export function useSendEmail() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: EmailSendInput) =>
      api<EmailSendResult>('/emails/send', { method: 'POST', body: JSON.stringify(data) }),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: emailKeys.threads });
      void queryClient.invalidateQueries({ queryKey: emailKeys.thread(result.thread_id) });
      // A send also logs an outreach touch, so the contact's journal, timeline and the dashboard's
      // touch counts are all stale. Invalidated via the owning modules' key factories, not string
      // literals, so a key rename cannot silently break this.
      void queryClient.invalidateQueries({ queryKey: outreachKeys.all });
      void queryClient.invalidateQueries({ queryKey: timelineKeys.all });
      void queryClient.invalidateQueries({ queryKey: dashboardKeys.all });
    },
  });
}

export function useReplyToThread(threadId: number) {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: EmailReplyInput) =>
      api<EmailSendResult>(`/emails/threads/${threadId}/replies`, {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: emailKeys.threads });
      void queryClient.invalidateQueries({ queryKey: emailKeys.thread(threadId) });
      void queryClient.invalidateQueries({ queryKey: outreachKeys.all });
      void queryClient.invalidateQueries({ queryKey: timelineKeys.all });
      void queryClient.invalidateQueries({ queryKey: dashboardKeys.all });
    },
  });
}

export function useMarkThreadRead() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (threadId: number) =>
      api<{ read: boolean }>(`/emails/threads/${threadId}/read`, { method: 'POST' }),
    onSuccess: (_result, threadId) => {
      void queryClient.invalidateQueries({ queryKey: emailKeys.threads });
      void queryClient.invalidateQueries({ queryKey: emailKeys.thread(threadId) });
    },
  });
}

export function useCloseThread() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ threadId, reopen = false }: { threadId: number; reopen?: boolean }) =>
      api<Record<string, boolean>>(`/emails/threads/${threadId}/${reopen ? 'reopen' : 'close'}`, {
        method: 'POST',
      }),
    onSuccess: (_result, { threadId }) => {
      void queryClient.invalidateQueries({ queryKey: emailKeys.threads });
      void queryClient.invalidateQueries({ queryKey: emailKeys.thread(threadId) });
    },
  });
}

/**
 * Upload one attachment and return the reference to send with the email.
 *
 * Two steps, because attachment bytes never pass through the API: the server issues a presigned
 * PUT for a key **it** chooses (user-scoped — a client-supplied key is ignored), the browser PUTs
 * the file straight to S3, and the send carries only the key back.
 *
 * The PUT deliberately uses bare `fetch`, not `useApi`: the presigned URL is S3's, not ours, and
 * attaching our `Authorization` header to it would break the signature.
 */
export function useUploadAttachment() {
  const api = useApi();
  return useMutation({
    mutationFn: async (file: File): Promise<EmailAttachmentInput> => {
      const contentType = file.type || 'application/octet-stream';
      const upload = await api<AttachmentUpload>('/emails/attachments', {
        method: 'POST',
        body: JSON.stringify({ filename: file.name, content_type: contentType }),
      });

      const res = await fetch(upload.upload_url, {
        method: 'PUT',
        // Must match the Content-Type signed into the URL, or S3 rejects the upload.
        headers: { 'Content-Type': upload.content_type },
        body: file,
      });
      if (!res.ok) {
        throw new Error(`upload failed (${res.status})`);
      }

      return {
        s3_key: upload.s3_key,
        filename: file.name,
        content_type: contentType,
        size_bytes: file.size,
      };
    },
  });
}