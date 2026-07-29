import {
  Alert,
  Badge,
  Button,
  Card,
  Collapse,
  Group,
  Loader,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconChevronDown, IconChevronUp, IconPencil } from '@tabler/icons-react';
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ApiError } from '../api/client';
import { isUnread, useEmailThreads, type EmailThread } from '../api/emails';
import { useCreateSignature, useSignatures, useUpdateSignature } from '../api/signatures';
import { CardTitle } from '../components/detailCards';
import { EmailComposer } from '../components/EmailComposer';
import { FieldLabel } from '../components/FieldLabel';
import { FilterBar, type FilterPill } from '../components/FilterBar';
import { PendingImportsCard } from '../components/PendingImportsCard';
import { RichTextField } from '../components/RichTextEditor';

/** Direction filters. Deliberately not "Replied": that cannot be derived from the data, and it is
 *  wrong for an inbound-first thread — a venue that emails Donna before she has contacted them. */
type DirectionFilter = 'all' | 'sent' | 'received';

const FILTERS: { value: DirectionFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'sent', label: 'Sent' },
  { value: 'received', label: 'Received' },
];

function formatDate(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function matchesSearch(thread: EmailThread, search: string): boolean {
  if (!search) return true;
  const needle = search.toLowerCase();
  return (
    thread.subject_normalized.toLowerCase().includes(needle) ||
    (thread.contact_name ?? '').toLowerCase().includes(needle)
  );
}

export function Emails() {
  const navigate = useNavigate();
  const threads = useEmailThreads();
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<DirectionFilter>('all');
  const [composerOpen, { open: openComposer, close: closeComposer }] = useDisclosure(false);
  const [signatureOpen, { toggle: toggleSignature }] = useDisclosure(false);

  const all = threads.data ?? [];
  const visible = all.filter(
    (t) =>
      matchesSearch(t, search) &&
      (filter === 'all' ||
        (filter === 'sent' && t.last_direction === 'out') ||
        (filter === 'received' && t.last_direction === 'in')),
  );

  const pills: FilterPill[] = FILTERS.map((f) => ({
    value: f.value,
    label: f.label,
    active: filter === f.value,
  }));

  const unreadCount = all.filter(isUnread).length;

  return (
    <Stack>
      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={2} c="navy.9">
            Emails
          </Title>
          <Text c="dimmed" size="sm">
            {all.length} {all.length === 1 ? 'thread' : 'threads'}
            {unreadCount > 0 && ` · ${unreadCount} unread`}
          </Text>
        </div>
        <Button onClick={openComposer}>Compose</Button>
      </Group>

      {/* Above the filters and the list: triage comes before browsing, and the card hides itself
          when there is nothing to triage. */}
      <PendingImportsCard />

      <FilterBar
        search={search}
        onSearch={setSearch}
        searchPlaceholder="Search email…"
        pills={pills}
        onPillClick={(value) => setFilter(value as DirectionFilter)}
      />

      <Card withBorder radius="md" p={0}>
        {threads.isPending ? (
          <Group p="md">
            <Loader size="sm" />
          </Group>
        ) : threads.isError ? (
          <Alert color="red" variant="light" m="md">
            Could not load your email threads.
          </Alert>
        ) : visible.length === 0 ? (
          <Text c="dimmed" size="sm" p="md">
            {all.length === 0
              ? 'No email threads yet. Compose one to get started.'
              : 'No threads match this filter.'}
          </Text>
        ) : (
          <Table highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Conversation</Table.Th>
                <Table.Th>Direction</Table.Th>
                <Table.Th>Messages</Table.Th>
                <Table.Th>Last message</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {visible.map((thread) => {
                const unread = isUnread(thread);
                return (
                  <Table.Tr
                    key={thread.id}
                    style={{ cursor: 'pointer' }}
                    onClick={() => navigate(`/emails/${thread.id}`)}
                  >
                    <Table.Td>
                      {/* Unread is shown by weight, the mail-client idiom — it needs no filter of
                          its own, and a chip would compete with the real state chips. */}
                      <Text fw={unread ? 700 : 500}>
                        {thread.subject_normalized || '(no subject)'}
                      </Text>
                      <Text size="xs" c="dimmed">
                        {thread.contact_name ?? 'Unlinked contact'}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Group gap={6}>
                        <Badge
                          size="sm"
                          variant="light"
                          color={thread.last_direction === 'out' ? 'blue' : 'teal'}
                        >
                          {thread.last_direction === 'out' ? 'Sent' : 'Received'}
                        </Badge>
                        {/* Sent but never confirmed: the mail went out and the confirm did not
                            land, so it awaits reconciliation. A fault state, not a conversation
                            state — worth showing rather than looking normal. */}
                        {thread.pending_count > 0 && (
                          <Badge size="sm" variant="light" color="orange">
                            Pending
                          </Badge>
                        )}
                      </Group>
                    </Table.Td>
                    <Table.Td>{thread.message_count}</Table.Td>
                    <Table.Td>{formatDate(thread.last_message_at)}</Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
        )}
      </Card>

      <SignatureCard opened={signatureOpen} onToggle={toggleSignature} />

      <EmailComposer
        opened={composerOpen}
        onClose={closeComposer}
        onSent={(threadId) => navigate(`/emails/${threadId}`)}
      />
    </Stack>
  );
}

/**
 * The signature editor, kept on this page behind a collapse.
 *
 * It lives here rather than in the nav because it is configuration touched once, not a place you
 * navigate to — but it must stay reachable, since it is what the composer appends to every send.
 */
function SignatureCard({ opened, onToggle }: { opened: boolean; onToggle: () => void }) {
  const signatures = useSignatures();
  const create = useCreateSignature();
  const [name, setName] = useState('Default');
  const [bodyHtml, setBodyHtml] = useState('');
  const [error, setError] = useState<string | null>(null);

  const list = signatures.data ?? [];
  const current = list.find((s) => s.is_default) ?? list[0] ?? null;
  const update = useUpdateSignature(current?.id ?? 0);

  // Seed the form when the stored signature (identity) loads or changes.
  useEffect(() => {
    if (current) {
      setName(current.name);
      setBodyHtml(current.body_html);
    }
  }, [current?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const saving = create.isPending || update.isPending;
  const isEmpty = !bodyHtml || bodyHtml === '<p></p>';

  async function handleSave() {
    setError(null);
    const payload = { name: name.trim() || 'Default', body_html: bodyHtml, is_default: true };
    try {
      if (current) {
        await update.mutateAsync(payload);
      } else {
        await create.mutateAsync(payload);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong.');
    }
  }

  return (
    <Card withBorder radius="md">
      <Group justify="space-between" onClick={onToggle} style={{ cursor: 'pointer' }}>
        <Group gap="xs">
          <IconPencil size={16} />
          <CardTitle>Email signature</CardTitle>
        </Group>
        {opened ? <IconChevronUp size={16} /> : <IconChevronDown size={16} />}
      </Group>
      <Collapse in={opened}>
        {signatures.isPending ? (
          <Loader size="sm" mt="sm" />
        ) : (
          <Stack mt="sm">
            {error && (
              <Alert color="red" variant="light">
                {error}
              </Alert>
            )}
            <div>
              <FieldLabel>Name</FieldLabel>
              <TextInput value={name} onChange={(e) => setName(e.currentTarget.value)} maw={320} />
            </div>
            <div>
              <FieldLabel helper="styled — appended to outgoing email">Signature</FieldLabel>
              <RichTextField value={bodyHtml} onChange={setBodyHtml} />
            </div>
            <Group>
              <Button onClick={handleSave} loading={saving} disabled={isEmpty}>
                Save signature
              </Button>
            </Group>
          </Stack>
        )}
      </Collapse>
    </Card>
  );
}