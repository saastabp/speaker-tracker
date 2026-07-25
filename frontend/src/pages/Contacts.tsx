import { Alert, Anchor, Badge, Button, Group, Loader, Stack, Table, Text, Title } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconAlertTriangle, IconPlus, IconStar } from '@tabler/icons-react';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useCatalogs } from '../api/catalogs';
import { useContacts, useCreateContact, type ContactInput } from '../api/contacts';
import { ContactFormModal } from '../components/ContactFormModal';
import { FilterBar, type FilterPill } from '../components/FilterBar';
import { warmthColor } from '../contactChips';

export function Contacts() {
  const contacts = useContacts();
  const catalogs = useCatalogs();
  const create = useCreateContact();
  const navigate = useNavigate();
  const [addOpen, addHandlers] = useDisclosure(false);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('everyone');

  const warmthLabel = (shortName: string | null) =>
    shortName
      ? (catalogs.data?.warmth_tiers.find((tier) => tier.short_name === shortName)?.description ??
        shortName)
      : '';

  async function handleCreate(values: ContactInput) {
    const created = await create.mutateAsync(values);
    navigate(`/contacts/${created.id}`);
  }

  const all = contacts.data ?? [];
  const powerCount = all.filter((c) => c.is_power_partner).length;
  const pills: FilterPill[] = [
    { value: 'everyone', label: 'Everyone', active: filter === 'everyone' },
    { value: 'power', label: 'Power partners', active: filter === 'power' },
  ];
  const term = search.trim().toLowerCase();
  const filtered = all.filter(
    (c) =>
      (filter === 'everyone' || c.is_power_partner) &&
      (!term ||
        c.name.toLowerCase().includes(term) ||
        (c.email ?? '').toLowerCase().includes(term)),
  );

  return (
    <Stack>
      <Group justify="space-between" align="flex-start">
        <div>
          <Title order={2} c="navy.9">
            Contacts
          </Title>
          <Text c="dimmed" size="sm">
            {all.length} {all.length === 1 ? 'person' : 'people'} · {powerCount} power partner
            {powerCount === 1 ? '' : 's'}
          </Text>
        </div>
        <Button leftSection={<IconPlus size={16} />} onClick={addHandlers.open}>
          Add contact
        </Button>
      </Group>

      {contacts.isLoading && (
        <Group>
          <Loader size="sm" />
          <Text>Loading contacts…</Text>
        </Group>
      )}
      {contacts.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={18} />}>
          {contacts.error.message}
        </Alert>
      )}

      {contacts.data && contacts.data.length > 0 && (
        <>
          <FilterBar
            search={search}
            onSearch={setSearch}
            searchPlaceholder="Search contacts…"
            pills={pills}
            onPillClick={setFilter}
          />
          {filtered.length === 0 ? (
            <Text c="dimmed">No contacts match these filters.</Text>
          ) : (
            <Table highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Name</Table.Th>
                  <Table.Th>Warmth</Table.Th>
                  <Table.Th>Venues</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {filtered.map((contact) => (
                  <Table.Tr
                    key={contact.id}
                    style={{ cursor: 'pointer' }}
                    onClick={() => navigate(`/contacts/${contact.id}`)}
                  >
                    <Table.Td>
                      <Group gap={6} wrap="nowrap">
                        {contact.is_power_partner && (
                          <IconStar size={14} color="var(--mantine-color-gold-6)" />
                        )}
                        <div>
                          <Anchor
                            component={Link}
                            to={`/contacts/${contact.id}`}
                            fw={600}
                            onClick={(e) => e.stopPropagation()}
                          >
                            {contact.name}
                          </Anchor>
                          {contact.is_power_partner && (
                            <Text size="xs" c="dimmed">
                              Power partner
                            </Text>
                          )}
                        </div>
                      </Group>
                    </Table.Td>
                    <Table.Td>
                      {contact.warmth_tier && (
                        <Badge color={warmthColor(contact.warmth_tier)} variant="light">
                          {warmthLabel(contact.warmth_tier)}
                        </Badge>
                      )}
                    </Table.Td>
                    <Table.Td>{contact.organization_count}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
        </>
      )}

      {contacts.data?.length === 0 && <Text c="dimmed">No contacts yet.</Text>}

      <ContactFormModal
        opened={addOpen}
        onClose={addHandlers.close}
        title="Add contact"
        submitLabel="Add contact"
        dedupe
        onSubmit={handleCreate}
      />
    </Stack>
  );
}