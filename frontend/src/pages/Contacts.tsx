import { Alert, Anchor, Badge, Button, Group, Loader, Stack, Table, Text, Title } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconAlertTriangle, IconPlus, IconStar } from '@tabler/icons-react';
import { Link, useNavigate } from 'react-router-dom';
import { useCatalogs } from '../api/catalogs';
import { useContacts, useCreateContact, type ContactInput } from '../api/contacts';
import { ContactFormModal } from '../components/ContactFormModal';

export function Contacts() {
  const contacts = useContacts();
  const catalogs = useCatalogs();
  const create = useCreateContact();
  const navigate = useNavigate();
  const [addOpen, addHandlers] = useDisclosure(false);

  const warmthLabel = (shortName: string | null) =>
    shortName
      ? (catalogs.data?.warmth_tiers.find((tier) => tier.short_name === shortName)?.description ??
        shortName)
      : '';

  async function handleCreate(values: ContactInput) {
    const created = await create.mutateAsync(values);
    navigate(`/contacts/${created.id}`);
  }

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={2} c="navy.9">
          Contacts
        </Title>
        <Button leftSection={<IconPlus size={16} />} onClick={addHandlers.open}>
          Add Contact
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

      {contacts.data?.length === 0 && <Text c="dimmed">No contacts yet.</Text>}

      {contacts.data && contacts.data.length > 0 && (
        <Table highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Name</Table.Th>
              <Table.Th>Email</Table.Th>
              <Table.Th>Warmth</Table.Th>
              <Table.Th>Venues</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {contacts.data.map((contact) => (
              <Table.Tr key={contact.id}>
                <Table.Td>
                  <Group gap={6}>
                    <Anchor component={Link} to={`/contacts/${contact.id}`}>
                      {contact.name}
                    </Anchor>
                    {contact.is_power_partner && (
                      <IconStar size={14} color="var(--mantine-color-gold-6)" />
                    )}
                  </Group>
                </Table.Td>
                <Table.Td>{contact.email}</Table.Td>
                <Table.Td>
                  {contact.warmth_tier && (
                    <Badge variant="light">{warmthLabel(contact.warmth_tier)}</Badge>
                  )}
                </Table.Td>
                <Table.Td>{contact.organization_count}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      <ContactFormModal
        opened={addOpen}
        onClose={addHandlers.close}
        title="Add Contact"
        submitLabel="Create"
        dedupe
        onSubmit={handleCreate}
      />
    </Stack>
  );
}