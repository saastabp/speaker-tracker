import {
  ActionIcon,
  Alert,
  Anchor,
  Button,
  Card,
  Divider,
  Group,
  Loader,
  SegmentedControl,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconAlertTriangle, IconPencil, IconPlus } from '@tabler/icons-react';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAppointments, type Appointment, type AppointmentScope } from '../api/appointments';
import { AppointmentFormModal } from '../components/AppointmentFormModal';
import { FilterBar } from '../components/FilterBar';
import { timestampDateTime } from '../dates';
import { BRAND_LINE } from '../theme';
import { useFilterParams } from '../urlFilters';

type Grouping = 'date' | 'contact';

const SCOPES: { value: AppointmentScope; label: string }[] = [
  { value: 'upcoming', label: 'Upcoming' },
  { value: 'past', label: 'Past' },
];

interface AppointmentGroup {
  /** Null in date grouping — one bucket, no separator. */
  heading: string | null;
  contactId: number | null;
  rows: Appointment[];
}

/**
 * The appointments list — everything logged, split by the scope toggle.
 *
 * The Dashboard's "Coming up" card shows only what is ahead, and only a few. This page is the whole
 * record, which is why **past appointments are reachable here and nowhere else**: one typed with
 * the wrong year would otherwise be invisible the moment it was saved, and so impossible to fix.
 *
 * Grouping is a pure view over one fetch. *By date* is the server's own order — a flat
 * chronological list, each row carrying its own date. *By contact* re-buckets the same rows under
 * the person's name, so "everything with Kalei" is one glance rather than a scan. Both paths render
 * through the same row markup below; only the buckets differ.
 */
export function Appointments() {
  // Filter state lives in the URL — see `useFilterParams`. The defaults here (`upcoming`, `date`)
  // are the values that leave a clean URL; anything else is a deliberate choice worth linking to.
  const params = useFilterParams();
  const scope = params.get('scope', 'upcoming') as AppointmentScope;
  const grouping = params.get('group', 'date') as Grouping;
  const search = params.get('q');
  const appointments = useAppointments({ scope });
  const [editing, setEditing] = useState<Appointment | null>(null);
  const [formOpen, formHandlers] = useDisclosure(false);

  const visible = useMemo(() => {
    const rows = appointments.data ?? [];
    const term = search.trim().toLowerCase();
    if (!term) return rows;
    return rows.filter((a) =>
      [a.title, a.contact_name, a.details].filter(Boolean).join(' ').toLowerCase().includes(term),
    );
  }, [appointments.data, search]);

  const groups: AppointmentGroup[] = useMemo(() => {
    if (grouping !== 'contact') {
      return [{ heading: null, contactId: null, rows: visible }];
    }
    // Contacts alphabetical; each bucket keeps the server's time order.
    const byContact = new Map<number, AppointmentGroup>();
    for (const a of visible) {
      const bucket = byContact.get(a.contact_id) ?? {
        heading: a.contact_name,
        contactId: a.contact_id,
        rows: [],
      };
      bucket.rows.push(a);
      byContact.set(a.contact_id, bucket);
    }
    return [...byContact.values()].sort((a, b) => (a.heading ?? '').localeCompare(b.heading ?? ''));
  }, [visible, grouping]);

  function openCreate() {
    setEditing(null);
    formHandlers.open();
  }

  function openEdit(appointment: Appointment) {
    setEditing(appointment);
    formHandlers.open();
  }

  /** Close only — **do not clear `editing` here.**
   *
   * The modal stays mounted through its exit transition, so nulling the target on close re-renders
   * a still-visible dialog as a blank *create* form: the title flips from "Edit appointment" to
   * "Add appointment" and the fields empty out, for the length of the fade. Every open path sets
   * the target explicitly (`openCreate` nulls it, `openEdit` sets the row), so leaving it alone
   * here is safe and the dialog fades out showing what it was actually editing.
   */
  function closeForm() {
    formHandlers.close();
  }

  const past = scope === 'past';

  return (
    <Stack>
      <Group justify="space-between" align="flex-start">
        <div>
          <Title order={2}>Appointments</Title>
          <Text c="dimmed" size="sm">
            Meetings you have logged. Upcoming ones also show on the Dashboard.
          </Text>
        </div>
        <Button leftSection={<IconPlus size={16} />} onClick={openCreate}>
          Add appointment
        </Button>
      </Group>

      <FilterBar
        search={search}
        onSearch={(value) => params.set('q', value)}
        searchPlaceholder="Search titles, contacts, details…"
        pills={SCOPES.map((s) => ({ ...s, active: scope === s.value }))}
        onPillClick={(value) => params.set('scope', value, 'upcoming')}
        extra={
          <Group gap="xs" wrap="nowrap">
            <Text size="xs" c="dimmed">
              Group by
            </Text>
            <SegmentedControl
              size="xs"
              data={[
                { value: 'date', label: 'Date' },
                { value: 'contact', label: 'Contact' },
              ]}
              value={grouping}
              onChange={(value) => params.set('group', value, 'date')}
            />
          </Group>
        }
      />

      {appointments.isPending ? (
        <Loader />
      ) : appointments.isError ? (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          Could not load appointments.
        </Alert>
      ) : visible.length === 0 ? (
        <Text c="dimmed" size="sm">
          {(appointments.data?.length ?? 0) === 0
            ? past
              ? 'No past appointments.'
              : 'Nothing scheduled yet. Add one and it shows on the Dashboard.'
            : 'Nothing matches this filter.'}
        </Text>
      ) : (
        <Stack gap="lg">
          {groups.map((group) => (
            <Stack key={group.contactId ?? 'all'} gap="xs">
              {group.heading && (
                <div>
                  <Anchor
                    component={Link}
                    to={`/contacts/${group.contactId}`}
                    fw={700}
                    c="navy.9"
                    size="sm"
                  >
                    {group.heading}
                  </Anchor>
                  <Divider mt={4} color={BRAND_LINE} />
                </div>
              )}
              {group.rows.map((a) => (
                <Card
                  key={a.id}
                  withBorder
                  radius="md"
                  padding="sm"
                  style={{ borderColor: BRAND_LINE, opacity: past ? 0.6 : 1 }}
                >
                  <Group justify="space-between" wrap="nowrap" align="flex-start">
                    <div style={{ minWidth: 0 }}>
                      <Text fw={600} size="sm">
                        {timestampDateTime(a.scheduled_at)}
                      </Text>
                      <Text size="sm">{a.title}</Text>
                      {/* Under a contact heading the name would only repeat the separator. */}
                      {grouping !== 'contact' && (
                        <Anchor component={Link} to={`/contacts/${a.contact_id}`} size="xs">
                          {a.contact_name}
                        </Anchor>
                      )}
                      {a.details && (
                        <Text size="sm" c="dimmed" mt={4} style={{ whiteSpace: 'pre-wrap' }}>
                          {a.details}
                        </Text>
                      )}
                    </div>
                    <ActionIcon
                      variant="subtle"
                      aria-label="Edit"
                      title="Edit"
                      onClick={() => openEdit(a)}
                      style={{ flexShrink: 0 }}
                    >
                      <IconPencil size={16} />
                    </ActionIcon>
                  </Group>
                </Card>
              ))}
            </Stack>
          ))}
        </Stack>
      )}

      <AppointmentFormModal opened={formOpen} onClose={closeForm} appointment={editing} />
    </Stack>
  );
}