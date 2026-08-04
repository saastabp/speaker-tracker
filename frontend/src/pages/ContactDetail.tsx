import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Grid,
  Group,
  Loader,
  Select,
  Stack,
  Switch,
  Text,
  TextInput,
  Timeline,
  Title,
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconMail, IconMessagePlus, IconPencil, IconStar, IconTrash } from '@tabler/icons-react';
import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { catalogLabel, useCatalogs } from '../api/catalogs';
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
import {
  useContactOutreaches,
  useContactTimeline,
  type Outreach,
  type TimelineItem,
} from '../api/outreaches';
import { useOrganizations } from '../api/organizations';
import { AffiliationRow } from '../components/AffiliationRow';
import { AppointmentsCard } from '../components/AppointmentsCard';
import { CardTitle, KV } from '../components/detailCards';
import { ContactFormModal } from '../components/ContactFormModal';
import { FollowUpsCard } from '../components/FollowUpsCard';
import { EmailComposer } from '../components/EmailComposer';
import { OutreachFormModal } from '../components/OutreachFormModal';
import { warmthColor } from '../contactChips';
import { timestampDate, timestampDateTime } from '../dates';

export function ContactDetail() {
  const { id } = useParams();
  const contactId = Number(id);
  const contact = useContact(contactId);
  const catalogs = useCatalogs();
  const venues = useOrganizations();
  const timeline = useContactTimeline(contactId);
  // The timeline is a projection — it carries a touch's text and channel but not the whole row, so
  // opening one for editing needs the outreach itself. Reusing the contact's own list rather than
  // adding a by-id route keeps this to a cache hit the Log-outreach modal already warms.
  const outreaches = useContactOutreaches(contactId);
  const update = useUpdateContact(contactId);
  const remove = useDeleteContact();
  const addAffiliation = useAddAffiliation(contactId);
  const editAffiliation = useEditAffiliation();
  const detachAffiliation = useDetachAffiliation();
  const navigate = useNavigate();
  const [editOpen, editHandlers] = useDisclosure(false);
  const [logOpen, logHandlers] = useDisclosure(false);
  const [composeOpen, composeHandlers] = useDisclosure(false);
  const [editingOutreach, setEditingOutreach] = useState<Outreach | null>(null);
  const [showAdd, setShowAdd] = useState(false);
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
  const warmthLabel = c.warmth_tier ? catalogLabel(catalogs.data?.warmth_tiers, c.warmth_tier) : null;
  // The Contact detail carries no power-partner rollup; derive it like the summary does
  // (a power partner at ≥1 affiliated venue).
  const isPowerPartner = c.organizations.some((org) => org.is_power_partner);
  const affiliatedIds = new Set(c.organizations.map((org) => org.organization_id));
  const availableVenues = (venues.data ?? []).filter((venue) => !affiliatedIds.has(venue.id));

  function timelineTitle(item: TimelineItem): string {
    if (item.item_type === 'outreach') {
      const channel = catalogLabel(catalogs.data?.outreach_channels, item.channel);
      const kind = catalogLabel(catalogs.data?.outreach_kinds, item.kind);
      return `Outreach · ${channel}${kind ? ` · ${kind}` : ''}`;
    }
    if (item.item_type === 'status_event') {
      return `Moved to ${catalogLabel(catalogs.data?.opportunity_statuses, item.status)}`;
    }
    return 'Note';
  }

  async function handleUpdate(values: ContactInput) {
    await update.mutateAsync(values);
  }

  /** Open a timeline entry for editing. Outreach rows only — a gig note and a status event belong
   *  to the opportunity that owns them, and are corrected there. */
  function openOutreach(sourceId: number) {
    const row = outreaches.data?.find((o) => o.id === sourceId);
    if (row) {
      setEditingOutreach(row);
      logHandlers.open();
    }
  }

  /** Open the modal to log a *new* touch. Clearing the target here rather than on close is what
   *  keeps the header button honest while still letting a closing edit dialog fade out as itself. */
  function openLogOutreach() {
    setEditingOutreach(null);
    logHandlers.open();
  }

  /** Close only — clearing `editingOutreach` here would re-render the still-visible dialog as a
   *  create form mid-fade, flipping its title and popping the template picker in. Both open paths
   *  set the target explicitly; see the note in `pages/Appointments.tsx`. */
  function closeOutreachForm() {
    logHandlers.close();
  }

  async function handleDelete() {
    if (!window.confirm(`Delete “${c.name}”? This hides them but keeps history.`)) {
      return;
    }
    await remove.mutateAsync(contactId);
    navigate('/contacts');
  }

  async function handleAddAffiliation() {
    if (!newVenue) return;
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
    setShowAdd(false);
  }

  return (
    <Stack>
      <Text size="sm" c="dimmed">
        <Anchor component={Link} to="/contacts" c="dimmed">
          Contacts
        </Anchor>{' '}
        ›{' '}
        <Text span fw={600} c="navy.9">
          {c.name}
        </Text>
      </Text>

      <Group justify="space-between" align="flex-start">
        <div>
          <Group gap={6} wrap="nowrap">
            {isPowerPartner && <IconStar size={20} color="var(--mantine-color-gold-6)" />}
            <Title order={2} c="navy.9">
              {c.name}
            </Title>
          </Group>
          <Group gap="xs" mt={6} align="center">
            {isPowerPartner && (
              <Badge color="terracotta" variant="light" leftSection={<IconStar size={12} />}>
                Power partner
              </Badge>
            )}
            {warmthLabel && (
              <Badge color={warmthColor(c.warmth_tier ?? null)} variant="light">
                {warmthLabel}
              </Badge>
            )}
          </Group>
        </div>
        <Group>
          <Button
            variant="default"
            leftSection={<IconMessagePlus size={16} />}
            onClick={openLogOutreach}
          >
            Log outreach
          </Button>
          {/* Composing here — rather than handing the address to Outlook — is what keeps the send
              in the CRM: the composer writes an `outreaches` row, so the touch reaches the journal,
              the contact timeline and the dashboard targets. */}
          <Button leftSection={<IconMail size={16} />} onClick={composeHandlers.open}>
            Compose email
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
                  availableVenues.length > 0 && (
                    <Anchor size="sm" onClick={() => setShowAdd((s) => !s)} style={{ cursor: 'pointer' }}>
                      + Add affiliation
                    </Anchor>
                  )
                }
              >
                Affiliations ({c.organizations.length})
              </CardTitle>
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

              {showAdd && availableVenues.length > 0 && (
                <Group align="flex-end" mt="md" gap="sm">
                  <Select
                    label="Add to venue"
                    placeholder="Select a venue"
                    data={availableVenues.map((venue) => ({
                      value: String(venue.id),
                      label: venue.name,
                    }))}
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

            <Card withBorder radius="md">
              <CardTitle
                action={
                  <Text size="xs" c="dimmed">
                    every touch, across all orgs — click one to edit
                  </Text>
                }
              >
                Activity
              </CardTitle>
              {timeline.isPending ? (
                <Loader size="sm" />
              ) : (timeline.data?.length ?? 0) === 0 ? (
                <Text c="dimmed" size="sm">
                  No outreach or gig activity yet. Log your first touch above.
                </Text>
              ) : (
                <Timeline bulletSize={14} lineWidth={2}>
                  {(timeline.data ?? []).map((item) => (
                    <Timeline.Item
                      key={`${item.item_type}-${item.source_id}`}
                      title={
                        item.item_type === 'outreach' ? (
                          <Anchor
                            component="button"
                            type="button"
                            size="sm"
                            fw={500}
                            onClick={() => openOutreach(item.source_id)}
                          >
                            {timelineTitle(item)}
                          </Anchor>
                        ) : (
                          timelineTitle(item)
                        )
                      }
                    >
                      {item.text && (
                        <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
                          {item.text}
                        </Text>
                      )}
                      {item.opportunity_title && (
                        <Anchor component={Link} to={`/pipeline/${item.opportunity_id}`} size="xs">
                          {item.opportunity_title}
                        </Anchor>
                      )}
                      <Text size="xs" c="dimmed">
                        {timestampDateTime(item.occurred_at)}
                      </Text>
                    </Timeline.Item>
                  ))}
                </Timeline>
              )}
            </Card>

            {c.notes && (
              <Card withBorder radius="md">
                <CardTitle>Notes</CardTitle>
                <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
                  {c.notes}
                </Text>
              </Card>
            )}
          </Stack>
        </Grid.Col>

        {/* RIGHT column */}
        <Grid.Col span={{ base: 12, md: 5 }}>
          <Stack>
            <FollowUpsCard contactId={c.id} />

            <AppointmentsCard contactId={c.id} />

            <Card withBorder radius="md">
              <CardTitle>Reach</CardTitle>
              <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '8px 16px' }}>
                <KV label="Email">
                  {/* Plain text, deliberately not a mailto: link. A mailto: hands the send to
                      Outlook, which writes no `outreaches` row — the touch would then be invisible
                      to the journal, the timeline and the targets. "Compose email" above is the
                      way out of this page. */}
                  {c.email ? c.email : '—'}
                </KV>
                <KV label="Phone">{c.phone?.trim() ? c.phone : '—'}</KV>
              </div>
            </Card>

            <Card withBorder radius="md">
              <CardTitle>Relationship</CardTitle>
              <Stack gap="sm">
                {isPowerPartner && (
                  <Group gap="xs" wrap="nowrap" align="center">
                    <Badge color="terracotta" variant="light" leftSection={<IconStar size={12} />}>
                      Power partner
                    </Badge>
                    <Text size="xs" c="dimmed">
                      referral ally — nurture, don't pitch
                    </Text>
                  </Group>
                )}
                {warmthLabel && (
                  <Group gap="xs" wrap="nowrap" align="center">
                    <Badge color={warmthColor(c.warmth_tier ?? null)} variant="light">
                      {warmthLabel}
                    </Badge>
                    <Text size="xs" c="dimmed">
                      warmth is the person, not any one org
                    </Text>
                  </Group>
                )}
                {!isPowerPartner && !warmthLabel && (
                  <Text size="sm" c="dimmed">
                    No relationship markers yet.
                  </Text>
                )}
              </Stack>
            </Card>

            <Card withBorder radius="md">
              <CardTitle>Details</CardTitle>
              <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '8px 16px' }}>
                {c.how_you_know?.trim() && <KV label="Warm intro">{c.how_you_know}</KV>}
                <KV label="Source">{c.source?.trim() ? c.source : '—'}</KV>
                <KV label="Added">{timestampDate(c.created_at)}</KV>
              </div>
            </Card>
          </Stack>
        </Grid.Col>
      </Grid>

      <ContactFormModal
        opened={editOpen}
        onClose={editHandlers.close}
        title="Edit contact"
        submitLabel="Save"
        initialValues={c}
        onSubmit={handleUpdate}
      />
      <OutreachFormModal
        opened={logOpen}
        onClose={closeOutreachForm}
        contactId={contactId}
        contactName={c.name}
        outreach={editingOutreach}
      />
      <EmailComposer
        opened={composeOpen}
        onClose={composeHandlers.close}
        contactId={contactId}
        contactName={c.name}
        contactEmail={c.email}
        onSent={(threadId) => navigate(`/emails/${threadId}`)}
      />
    </Stack>
  );
}