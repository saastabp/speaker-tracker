import { Alert, Anchor, Badge, Button, Group, Loader, Stack, Table, Text, Title } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconAlertTriangle, IconMessagePlus, IconPlus } from '@tabler/icons-react';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { catalogLabel, useCatalogs } from '../api/catalogs';
import {
  useCreateOrganization,
  useOrganizations,
  type OrganizationInput,
} from '../api/organizations';
import { FilterBar, type FilterPill } from '../components/FilterBar';
import { LogOutreachModal } from '../components/LogOutreachModal';
import { VenueFormModal } from '../components/VenueFormModal';
import { orgTypeColor } from '../venueChips';

const READY = '__ready';

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
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState('all');
  const [readyOnly, setReadyOnly] = useState(false);

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
  ];
  function handlePill(value: string) {
    if (value === READY) setReadyOnly((v) => !v);
    else setTypeFilter(value);
  }

  const term = search.trim().toLowerCase();
  const filtered = all.filter(
    (v) =>
      (typeFilter === 'all' || v.organization_type === typeFilter) &&
      (!readyOnly || v.research_ready) &&
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
            onSearch={setSearch}
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
      <LogOutreachModal opened={logOpen} onClose={logHandlers.close} />
    </Stack>
  );
}