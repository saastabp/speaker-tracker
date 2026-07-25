import { Alert, Anchor, Badge, Button, Card, Grid, Group, Loader, Stack, Text, Title } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconMessagePlus, IconPencil, IconTrash } from '@tabler/icons-react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useCatalogs } from '../api/catalogs';
import { ApiError } from '../api/client';
import { useDetachAffiliation, useEditAffiliation } from '../api/contacts';
import {
  useDeleteOrganization,
  useOrganization,
  useUpdateOrganization,
  type OrganizationInput,
} from '../api/organizations';
import { useOpportunities } from '../api/opportunities';
import { AffiliationRow } from '../components/AffiliationRow';
import { CardTitle, KV } from '../components/detailCards';
import { LogOutreachModal } from '../components/LogOutreachModal';
import { VenueFormModal } from '../components/VenueFormModal';
import { stageColor } from '../opportunityChips';
import { orgTypeColor } from '../venueChips';

type Catalog = { short_name: string; description: string }[] | undefined;
const label = (list: Catalog, sn: string) =>
  list?.find((c) => c.short_name === sn)?.description ?? sn;

function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

/** One labelled block in the Kindling research panel. */
function KindlingField({ label: k, value }: { label: string; value: string | null | undefined }) {
  return (
    <div>
      <Text size="xs" tt="uppercase" fw={600} c="dimmed">
        {k}
      </Text>
      <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
        {value?.trim() ? value : '—'}
      </Text>
    </div>
  );
}

export function VenueDetail() {
  const { id } = useParams();
  const venueId = Number(id);
  const venue = useOrganization(venueId);
  const catalogs = useCatalogs();
  const opportunities = useOpportunities();
  const update = useUpdateOrganization(venueId);
  const remove = useDeleteOrganization();
  const editAffiliation = useEditAffiliation();
  const detachAffiliation = useDetachAffiliation();
  const navigate = useNavigate();
  const [editOpen, editHandlers] = useDisclosure(false);
  const [logOpen, logHandlers] = useDisclosure(false);

  if (venue.isPending) {
    return <Loader />;
  }
  if (venue.isError) {
    const notFound = venue.error instanceof ApiError && venue.error.status === 404;
    return <Alert color="red">{notFound ? 'Venue not found.' : venue.error.message}</Alert>;
  }

  const v = venue.data;
  const typeLabel = label(catalogs.data?.organization_types, v.organization_type);
  const venueOpps = (opportunities.data ?? []).filter((o) => o.organization_id === venueId);

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
      <Text size="sm" c="dimmed">
        <Anchor component={Link} to="/venues" c="dimmed">
          Venues &amp; Orgs
        </Anchor>{' '}
        ›{' '}
        <Text span fw={600} c="navy.9">
          {v.name}
        </Text>
      </Text>

      <Group justify="space-between" align="flex-start">
        <div>
          <Title order={2} c="navy.9">
            {v.name}
          </Title>
          <Group gap="xs" mt={6} align="center">
            <Badge color={orgTypeColor(v.organization_type)} variant="light">
              {typeLabel}
            </Badge>
            {v.website_url && (
              <Anchor href={v.website_url} target="_blank" size="sm" c="dimmed">
                {v.website_url}
              </Anchor>
            )}
            <Badge color={v.research_ready ? 'good' : 'gray'} variant="light">
              {v.research_ready ? 'Outreach-ready' : 'Not research-ready'}
            </Badge>
          </Group>
        </div>
        <Group>
          <Button
            variant="default"
            leftSection={<IconMessagePlus size={16} />}
            onClick={logHandlers.open}
          >
            Log touch
          </Button>
          <Button variant="default" leftSection={<IconPencil size={16} />} onClick={editHandlers.open}>
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

      <Grid gutter="md" align="flex-start">
        {/* LEFT column */}
        <Grid.Col span={{ base: 12, md: 7 }}>
          <Stack>
            <Card withBorder radius="md">
              <CardTitle
                action={
                  <Anchor size="sm" onClick={editHandlers.open} style={{ cursor: 'pointer' }}>
                    Edit
                  </Anchor>
                }
              >
                Research — Kindling
              </CardTitle>
              <Stack gap="md">
                <KindlingField label="What it is" value={v.what_it_is} />
                <KindlingField label="Why it fits" value={v.why_it_fits} />
                <KindlingField label="How to approach" value={v.how_to_approach} />
              </Stack>
            </Card>

            <Card withBorder radius="md">
              <CardTitle>Opportunities</CardTitle>
              <Stack gap="sm">
                {venueOpps.length === 0 && (
                  <Text c="dimmed" size="sm">
                    No opportunities yet.
                  </Text>
                )}
                {venueOpps.map((o) => (
                  <Group key={o.id} justify="space-between" wrap="nowrap">
                    <Anchor component={Link} to={`/pipeline/${o.id}`} size="sm" fw={600}>
                      {o.title}
                    </Anchor>
                    <Badge color={stageColor(o.current_status)} variant="light">
                      {label(catalogs.data?.opportunity_statuses, o.current_status)}
                    </Badge>
                  </Group>
                ))}
              </Stack>
            </Card>
          </Stack>
        </Grid.Col>

        {/* RIGHT column */}
        <Grid.Col span={{ base: 12, md: 5 }}>
          <Stack>
            <Card withBorder radius="md">
              <CardTitle>Contacts ({v.contacts.length})</CardTitle>
              {v.contacts.length === 0 ? (
                <Text c="dimmed" size="sm">
                  No contacts yet — add a contact and affiliate them with this venue.
                </Text>
              ) : (
                <Stack gap="sm">
                  {[...v.contacts]
                    .sort((a, b) => a.name.localeCompare(b.name))
                    .map((contact) => (
                      <AffiliationRow
                        key={contact.contact_id}
                        label={contact.name}
                        linkTo={`/contacts/${contact.contact_id}`}
                        values={{
                          title: contact.title,
                          is_primary: contact.is_primary,
                          is_power_partner: contact.is_power_partner,
                        }}
                        onSave={(values) =>
                          editAffiliation.mutate({
                            contactId: contact.contact_id,
                            organizationId: venueId,
                            data: values,
                          })
                        }
                        onRemove={() => {
                          if (window.confirm(`Remove ${contact.name} from ${v.name}?`)) {
                            detachAffiliation.mutate({
                              contactId: contact.contact_id,
                              organizationId: venueId,
                            });
                          }
                        }}
                      />
                    ))}
                </Stack>
              )}
            </Card>

            <Card withBorder radius="md">
              <CardTitle>Details</CardTitle>
              <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '8px 16px' }}>
                <KV label="Type">{typeLabel}</KV>
                <KV label="Location">{v.location?.trim() ? v.location : '—'}</KV>
                <KV label="Added">{formatDate(v.created_at)}</KV>
              </div>
            </Card>
          </Stack>
        </Grid.Col>
      </Grid>

      <VenueFormModal
        opened={editOpen}
        onClose={editHandlers.close}
        title="Edit venue"
        submitLabel="Save"
        initialValues={v}
        onSubmit={handleUpdate}
      />
      <LogOutreachModal opened={logOpen} onClose={logHandlers.close} />
    </Stack>
  );
}