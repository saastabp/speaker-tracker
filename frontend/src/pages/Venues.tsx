import { Alert, Anchor, Badge, Button, Group, Loader, Stack, Table, Text, Title } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconAlertTriangle, IconMessagePlus, IconPlus } from '@tabler/icons-react';
import { Link, useNavigate } from 'react-router-dom';
import { useCatalogs } from '../api/catalogs';
import {
  useCreateOrganization,
  useOrganizations,
  type OrganizationInput,
} from '../api/organizations';
import { LogOutreachModal } from '../components/LogOutreachModal';
import { VenueFormModal } from '../components/VenueFormModal';

function firstLine(text: string | null): string {
  return text ? text.split('\n')[0] : '';
}

export function Venues() {
  const venues = useOrganizations();
  const catalogs = useCatalogs();
  const create = useCreateOrganization();
  const navigate = useNavigate();
  const [addOpen, addHandlers] = useDisclosure(false);
  const [logOpen, logHandlers] = useDisclosure(false);

  const typeLabel = (shortName: string) =>
    catalogs.data?.organization_types.find((type) => type.short_name === shortName)?.description ??
    shortName;

  async function handleCreate(values: OrganizationInput) {
    const created = await create.mutateAsync(values);
    navigate(`/venues/${created.id}`);
  }

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={2} c="navy.9">
          Venues
        </Title>
        <Group>
          <Button
            variant="default"
            leftSection={<IconMessagePlus size={16} />}
            onClick={logHandlers.open}
          >
            Log outreach
          </Button>
          <Button leftSection={<IconPlus size={16} />} onClick={addHandlers.open}>
            Add Venue
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

      {venues.data?.length === 0 && (
        <Text c="dimmed">No venues yet. Add one as you research it.</Text>
      )}

      {venues.data && venues.data.length > 0 && (
        <Table highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Name</Table.Th>
              <Table.Th>Type</Table.Th>
              <Table.Th>Location</Table.Th>
              <Table.Th>Why it fits</Table.Th>
              <Table.Th>Contacts</Table.Th>
              <Table.Th>Research</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {venues.data.map((venue) => (
              <Table.Tr key={venue.id}>
                <Table.Td>
                  <Anchor component={Link} to={`/venues/${venue.id}`}>
                    {venue.name}
                  </Anchor>
                </Table.Td>
                <Table.Td>{typeLabel(venue.organization_type)}</Table.Td>
                <Table.Td>{venue.location}</Table.Td>
                <Table.Td>{firstLine(venue.why_it_fits)}</Table.Td>
                <Table.Td>{venue.contact_count}</Table.Td>
                <Table.Td>
                  {venue.research_ready ? (
                    <Badge color="green">Ready</Badge>
                  ) : (
                    <Badge color="gray" variant="light">
                      Not ready
                    </Badge>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      <VenueFormModal
        opened={addOpen}
        onClose={addHandlers.close}
        title="Add Venue"
        submitLabel="Create"
        onSubmit={handleCreate}
      />
      <LogOutreachModal opened={logOpen} onClose={logHandlers.close} />
    </Stack>
  );
}