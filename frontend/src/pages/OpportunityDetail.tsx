import {
  ActionIcon,
  Alert,
  Anchor,
  Avatar,
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
  Textarea,
  TextInput,
  Timeline,
  Title,
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconPencil, IconPlus, IconTrash, IconX } from '@tabler/icons-react';
import { useEffect, useState, type ReactNode } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useCatalogs } from '../api/catalogs';
import { ApiError } from '../api/client';
import { useContacts } from '../api/contacts';
import {
  useAddOpportunityContact,
  useAddOpportunityNote,
  useDeleteOpportunity,
  useDeleteOpportunityNote,
  useOpportunity,
  usePatchPayment,
  useRemoveOpportunityContact,
  useUpdateOpportunity,
  useUpdateOpportunityContact,
  type Opportunity,
  type OpportunityContact,
  type OpportunityInput,
} from '../api/opportunities';
import { useOrganization } from '../api/organizations';
import { CloseOpportunityModal } from '../components/CloseOpportunityModal';
import { OpportunityFormModal } from '../components/OpportunityFormModal';
import { formatMoney, paymentColor, stageColor } from '../opportunityChips';
import { BRAND_FAINT, BRAND_LINE } from '../theme';

type Catalog = { short_name: string; description: string }[] | undefined;
const label = (list: Catalog, sn: string) =>
  list?.find((c) => c.short_name === sn)?.description ?? sn;

function formatWhen(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

/** First+second word initials, e.g. "Kauai Beach Resort & Spa" → "KB", "Iris Kealoha" → "IK". */
function initials(name: string): string {
  const words = name
    .trim()
    .split(/\s+/)
    .filter((w) => /[a-z0-9]/i.test(w));
  if (words.length === 0) return '?';
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

/** Card header: sentence-case heading + optional right-side action/hint, over a hairline. */
function CardTitle({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <Group
      justify="space-between"
      align="center"
      pb={8}
      mb="sm"
      style={{ borderBottom: `1px solid ${BRAND_LINE}` }}
    >
      <Text fw={600} c="navy.9">
        {children}
      </Text>
      {action}
    </Group>
  );
}

/** One row of the Details key-value grid. */
function KV({ label: k, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <Text size="sm" c={BRAND_FAINT}>
        {k}
      </Text>
      <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
        {children}
      </Text>
    </>
  );
}

/** A linked contact with an editable per-gig role and lead flag, plus an avatar. */
function LinkedContactRow({
  oppId,
  contact,
  roleOptions,
}: {
  oppId: number;
  contact: OpportunityContact;
  roleOptions: { value: string; label: string }[];
}) {
  const updateContact = useUpdateOpportunityContact();
  const removeContact = useRemoveOpportunityContact();
  return (
    <Group justify="space-between" wrap="nowrap" align="flex-start">
      <Group gap="sm" wrap="nowrap" style={{ minWidth: 0 }}>
        <Avatar color="terracotta" radius="xl" size="md">
          {initials(contact.name)}
        </Avatar>
        <div style={{ minWidth: 0 }}>
          <Anchor component={Link} to={`/contacts/${contact.contact_id}`} size="sm" fw={600}>
            {contact.name}
          </Anchor>
          {contact.is_primary && (
            <Badge color="good" variant="light" size="xs" ml={6}>
              Lead on this gig
            </Badge>
          )}
        </div>
      </Group>
      <Group gap="xs" wrap="nowrap">
        <Select
          placeholder="Role"
          size="xs"
          w={140}
          data={roleOptions}
          clearable
          value={contact.contact_role}
          onChange={(value) =>
            updateContact.mutate({
              oppId,
              contactId: contact.contact_id,
              data: { contact_role: value, is_primary: contact.is_primary },
            })
          }
        />
        <Switch
          label="Lead"
          size="xs"
          checked={contact.is_primary}
          onChange={(event) =>
            updateContact.mutate({
              oppId,
              contactId: contact.contact_id,
              data: { contact_role: contact.contact_role, is_primary: event.currentTarget.checked },
            })
          }
        />
        <ActionIcon
          variant="subtle"
          color="red"
          aria-label="Remove contact"
          onClick={() => removeContact.mutate({ oppId, contactId: contact.contact_id })}
        >
          <IconX size={16} />
        </ActionIcon>
      </Group>
    </Group>
  );
}

/** Editable payment control (mark invoiced/partial/paid + paid-on); recomputes closed_at server-side. */
function PaymentPanel({ opp }: { opp: Opportunity }) {
  const catalogs = useCatalogs();
  const patchPayment = usePatchPayment();
  const [paymentStatus, setPaymentStatus] = useState(opp.payment_status);
  const [paidOn, setPaidOn] = useState(opp.paid_on ?? '');

  // Re-seed when the server state changes (e.g. after a save re-reads the detail).
  useEffect(() => {
    setPaymentStatus(opp.payment_status);
    setPaidOn(opp.paid_on ?? '');
  }, [opp.payment_status, opp.paid_on]);

  const paymentOptions = (catalogs.data?.payment_statuses ?? []).map((p) => ({
    value: p.short_name,
    label: p.description,
  }));
  const dirty = paymentStatus !== opp.payment_status || (paidOn || null) !== (opp.paid_on ?? null);

  return (
    <Card withBorder radius="md">
      <CardTitle>Payment</CardTitle>
      <Group align="flex-end" gap="sm">
        <Select
          label="Status"
          data={paymentOptions}
          value={paymentStatus}
          onChange={(value) => value && setPaymentStatus(value)}
          w={170}
        />
        <TextInput
          label="Paid on"
          type="date"
          value={paidOn}
          onChange={(event) => setPaidOn(event.currentTarget.value)}
        />
        <Button
          disabled={!dirty}
          loading={patchPayment.isPending}
          onClick={() =>
            patchPayment.mutate({ id: opp.id, payment_status: paymentStatus, paid_on: paidOn || null })
          }
        >
          Save
        </Button>
      </Group>
    </Card>
  );
}

/** Right-column Venue card: fetches the org for its type + location subline. */
function VenueCard({ orgId, orgName }: { orgId: number; orgName: string }) {
  const catalogs = useCatalogs();
  const org = useOrganization(orgId);
  const sub = [
    org.data ? label(catalogs.data?.organization_types, org.data.organization_type) : null,
    org.data?.location,
  ]
    .filter(Boolean)
    .join(' · ');
  return (
    <Card withBorder radius="md">
      <CardTitle>Venue</CardTitle>
      <Group wrap="nowrap">
        <Avatar color="navy" radius="xl" size="md">
          {initials(orgName)}
        </Avatar>
        <div style={{ flex: 1, minWidth: 0 }}>
          <Text fw={600} size="sm" c="navy.9">
            {orgName}
          </Text>
          {sub && (
            <Text size="xs" c="dimmed">
              {sub}
            </Text>
          )}
        </div>
        <Anchor component={Link} to={`/venues/${orgId}`} size="sm">
          View
        </Anchor>
      </Group>
    </Card>
  );
}

export function OpportunityDetail() {
  const { id } = useParams();
  const oppId = Number(id);
  const opp = useOpportunity(oppId);
  const catalogs = useCatalogs();
  const contacts = useContacts();
  const update = useUpdateOpportunity(oppId);
  const remove = useDeleteOpportunity();
  const addContact = useAddOpportunityContact();
  const addNote = useAddOpportunityNote();
  const deleteNote = useDeleteOpportunityNote();
  const navigate = useNavigate();
  const [editOpen, editHandlers] = useDisclosure(false);
  const [closeOpen, closeHandlers] = useDisclosure(false);
  const [noteBody, setNoteBody] = useState('');

  if (opp.isPending) {
    return <Loader />;
  }
  if (opp.isError) {
    const notFound = opp.error instanceof ApiError && opp.error.status === 404;
    return <Alert color="red">{notFound ? 'Opportunity not found.' : opp.error.message}</Alert>;
  }

  const o = opp.data;
  const statusLabel = label(catalogs.data?.opportunity_statuses, o.current_status);
  const formatLabel = label(catalogs.data?.opportunity_formats, o.opportunity_format);
  const compLabel = label(catalogs.data?.comp_types, o.comp_type);
  const paymentLabel = label(catalogs.data?.payment_statuses, o.payment_status);
  const settled =
    (catalogs.data?.payment_statuses ?? []).find((p) => p.short_name === o.payment_status)
      ?.is_settled ?? false;
  const money = formatMoney(o.fee_amount, o.currency);
  const isPaid = o.comp_type === 'paid';
  const compChip = isPaid && money ? `${compLabel} · ${money}` : compLabel;

  const roleOptions = (catalogs.data?.contact_roles ?? []).map((r) => ({
    value: r.short_name,
    label: r.description,
  }));
  const linkedIds = new Set(o.contacts.map((c) => c.contact_id));
  const contactOptions = (contacts.data ?? [])
    .filter((c) => !linkedIds.has(c.id))
    .map((c) => ({ value: String(c.id), label: c.name }));

  const backTo = o.closed_at ? '/history' : '/pipeline';
  const backLabel = o.closed_at ? 'History' : 'Pipeline';
  const heading = o.title;
  const crumbTail = `${o.organization_name} — ${o.title}`;

  async function handleUpdate(values: OpportunityInput) {
    await update.mutateAsync(values);
  }

  async function handleDelete() {
    if (!window.confirm(`Delete "${o.title}"? This hides it but keeps its history.`)) {
      return;
    }
    await remove.mutateAsync(oppId);
    navigate('/pipeline');
  }

  function handleAddNote() {
    if (!noteBody.trim()) return;
    addNote.mutate({ oppId, data: { body: noteBody.trim() } }, { onSuccess: () => setNoteBody('') });
  }

  return (
    <Stack>
      <Text size="sm" c="dimmed">
        <Anchor component={Link} to={backTo} c="dimmed">
          {backLabel}
        </Anchor>{' '}
        ›{' '}
        <Text span fw={600} c="navy.9">
          {crumbTail}
        </Text>
      </Text>

      <Group justify="space-between" align="flex-start">
        <div>
          <Title order={2} c="navy.9">
            {heading}
          </Title>
          <Group gap="xs" mt={6} align="center">
            <Anchor component={Link} to={`/venues/${o.organization_id}`} c="dimmed" size="sm">
              {o.organization_name}
            </Anchor>
            <Badge color={stageColor(o.current_status)} variant="light">
              {statusLabel}
            </Badge>
            <Badge color={isPaid && money ? 'good' : 'gray'} variant="light">
              {compChip}
            </Badge>
            {isPaid && (
              <Badge color={paymentColor(o.payment_status, settled)} variant="light">
                {paymentLabel}
              </Badge>
            )}
            {o.event_date && (
              <Text size="sm" c="dimmed">
                {o.event_date}
              </Text>
            )}
          </Group>
        </div>
        <Group>
          <Button variant="default" leftSection={<IconPencil size={16} />} onClick={editHandlers.open}>
            Edit
          </Button>
          {!o.closed_at && (
            <Button variant="light" color="terracotta" onClick={closeHandlers.open}>
              Close…
            </Button>
          )}
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
                Details
              </CardTitle>
              <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '8px 16px' }}>
                <KV label="Talk">{o.talk_title ?? '—'}</KV>
                <KV label="Format">{formatLabel}</KV>
                <KV label="Event date">{o.event_date ?? '—'}</KV>
                <Text size="sm" c={BRAND_FAINT}>
                  Stage
                </Text>
                <div>
                  <Badge color={stageColor(o.current_status)} variant="light">
                    {statusLabel}
                  </Badge>
                </div>
                <KV label="Compensation">{compChip}</KV>
                <KV label="Angle">{o.angle?.trim() ? o.angle : '—'}</KV>
                {o.outcome?.trim() && <KV label="Outcome">{o.outcome}</KV>}
              </div>
            </Card>

            <PaymentPanel opp={o} />

            <Card withBorder radius="md">
              <CardTitle action={<Text size="xs" c="dimmed">dated log for this gig</Text>}>
                Notes
              </CardTitle>
              <Textarea
                placeholder="Add a dated note — call outcome, scheduling change, prep detail…"
                autosize
                minRows={2}
                value={noteBody}
                onChange={(event) => setNoteBody(event.currentTarget.value)}
              />
              <Group justify="flex-end" mt="sm">
                <Button size="sm" onClick={handleAddNote} loading={addNote.isPending}>
                  Add note
                </Button>
              </Group>
              <Stack gap="sm" mt="md">
                {o.notes.length === 0 && (
                  <Text c="dimmed" size="sm">
                    No notes yet.
                  </Text>
                )}
                {o.notes.map((note) => (
                  <Group key={note.id} justify="space-between" wrap="nowrap" align="flex-start">
                    <div>
                      <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
                        {note.body}
                      </Text>
                      <Text size="xs" c="dimmed">
                        {formatWhen(note.occurred_at)}
                      </Text>
                    </div>
                    <ActionIcon
                      variant="subtle"
                      color="red"
                      aria-label="Delete note"
                      onClick={() => deleteNote.mutate({ oppId, noteId: note.id })}
                    >
                      <IconX size={16} />
                    </ActionIcon>
                  </Group>
                ))}
              </Stack>
            </Card>

            <Card withBorder radius="md">
              <CardTitle action={<Text size="xs" c="dimmed">stage history</Text>}>Lifecycle</CardTitle>
              <Timeline active={o.status_events.length} bulletSize={14} lineWidth={2}>
                {o.status_events.map((event) => (
                  <Timeline.Item
                    key={event.id}
                    title={label(catalogs.data?.opportunity_statuses, event.status)}
                  >
                    {event.note && (
                      <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
                        {event.note}
                      </Text>
                    )}
                    <Text size="xs" c="dimmed">
                      {formatWhen(event.occurred_at)}
                    </Text>
                  </Timeline.Item>
                ))}
              </Timeline>
            </Card>
          </Stack>
        </Grid.Col>

        {/* RIGHT column */}
        <Grid.Col span={{ base: 12, md: 5 }}>
          <Stack>
            <VenueCard orgId={o.organization_id} orgName={o.organization_name} />

            <Card withBorder radius="md">
              <CardTitle>On this gig ({o.contacts.length})</CardTitle>
              <Stack gap="md">
                {o.contacts.length === 0 && (
                  <Text c="dimmed" size="sm">
                    No one linked yet.
                  </Text>
                )}
                {[...o.contacts]
                  .sort((a, b) => a.name.localeCompare(b.name))
                  .map((contact) => (
                    <LinkedContactRow
                      key={contact.contact_id}
                      oppId={oppId}
                      contact={contact}
                      roleOptions={roleOptions}
                    />
                  ))}
                <Select
                  placeholder="Link a contact…"
                  data={contactOptions}
                  searchable
                  value={null}
                  leftSection={<IconPlus size={14} />}
                  onChange={(value) =>
                    value && addContact.mutate({ oppId, data: { contact_id: Number(value) } })
                  }
                />
              </Stack>
            </Card>
          </Stack>
        </Grid.Col>
      </Grid>

      <OpportunityFormModal
        opened={editOpen}
        onClose={editHandlers.close}
        title="Edit opportunity"
        submitLabel="Save"
        initialValues={o}
        onSubmit={handleUpdate}
      />
      <CloseOpportunityModal opened={closeOpen} onClose={closeHandlers.close} opportunity={o} />
    </Stack>
  );
}