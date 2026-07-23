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
  Switch,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconArrowLeft, IconPencil, IconTrash } from '@tabler/icons-react';
import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useCatalogs } from '../api/catalogs';
import { ApiError } from '../api/client';
import {
  useAddAffiliation,
  useContact,
  useDeleteContact,
  useDetachAffiliation,
  useEditAffiliation,
  useUpdateContact,
  type ContactInput,
} from '../api/contacts';
import { useOrganizations } from '../api/organizations';
import { AffiliationRow } from '../components/AffiliationRow';
import { ContactFormModal } from '../components/ContactFormModal';

export function ContactDetail() {
  const { id } = useParams();
  const contactId = Number(id);
  const contact = useContact(contactId);
  const catalogs = useCatalogs();
  const venues = useOrganizations();
  const update = useUpdateContact(contactId);
  const remove = useDeleteContact();
  const addAffiliation = useAddAffiliation(contactId);
  const editAffiliation = useEditAffiliation();
  const detachAffiliation = useDetachAffiliation();
  const navigate = useNavigate();
  const [editOpen, editHandlers] = useDisclosure(false);
  const [newVenue, setNewVenue] = useState<string | null>(null);
  const [newTitle, setNewTitle] = useState('');
  const [newPrimary, setNewPrimary] = useState(false);
  const [newPowerPartner, setNewPowerPartner] = useState(false);

  if (contact.isPending) {
    return <Loader />;
  }
  if (contact.isError) {
    const notFound = contact.error instanceof ApiError && contact.error.status === 404;
    return <Alert color="red">{notFound ? 'Contact not found.' : contact.error.message}</Alert>;
  }

  const c = contact.data;
  const warmthLabel = c.warmth_tier
    ? (catalogs.data?.warmth_tiers.find((tier) => tier.short_name === c.warmth_tier)?.description ??
      c.warmth_tier)
    : null;

  const affiliatedIds = new Set(c.organizations.map((org) => org.organization_id));
  const availableVenues = (venues.data ?? []).filter((venue) => !affiliatedIds.has(venue.id));

  async function handleUpdate(values: ContactInput) {
    await update.mutateAsync(values);
  }

  async function handleDelete() {
    if (!window.confirm(`Delete “${c.name}”? This hides them but keeps history.`)) {
      return;
    }
    await remove.mutateAsync(contactId);
    navigate('/contacts');
  }

  async function handleAddAffiliation() {
    if (!newVenue) {
      return;
    }
    await addAffiliation.mutateAsync({
      organization_id: Number(newVenue),
      title: newTitle || null,
      is_primary: newPrimary,
      is_power_partner: newPowerPartner,
    });
    setNewVenue(null);
    setNewTitle('');
    setNewPrimary(false);
    setNewPowerPartner(false);
  }

  return (
    <Stack>
      <Anchor component={Link} to="/contacts" size="sm">
        <Group gap={4}>
          <IconArrowLeft size={14} />
          Contacts
        </Group>
      </Anchor>

      <Group justify="space-between" align="flex-start">
        <Group gap="sm">
          <Title order={2} c="navy.9">
            {c.name}
          </Title>
          {warmthLabel && <Badge variant="light">{warmthLabel}</Badge>}
        </Group>
        <Group>
          <Button
            variant="default"
            leftSection={<IconPencil size={16} />}
            onClick={editHandlers.open}
          >
            Edit
          </Button>
          <Button
            variant="light"
            color="red"
            leftSection={<IconTrash size={16} />}
            onClick={handleDelete}
          >
            Delete
          </Button>
        </Group>
      </Group>

      <Group gap="xl">
        {c.email && (
          <Anchor href={`mailto:${c.email}`} size="sm">
            {c.email}
          </Anchor>
        )}
        {c.phone && <Text size="sm">{c.phone}</Text>}
        {c.source && (
          <Text size="sm" c="dimmed">
            Source: {c.source}
          </Text>
        )}
      </Group>
      {c.how_you_know && (
        <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
          {c.how_you_know}
        </Text>
      )}

      <Card withBorder radius="md">
        <Text fw={600} mb="sm">
          Affiliations ({c.organizations.length})
        </Text>
        <Stack gap="sm">
          {c.organizations.length === 0 && (
            <Text c="dimmed" size="sm">
              Not affiliated with any venue yet.
            </Text>
          )}
          {[...c.organizations]
            .sort((a, b) => a.organization_name.localeCompare(b.organization_name))
            .map((org) => (
            <AffiliationRow
              key={org.organization_id}
              label={org.organization_name}
              linkTo={`/venues/${org.organization_id}`}
              values={{
                title: org.title,
                is_primary: org.is_primary,
                is_power_partner: org.is_power_partner,
              }}
              onSave={(values) =>
                editAffiliation.mutate({
                  contactId,
                  organizationId: org.organization_id,
                  data: values,
                })
              }
              onRemove={() => {
                if (window.confirm(`Remove affiliation with ${org.organization_name}?`)) {
                  detachAffiliation.mutate({ contactId, organizationId: org.organization_id });
                }
              }}
            />
          ))}
        </Stack>

        {availableVenues.length > 0 && (
          <Group align="flex-end" mt="md" gap="sm">
            <Select
              label="Add to venue"
              placeholder="Select a venue"
              data={availableVenues.map((venue) => ({ value: String(venue.id), label: venue.name }))}
              searchable
              value={newVenue}
              onChange={setNewVenue}
              style={{ flex: 1 }}
            />
            <TextInput
              label="Title"
              placeholder="Role at this venue"
              value={newTitle}
              onChange={(event) => setNewTitle(event.currentTarget.value)}
            />
            <Switch
              label="Primary"
              mb={8}
              checked={newPrimary}
              onChange={(event) => setNewPrimary(event.currentTarget.checked)}
            />
            <Switch
              label="Power partner"
              mb={8}
              checked={newPowerPartner}
              onChange={(event) => setNewPowerPartner(event.currentTarget.checked)}
            />
            <Button
              onClick={handleAddAffiliation}
              disabled={!newVenue}
              loading={addAffiliation.isPending}
              mb={4}
            >
              Add
            </Button>
          </Group>
        )}
      </Card>

      {c.notes && (
        <Card withBorder radius="md">
          <Text fw={600} mb="xs">
            Notes
          </Text>
          <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
            {c.notes}
          </Text>
        </Card>
      )}

      <ContactFormModal
        opened={editOpen}
        onClose={editHandlers.close}
        title="Edit Contact"
        submitLabel="Save"
        initialValues={c}
        onSubmit={handleUpdate}
      />
    </Stack>
  );
}