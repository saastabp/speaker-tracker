import {
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
import { IconArrowLeft, IconPencil, IconStar, IconTrash } from '@tabler/icons-react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useCatalogs } from '../api/catalogs';
import { ApiError } from '../api/client';
import {
  useDeleteOrganization,
  useOrganization,
  useUpdateOrganization,
  type OrganizationInput,
} from '../api/organizations';
import { VenueFormModal } from '../components/VenueFormModal';

/** One labelled block in the Kindling research panel. */
function ResearchField({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div>
      <Text size="xs" tt="uppercase" fw={600} c="dimmed">
        {label}
      </Text>
      <Text style={{ whiteSpace: 'pre-wrap' }}>{value?.trim() ? value : '—'}</Text>
    </div>
  );
}

export function VenueDetail() {
  const { id } = useParams();
  const venueId = Number(id);
  const venue = useOrganization(venueId);
  const catalogs = useCatalogs();
  const update = useUpdateOrganization(venueId);
  const remove = useDeleteOrganization();
  const navigate = useNavigate();
  const [editOpen, editHandlers] = useDisclosure(false);

  if (venue.isPending) {
    return <Loader />;
  }
  if (venue.isError) {
    const notFound = venue.error instanceof ApiError && venue.error.status === 404;
    return (
      <Alert color="red">{notFound ? 'Venue not found.' : venue.error.message}</Alert>
    );
  }

  const v = venue.data;
  const typeLabel =
    catalogs.data?.organization_types.find((type) => type.short_name === v.organization_type)
      ?.description ?? v.organization_type;

  async function handleUpdate(values: OrganizationInput) {
    await update.mutateAsync(values);
  }

  async function handleDelete() {
    if (!window.confirm(`Delete “${v.name}”? This hides it but keeps its history.`)) {
      return;
    }
    await remove.mutateAsync(venueId);
    navigate('/venues');
  }

  return (
    <Stack>
      <Anchor component={Link} to="/venues" size="sm">
        <Group gap={4}>
          <IconArrowLeft size={14} />
          Venues
        </Group>
      </Anchor>

      <Group justify="space-between" align="flex-start">
        <div>
          <Group gap="sm">
            <Title order={2} c="navy.9">
              {v.name}
            </Title>
            {v.research_ready ? (
              <Badge color="green">Research-ready</Badge>
            ) : (
              <Badge color="gray" variant="light">
                Not research-ready
              </Badge>
            )}
          </Group>
          <Text c="dimmed">{typeLabel}</Text>
        </div>
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
        {v.location && <Text size="sm">📍 {v.location}</Text>}
        {v.website_url && (
          <Anchor href={v.website_url} target="_blank" size="sm">
            {v.website_url}
          </Anchor>
        )}
        {v.email_domain && (
          <Text size="sm" c="dimmed">
            @{v.email_domain}
          </Text>
        )}
      </Group>

      <Card withBorder radius="md">
        <Text fw={600} mb="sm">
          Research
        </Text>
        <Stack gap="md">
          <ResearchField label="What it is" value={v.what_it_is} />
          <ResearchField label="Why it fits" value={v.why_it_fits} />
          <ResearchField label="How to approach" value={v.how_to_approach} />
        </Stack>
      </Card>

      <Card withBorder radius="md">
        <Text fw={600} mb="sm">
          Contacts ({v.contacts.length})
        </Text>
        {v.contacts.length === 0 ? (
          <Text c="dimmed" size="sm">
            No contacts yet — add a contact and affiliate them with this venue.
          </Text>
        ) : (
          <Stack gap="xs">
            {v.contacts.map((contact) => (
              <Group key={contact.contact_id} gap="sm">
                <Anchor component={Link} to={`/contacts/${contact.contact_id}`}>
                  {contact.name}
                </Anchor>
                {contact.is_power_partner && <IconStar size={14} color="var(--mantine-color-gold-6)" />}
                {contact.title && (
                  <Text size="sm" c="dimmed">
                    {contact.title}
                  </Text>
                )}
                {contact.is_primary && (
                  <Badge size="xs" variant="light">
                    Primary
                  </Badge>
                )}
              </Group>
            ))}
          </Stack>
        )}
      </Card>

      <VenueFormModal
        opened={editOpen}
        onClose={editHandlers.close}
        title="Edit Venue"
        submitLabel="Save"
        initialValues={v}
        onSubmit={handleUpdate}
      />
    </Stack>
  );
}