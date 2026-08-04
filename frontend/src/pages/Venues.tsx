import { Alert, Anchor, Badge, Button, Group, Loader, Stack, Table, Text, Title } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconAlertTriangle, IconMessagePlus, IconPlus } from '@tabler/icons-react';
import { Link, useNavigate } from 'react-router-dom';
import { catalogLabel, useCatalogs } from '../api/catalogs';
import {
  useCreateOrganization,
  useOrganizations,
  type OrganizationInput,
} from '../api/organizations';
import { FilterBar, type FilterPill } from '../components/FilterBar';
import { OutreachFormModal } from '../components/OutreachFormModal';
import { VenueFormModal } from '../components/VenueFormModal';
import { windowLabel } from '../dates';
import { useFilterParams } from '../urlFilters';
import { orgTypeColor } from '../venueChips';

const READY = '__ready';
/** Sentinel for the pill that displays (and clears) the link-driven researched-window filter. */
const READY_WINDOW = '__ready_window';

/** Whether a venue became research-ready inside `[from, to)`. No window means no constraint.
 *
 *  Compares the ISO date prefix rather than parsing: `research_ready_at` already arrives in the
 *  user's zone, and string compare on `YYYY-MM-DD` is both correct and immune to the UTC-parse
 *  trap. A venue that never crossed the bar is excluded, not included — a window asking "what was
 *  researched in April" must not answer with things that were never researched at all. */
function inReadyWindow(readyAt: string | null, from: string, to: string): boolean {
  if (!from && !to) return true;
  if (!readyAt) return false;
  const day = readyAt.slice(0, 10);
  return (!from || day >= from) && (!to || day < to);
}

function firstLine(text: string | null): string {
  return text ? text.split('\n')[0] : '';
}

/** Research-readiness dot + label (mockup `.ready`); the "N/3" fraction needs backend, deferred. */
function ResearchDot({ ready }: { ready: boolean }) {
  return (
    <Group gap={6} wrap="nowrap">
      <span
        style={{
          width: 8,
          height: 8,
          borderRadius: '50%',
          background: ready ? 'var(--mantine-color-good-6)' : 'var(--mantine-color-gray-4)',
        }}
      />
      <Text size="sm" c={ready ? undefined : 'dimmed'}>
        {ready ? 'Ready' : 'Not ready'}
      </Text>
    </Group>
  );
}

export function Venues() {
  const venues = useOrganizations();
  const catalogs = useCatalogs();
  const create = useCreateOrganization();
  const navigate = useNavigate();
  const [addOpen, addHandlers] = useDisclosure(false);
  const [logOpen, logHandlers] = useDisclosure(false);
  // Filter state lives in the URL — see `useFilterParams`.
  const params = useFilterParams();
  const search = params.get('q');
  const typeFilter = params.get('type', 'all');
  const readyOnly = params.has('ready', '1');
  // Arrives from the Dashboard's "new venues researched" tile, never set on this page.
  const readyFrom = params.get('ready_from');
  const readyTo = params.get('ready_to');

  const typeLabel = (shortName: string) =>
    catalogLabel(catalogs.data?.organization_types, shortName);

  async function handleCreate(values: OrganizationInput) {
    const created = await create.mutateAsync(values);
    navigate(`/venues/${created.id}`);
  }

  const all = venues.data ?? [];
  const readyCount = all.filter((v) => v.research_ready).length;
  const presentTypes = new Set(all.map((v) => v.organization_type));
  const orderedTypes = (catalogs.data?.organization_types ?? [])
    .filter((t) => presentTypes.has(t.short_name))
    .map((t) => t.short_name);

  const pills: FilterPill[] = [
    { value: 'all', label: 'All types', active: typeFilter === 'all' },
    ...orderedTypes.map((t) => ({ value: t, label: typeLabel(t), active: typeFilter === t })),
    { value: READY, label: 'Ready only', active: readyOnly },
    // No permanent control — it arrives from a Dashboard link. It still has to be *visible*: a
    // filter that silently hides most of the list leaves the reader unable to tell why, or to
    // undo it. Shown only while on, and clicking clears it (same rule as Pipeline's link pills).
    ...(readyFrom || readyTo
      ? [
          {
            value: READY_WINDOW,
            label: `Researched ${windowLabel(readyFrom, readyTo)}`,
            active: true,
            removable: true,
          },
        ]
      : []),
  ];
  function handlePill(value: string) {
    if (value === READY) params.toggle('ready', '1');
    else if (value === READY_WINDOW) params.setMany({ ready_from: '', ready_to: '' });
    else params.set('type', value, 'all');
  }

  const term = search.trim().toLowerCase();
  const filtered = all.filter(
    (v) =>
      (typeFilter === 'all' || v.organization_type === typeFilter) &&
      (!readyOnly || v.research_ready) &&
      inReadyWindow(v.research_ready_at, readyFrom, readyTo) &&
      (!term || v.name.toLowerCase().includes(term)),
  );

  return (
    <Stack>
      <Group justify="space-between" align="flex-start">
        <div>
          <Title order={2} c="navy.9">
            Venues &amp; Organizations
          </Title>
          <Text c="dimmed" size="sm">
            {all.length} tracked · {readyCount} outreach-ready
          </Text>
        </div>
        <Group>
          <Button
            variant="default"
            leftSection={<IconMessagePlus size={16} />}
            onClick={logHandlers.open}
          >
            Log outreach
          </Button>
          <Button leftSection={<IconPlus size={16} />} onClick={addHandlers.open}>
            Add venue
          </Button>
        </Group>
      </Group>

      {venues.isLoading && (
        <Group>
          <Loader size="sm" />
          <Text>Loading venues…</Text>
        </Group>
      )}
      {venues.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={18} />}>
          {venues.error.message}
        </Alert>
      )}

      {venues.data && venues.data.length > 0 && (
        <>
          <FilterBar
            search={search}
            onSearch={(value) => params.set('q', value)}
            searchPlaceholder="Search venues…"
            pills={pills}
            onPillClick={handlePill}
          />
          {filtered.length === 0 ? (
            <Text c="dimmed">No venues match these filters.</Text>
          ) : (
            <Table highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Organization</Table.Th>
                  <Table.Th>Type</Table.Th>
                  <Table.Th>Why it fits</Table.Th>
                  <Table.Th>Research</Table.Th>
                  <Table.Th>Contacts</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {filtered.map((venue) => (
                  <Table.Tr
                    key={venue.id}
                    style={{ cursor: 'pointer' }}
                    onClick={() => navigate(`/venues/${venue.id}`)}
                  >
                    <Table.Td>
                      <Anchor
                        component={Link}
                        to={`/venues/${venue.id}`}
                        fw={600}
                        onClick={(e) => e.stopPropagation()}
                      >
                        {venue.name}
                      </Anchor>
                      {venue.location && (
                        <Text size="xs" c="dimmed">
                          {venue.location}
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <Badge color={orgTypeColor(venue.organization_type)} variant="light">
                        {typeLabel(venue.organization_type)}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" c="dimmed" lineClamp={2}>
                        {firstLine(venue.why_it_fits)}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <ResearchDot ready={venue.research_ready} />
                    </Table.Td>
                    <Table.Td>{venue.contact_count}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
        </>
      )}

      {venues.data?.length === 0 && (
        <Text c="dimmed">No venues yet. Add one as you research it.</Text>
      )}

      <VenueFormModal
        opened={addOpen}
        onClose={addHandlers.close}
        title="Add venue"
        submitLabel="Add venue"
        onSubmit={handleCreate}
      />
      <OutreachFormModal opened={logOpen} onClose={logHandlers.close} />
    </Stack>
  );
}