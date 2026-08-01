import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Select,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { IconPaperclip } from '@tabler/icons-react';
import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ApiError } from '../api/client';
import {
  useCloseThread,
  useEmailThread,
  useMarkThreadRead,
  useReplyToThread,
  type EmailMessageDetail,
} from '../api/emails';
import { useLinkThreadOpportunity } from '../api/emailImports';
import { useOpportunities } from '../api/opportunities';
import { useSignatures } from '../api/signatures';
import { CardTitle } from '../components/detailCards';
import { FieldLabel } from '../components/FieldLabel';
import { RichTextField } from '../components/RichTextEditor';
import { SafeHtml } from '../components/SafeHtml';
import { BRAND_LINE, BRAND_PANEL } from '../theme';

function formatTimestamp(message: EmailMessageDetail): string {
  const iso = message.sent_at ?? message.received_at;
  if (!iso) return 'Not yet sent';
  return new Date(iso).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function formatSize(bytes: number | null): string {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function EmailThread() {
  const { id } = useParams<{ id: string }>();
  const threadId = id ? Number(id) : null;
  const navigate = useNavigate();
  const thread = useEmailThread(threadId);
  const markRead = useMarkThreadRead();
  const closeThread = useCloseThread();
  const opportunities = useOpportunities();
  const linkOpportunity = useLinkThreadOpportunity();

  // Opening the thread is what clears the inbox's unread weighting. Fired once per thread id,
  // not on every render or refetch.
  useEffect(() => {
    if (threadId !== null) markRead.mutate(threadId);
  }, [threadId]); // eslint-disable-line react-hooks/exhaustive-deps

  if (thread.isPending) {
    return <Loader size="sm" />;
  }
  if (thread.isError || !thread.data) {
    return (
      <Alert color="red" variant="light">
        Could not load this thread.
      </Alert>
    );
  }

  const data = thread.data;

  return (
    <Stack>
      <Text size="sm" c="dimmed">
        <Anchor component={Link} to="/emails" c="dimmed">
          Emails
        </Anchor>
        {' › '}
        <Text span fw={600}>
          {data.subject_normalized || '(no subject)'}
        </Text>
      </Text>

      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={2} c="navy.9">
            {data.subject_normalized || '(no subject)'}
          </Title>
          <Group gap="xs" mt={4}>
            {data.contact_id ? (
              <Anchor component={Link} to={`/contacts/${data.contact_id}`} size="sm">
                {data.contact_name}
              </Anchor>
            ) : (
              <Text size="sm" c="dimmed">
                Unlinked contact
              </Text>
            )}
            {/* The linked gig, as a link rather than a label: the whole point of attributing a
                conversation to an opportunity is being able to get from one to the other. */}
            {data.opportunity_id !== null && (
              <Anchor
                component={Link}
                to={`/pipeline/${data.opportunity_id}`}
                size="sm"
                c="teal.8"
              >
                {opportunities.data?.find((o) => o.id === data.opportunity_id)?.title ??
                  'Linked gig'}
              </Anchor>
            )}
            {data.pending_count > 0 && (
              <Badge size="sm" variant="light" color="orange">
                {data.pending_count} pending
              </Badge>
            )}
            {data.closed_at && (
              <Badge size="sm" variant="light" color="gray">
                Closed
              </Badge>
            )}
          </Group>
        </div>
        <Group gap="xs">
          {/*
            Linking a thread to a gig is not a convenience — it is the only way an inbound-first
            thread ever reaches one. Nothing infers it: a contact having exactly one open gig is
            not evidence that a given email concerns it, and filing side-channel mail against the
            wrong gig is worse than leaving it unattached. So a thread that started with a venue
            writing in would otherwise stay unattributed forever.

            `clearable` is what makes a wrong link correctable — clearing the picker detaches.
          */}
          <Select
            size="sm"
            w={230}
            placeholder="Not linked to a gig"
            aria-label="Link this thread to a gig"
            data={(opportunities.data ?? []).map((opportunity) => ({
              value: String(opportunity.id),
              label: `${opportunity.title} — ${opportunity.organization_name}`,
            }))}
            value={data.opportunity_id === null ? null : String(data.opportunity_id)}
            onChange={(value) =>
              linkOpportunity.mutate({
                threadId: data.id,
                opportunityId: value === null ? null : Number(value),
              })
            }
            disabled={opportunities.isPending || linkOpportunity.isPending}
            clearable
            searchable
            nothingFoundMessage="No gigs match"
          />
          {data.contact_id && (
            <Button variant="default" onClick={() => navigate(`/contacts/${data.contact_id}`)}>
              View contact
            </Button>
          )}
          <Button
            variant="default"
            loading={closeThread.isPending}
            onClick={() =>
              closeThread.mutate({ threadId: data.id, reopen: Boolean(data.closed_at) })
            }
          >
            {data.closed_at ? 'Reopen thread' : 'Close thread'}
          </Button>
        </Group>
      </Group>

      <Stack gap="sm">
        {data.messages.map((message) => (
          <MessageCard key={message.id} message={message} />
        ))}
      </Stack>

      {!data.closed_at && <ReplyBox threadId={data.id} contactName={data.contact_name} />}
    </Stack>
  );
}

function MessageCard({ message }: { message: EmailMessageDetail }) {
  const outbound = message.direction === 'out';
  const pending = outbound && !message.sent_at;

  return (
    <Card
      withBorder
      radius="md"
      style={{
        borderColor: BRAND_LINE,
        // Outbound sits on the tinted panel so the conversation reads as two voices at a glance.
        backgroundColor: outbound ? BRAND_PANEL : undefined,
      }}
    >
      <Group justify="space-between" align="flex-start" mb="xs">
        <div>
          <Text size="sm" fw={600}>
            {message.from_addr}
          </Text>
          <Text size="xs" c="dimmed">
            to {message.to_addr.join(', ') || '—'}
            {message.cc_addr.length > 0 && ` · cc ${message.cc_addr.join(', ')}`}
          </Text>
        </div>
        <Group gap={6}>
          {pending && (
            <Badge size="sm" variant="light" color="orange">
              Pending
            </Badge>
          )}
          <Text size="xs" c="dimmed">
            {formatTimestamp(message)}
          </Text>
        </Group>
      </Group>

      {message.body_html ? (
        <SafeHtml html={message.body_html} />
      ) : (
        // The API returns a null body when the stored MIME could not be read, rather than failing
        // the whole thread. Say so plainly instead of rendering an empty bubble that looks like a
        // blank email.
        <Text size="sm" c="dimmed" fs="italic">
          Message body unavailable.
        </Text>
      )}

      {message.attachments.length > 0 && (
        <Group gap="xs" mt="sm">
          {message.attachments.map((a) => (
            <Group key={a.filename} gap={4}>
              <IconPaperclip size={14} />
              <Text size="xs">{a.filename}</Text>
              <Text size="xs" c="dimmed">
                {formatSize(a.size_bytes)}
              </Text>
            </Group>
          ))}
        </Group>
      )}
    </Card>
  );
}

/**
 * Inline reply.
 *
 * Recipients, subject and the threading headers are all derived server-side from the parent
 * message (`In-Reply-To` / `References`), so this posts only a body — there is nothing here that
 * could produce a reply that threads nowhere.
 */
function ReplyBox({ threadId, contactName }: { threadId: number; contactName: string | null }) {
  const reply = useReplyToThread(threadId);
  const signatures = useSignatures();
  const defaultSignature = (signatures.data ?? []).find((s) => s.is_default) ?? null;
  const [bodyHtml, setBodyHtml] = useState('');
  // Stable across retries of this reply, rotated once it actually sends — so pressing Send twice
  // after a timeout is recognised as one message, not two.
  const [replyKey, setReplyKey] = useState(() => crypto.randomUUID());
  const [error, setError] = useState<string | null>(null);

  // Seed with the signature once it loads, as the composer does, so a reply carries it too.
  useEffect(() => {
    if (defaultSignature && !bodyHtml) {
      setBodyHtml(`<p></p>${defaultSignature.body_html}`);
    }
  }, [defaultSignature?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const isEmpty = !bodyHtml || bodyHtml === '<p></p>';

  async function handleSend() {
    setError(null);
    try {
      await reply.mutateAsync({ idempotency_key: replyKey, body_html: bodyHtml });
      // A sent reply means the next one is a new message, so the retry key rotates with the draft.
      setReplyKey(crypto.randomUUID());
      setBodyHtml(defaultSignature ? `<p></p>${defaultSignature.body_html}` : '');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not send the reply.');
    }
  }

  return (
    <Card withBorder radius="md">
      <Group justify="space-between">
        <CardTitle>{contactName ? `Reply to ${contactName}` : 'Reply'}</CardTitle>
        <Text size="xs" c="dimmed">
          stays on this thread
        </Text>
      </Group>
      <Stack mt="sm">
        {error && (
          <Alert color="red" variant="light">
            {error}
          </Alert>
        )}
        <div>
          <FieldLabel>Message</FieldLabel>
          <RichTextField value={bodyHtml} onChange={setBodyHtml} />
        </div>
        <Group>
          <Button onClick={handleSend} loading={reply.isPending} disabled={isEmpty}>
            Send reply
          </Button>
          <Text size="xs" c="dimmed">
            Keeps the thread and logs a touch.
          </Text>
        </Group>
      </Stack>
    </Card>
  );
}