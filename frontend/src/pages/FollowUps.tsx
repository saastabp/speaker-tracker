import {
  ActionIcon,
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import {
  IconAlertTriangle,
  IconArrowBackUp,
  IconCheck,
  IconPencil,
  IconPlus,
  IconTrash,
} from '@tabler/icons-react';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  useDeleteFollowUp,
  useFollowUps,
  usePatchFollowUp,
  type FollowUp,
} from '../api/followUps';
import { FilterBar } from '../components/FilterBar';
import { FollowUpFormModal } from '../components/FollowUpFormModal';
import { BRAND_LINE } from '../theme';

/** Parse a bare `YYYY-MM-DD` as a local date — `new Date(iso)` would read it as UTC midnight and
 *  land on the previous day in a negative-offset zone like Kauaʻi. */
function parseDateLocal(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d);
}

function startOfToday(): Date {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

type Filter = 'all' | 'pending' | 'overdue' | 'completed';

const FILTERS: { value: Filter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'pending', label: 'Pending' },
  { value: 'overdue', label: 'Overdue' },
  { value: 'completed', label: 'Completed' },
];

/**
 * The full list of follow-ups — everything the Dashboard card deliberately does not show.
 *
 * The card is scoped to what is due today or earlier, so it stays a short list of things to act on
 * now. This page is where the rest live: scheduled for later, already done, or dashboard-only
 * (reminder switched off). Filtering happens client-side over one unfiltered fetch because the
 * whole set is small — a single user's reminders — and it keeps the pills instant.
 */
export function FollowUps() {
  const followUps = useFollowUps();
  const patch = usePatchFollowUp();
  const remove = useDeleteFollowUp();
  const [filter, setFilter] = useState<Filter>('pending');
  const [search, setSearch] = useState('');
  const [editing, setEditing] = useState<FollowUp | null>(null);
  const [formOpen, formHandlers] = useDisclosure(false);

  const today = startOfToday();

  const visible = useMemo(() => {
    const rows = followUps.data ?? [];
    const term = search.trim().toLowerCase();
    return rows.filter((f) => {
      const pending = f.completed_at === null;
      const overdue = pending && parseDateLocal(f.due_date) < today;
      if (filter === 'pending' && !pending) return false;
      if (filter === 'overdue' && !overdue) return false;
      if (filter === 'completed' && pending) return false;
      if (!term) return true;
      const haystack = [f.note, f.contact_name, f.opportunity_title]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      return haystack.includes(term);
    });
  }, [followUps.data, filter, search, today]);

  function openCreate() {
    setEditing(null);
    formHandlers.open();
  }

  function openEdit(followUp: FollowUp) {
    setEditing(followUp);
    formHandlers.open();
  }

  function closeForm() {
    formHandlers.close();
    setEditing(null);
  }

  return (
    <Stack>
      <Group justify="space-between" align="flex-start">
        <div>
          <Title order={2}>Follow-ups</Title>
          <Text c="dimmed" size="sm">
            Reminders you have set. Anything due today or earlier also shows on the Dashboard.
          </Text>
        </div>
        <Button leftSection={<IconPlus size={16} />} onClick={openCreate}>
          Schedule follow-up
        </Button>
      </Group>

      <FilterBar
        search={search}
        onSearch={setSearch}
        searchPlaceholder="Search notes, contacts, gigs…"
        pills={FILTERS.map((f) => ({ ...f, active: filter === f.value }))}
        onPillClick={(value) => setFilter(value as Filter)}
      />

      {followUps.isPending ? (
        <Loader />
      ) : followUps.isError ? (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          Could not load follow-ups.
        </Alert>
      ) : visible.length === 0 ? (
        <Text c="dimmed" size="sm">
          {followUps.data?.length === 0
            ? 'No follow-ups yet. Schedule one to get a nudge when it comes due.'
            : 'Nothing matches this filter.'}
        </Text>
      ) : (
        <Stack gap="xs">
          {visible.map((f) => {
            const pending = f.completed_at === null;
            const overdue = pending && parseDateLocal(f.due_date) < today;
            const target = f.opportunity_id
              ? `/pipeline/${f.opportunity_id}`
              : f.contact_id
                ? `/contacts/${f.contact_id}`
                : null;
            const label = [f.contact_name, f.opportunity_title].filter(Boolean).join(' · ');

            return (
              <Card
                key={f.id}
                withBorder
                radius="md"
                padding="sm"
                style={{ borderColor: BRAND_LINE, opacity: pending ? 1 : 0.6 }}
              >
                <Group justify="space-between" wrap="nowrap" align="flex-start">
                  <div style={{ minWidth: 0 }}>
                    <Group gap="xs" wrap="nowrap">
                      <Text fw={600} size="sm">
                        {parseDateLocal(f.due_date).toLocaleDateString()}
                      </Text>
                      {overdue && (
                        <Badge color="warn" variant="light" size="xs">
                          overdue
                        </Badge>
                      )}
                      {!pending && (
                        <Badge color="good" variant="light" size="xs">
                          done
                        </Badge>
                      )}
                      {/* A dashboard-only reminder is a legitimate choice, not a fault — it is
                          worth showing so an email that never arrives is explicable. */}
                      {pending && !f.remind_by_email && (
                        <Badge color="gray" variant="light" size="xs">
                          no email
                        </Badge>
                      )}
                    </Group>
                    {label &&
                      (target ? (
                        <Anchor component={Link} to={target} size="xs">
                          {label}
                        </Anchor>
                      ) : (
                        <Text size="xs" c="dimmed">
                          {label}
                        </Text>
                      ))}
                    <Text size="sm" mt={4}>
                      {f.note}
                    </Text>
                  </div>

                  <Group gap={4} wrap="nowrap" style={{ flexShrink: 0 }}>
                    <ActionIcon
                      variant="subtle"
                      aria-label={pending ? 'Mark done' : 'Reopen'}
                      title={pending ? 'Mark done' : 'Reopen'}
                      onClick={() => patch.mutate({ id: f.id, completed: pending })}
                    >
                      {pending ? <IconCheck size={16} /> : <IconArrowBackUp size={16} />}
                    </ActionIcon>
                    <ActionIcon
                      variant="subtle"
                      aria-label="Edit"
                      title="Edit"
                      onClick={() => openEdit(f)}
                    >
                      <IconPencil size={16} />
                    </ActionIcon>
                    <ActionIcon
                      variant="subtle"
                      color="red"
                      aria-label="Delete"
                      title="Delete"
                      onClick={() => remove.mutate({ id: f.id })}
                    >
                      <IconTrash size={16} />
                    </ActionIcon>
                  </Group>
                </Group>
              </Card>
            );
          })}
        </Stack>
      )}

      <FollowUpFormModal opened={formOpen} onClose={closeForm} followUp={editing} />
    </Stack>
  );
}