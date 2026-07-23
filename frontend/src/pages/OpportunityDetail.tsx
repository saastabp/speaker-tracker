import {
  ActionIcon,
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
  Textarea,
  TextInput,
  Timeline,
  Title,
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconArrowLeft, IconPencil, IconPlus, IconTrash, IconX } from '@tabler/icons-react';
import { useEffect, useState } from 'react';
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
import { CloseOpportunityModal } from '../components/CloseOpportunityModal';
import { OpportunityFormModal } from '../components/OpportunityFormModal';

function formatMoney(fee: string | null | undefined, currency: string | undefined): string | null {
  if (!fee) return null;
  const amount = Number(fee);
  const cur = currency || 'USD';
  if (Number.isNaN(amount)) return `${cur} ${fee}`;
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency: cur }).format(amount);
  } catch {
    return `${cur} ${amount.toFixed(2)}`;
  }
}

function formatWhen(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

/** One labelled inline field. */
function Field({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div>
      <Text size="xs" tt="uppercase" fw={600} c="dimmed">
        {label}
      </Text>
      <Text style={{ whiteSpace: 'pre-wrap' }}>{value?.trim() ? value : '—'}</Text>
    </div>
  );
}

/** A linked contact with an editable per-gig role and lead flag. */
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
    <Group justify="space-between" wrap="nowrap">
      <Anchor component={Link} to={`/contacts/${contact.contact_id}`} size="sm">
        {contact.name}
      </Anchor>
      <Group gap="sm" wrap="nowrap">
        <Select
          placeholder="Role"
          size="xs"
          w={150}
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

/** Money summary plus the payment-status control (mark paid / correct back). */
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
  const money = formatMoney(opp.fee_amount, opp.currency);
  const dirty = paymentStatus !== opp.payment_status || (paidOn || null) !== (opp.paid_on ?? null);

  return (
    <Card withBorder radius="md">
      <Text fw={600} mb="sm">
        Money &amp; payment
      </Text>
      <Group gap="xl" mb="sm">
        <Field label="Compensation" value={opp.comp_type} />
        <Field label="Fee" value={money ?? '—'} />
      </Group>
      <Group align="flex-end" gap="sm">
        <Select
          label="Payment status"
          data={paymentOptions}
          value={paymentStatus}
          onChange={(value) => value && setPaymentStatus(value)}
          w={180}
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
            patchPayment.mutate({
              id: opp.id,
              payment_status: paymentStatus,
              paid_on: paidOn || null,
            })
          }
        >
          Save
        </Button>
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
  const label = (list: { short_name: string; description: string }[] | undefined, sn: string) =>
    list?.find((c) => c.short_name === sn)?.description ?? sn;
  const statusLabel = label(catalogs.data?.opportunity_statuses, o.current_status);
  const roleOptions = (catalogs.data?.contact_roles ?? []).map((r) => ({
    value: r.short_name,
    label: r.description,
  }));
  const linkedIds = new Set(o.contacts.map((c) => c.contact_id));
  const contactOptions = (contacts.data ?? [])
    .filter((c) => !linkedIds.has(c.id))
    .map((c) => ({ value: String(c.id), label: c.name }));

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
    addNote.mutate(
      { oppId, data: { body: noteBody.trim() } },
      { onSuccess: () => setNoteBody('') },
    );
  }

  return (
    <Stack>
      <Anchor component={Link} to={o.closed_at ? '/history' : '/pipeline'} size="sm">
        <Group gap={4}>
          <IconArrowLeft size={14} />
          {o.closed_at ? 'History' : 'Pipeline'}
        </Group>
      </Anchor>

      <Group justify="space-between" align="flex-start">
        <div>
          <Group gap="sm">
            <Title order={2} c="navy.9">
              {o.title}
            </Title>
            <Badge color={o.closed_at ? 'gray' : 'navy'} variant={o.closed_at ? 'light' : 'filled'}>
              {statusLabel}
            </Badge>
          </Group>
          <Anchor component={Link} to={`/venues/${o.organization_id}`} c="dimmed" size="sm">
            {o.organization_name}
          </Anchor>
        </div>
        <Group>
          <Button variant="default" leftSection={<IconPencil size={16} />} onClick={editHandlers.open}>
            Edit
          </Button>
          {!o.closed_at && (
            <Button variant="light" color="terracotta" onClick={closeHandlers.open}>
              Close
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

      <Card withBorder radius="md">
        <Group gap="xl">
          <Field label="Format" value={label(catalogs.data?.opportunity_formats, o.opportunity_format)} />
          <Field label="Talk" value={o.talk_title} />
          <Field label="Event date" value={o.event_date} />
        </Group>
        <Stack gap="md" mt="md">
          <Field label="Angle" value={o.angle} />
          <Field label="Outcome" value={o.outcome} />
        </Stack>
      </Card>

      <PaymentPanel opp={o} />

      <Card withBorder radius="md">
        <Text fw={600} mb="sm">
          People on this gig ({o.contacts.length})
        </Text>
        <Stack gap="sm">
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
            placeholder="Add a contact…"
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

      <Card withBorder radius="md">
        <Text fw={600} mb="sm">
          Notes
        </Text>
        <Stack gap="sm">
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
          <Group align="flex-end" gap="sm">
            <Textarea
              placeholder="Add a note…"
              autosize
              minRows={1}
              style={{ flex: 1 }}
              value={noteBody}
              onChange={(event) => setNoteBody(event.currentTarget.value)}
            />
            <Button onClick={handleAddNote} loading={addNote.isPending}>
              Add
            </Button>
          </Group>
        </Stack>
      </Card>

      <Card withBorder radius="md">
        <Text fw={600} mb="sm">
          Lifecycle
        </Text>
        <Timeline active={o.status_events.length} bulletSize={14} lineWidth={2}>
          {o.status_events.map((event) => (
            <Timeline.Item key={event.id} title={label(catalogs.data?.opportunity_statuses, event.status)}>
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

      <OpportunityFormModal
        opened={editOpen}
        onClose={editHandlers.close}
        title="Edit opportunity"
        submitLabel="Save"
        initialValues={o}
        onSubmit={handleUpdate}
      />
      <CloseOpportunityModal
        opened={closeOpen}
        onClose={closeHandlers.close}
        opportunity={o}
      />
    </Stack>
  );
}