import {
  Alert,
  Button,
  Group,
  Loader,
  Modal,
  SegmentedControl,
  Select,
  Stack,
  Text,
  Textarea,
  TextInput,
} from '@mantine/core';
import { useEffect, useState } from 'react';
import { catalogLabel, useCatalogs } from '../api/catalogs';
import { ApiError } from '../api/client';
import { useContacts } from '../api/contacts';
import {
  useContactOutreaches,
  useCreateOutreach,
  useDeleteOutreach,
  usePatchOutreach,
  type Outreach,
} from '../api/outreaches';
import { useOpportunities } from '../api/opportunities';
import { FieldLabel } from './FieldLabel';
import { FollowUpRiderFields, type FollowUpRiderValue } from './FollowUpRiderFields';
import { TemplatePicker } from './TemplatePicker';

// Email is owned by the composer (it auto-logs an outreach on send), so it is not a manual
// Log-Outreach channel. The catalog still defines it; we just don't offer it here.
const EMAIL_CHANNEL = 'email';

interface OutreachFormModalProps {
  opened: boolean;
  onClose: () => void;
  /** When set, the contact is preselected and its selector is locked (opened from a contact).
   *  Omit to let the user pick the contact (opened from the pipeline, venues, etc.). */
  contactId?: number;
  /** Display-name fallback for the locked contact, so merge fields resolve before the contact
   *  list finishes loading. */
  contactName?: string;
  /** Pass an existing touch to edit it; omit to log a new one. */
  outreach?: Outreach | null;
}

/**
 * Log a new outbound touch, or correct one already logged.
 *
 * One modal for both, like every other form here — but editing **hides** two of the controls rather
 * than disabling them, because neither has a meaning after the fact. The *template picker* records
 * what was used to compose at the moment of sending; the *follow-up rider* creates a separate
 * reminder alongside a **new** touch, and a correction has nothing to schedule.
 *
 * Two things are locked in edit mode. The **contact** is what the row is — moving a touch between
 * two people's timelines would re-open the kind inference that ran when it was logged, so the
 * server does not accept it either. And the **channel of an email touch**: those rows are written
 * by the composer against a real sent message, so relabelling one "Call" would make the journal
 * lie. Its note, date, kind and gig stay editable.
 */
export function OutreachFormModal({
  opened,
  onClose,
  contactId,
  contactName,
  outreach,
}: OutreachFormModalProps) {
  const catalogs = useCatalogs();
  const contacts = useContacts();
  const opportunities = useOpportunities({ closed: false });
  const create = useCreateOutreach();
  const patch = usePatchOutreach();
  const remove = useDeleteOutreach();

  const editing = outreach != null;
  const locked = editing || contactId != null;
  // A touch the composer logged. Its channel is a fact about a message that was actually sent.
  const emailTouch = editing && outreach.channel === EMAIL_CHANNEL;
  const [selectedId, setSelectedId] = useState<number | null>(contactId ?? null);

  // The chosen contact's prior touches drive the inferred kind default (contact 0 → empty list).
  const priorOutreaches = useContactOutreaches(selectedId ?? 0);
  const hasPriorOutreach = (priorOutreaches.data?.length ?? 0) > 0;
  const inferredKind = hasPriorOutreach ? 'correspondence' : 'initial';
  const resolvedName =
    contacts.data?.find((c) => c.id === selectedId)?.name ?? outreach?.contact_name ?? contactName ?? '';

  const [channel, setChannel] = useState('dm');
  const [kind, setKind] = useState(inferredKind);
  const [kindTouched, setKindTouched] = useState(false);
  const [opportunityId, setOpportunityId] = useState<string | null>(null);
  const [note, setNote] = useState('');
  // One field for "when", but two input types. Creating takes a bare date (blank means now,
  // server-side); editing takes date *and* time, because saving an unrelated field must not
  // quietly reset a 3:30pm touch to midnight.
  const [occurredAt, setOccurredAt] = useState('');
  const [templateId, setTemplateId] = useState<number | null>(null);
  const [riderOn, setRiderOn] = useState(false);
  const [rider, setRider] = useState<FollowUpRiderValue>({ due_date: '', note: '' });
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Reset each time the modal opens, from the touch being edited or from the page's context.
  useEffect(() => {
    if (opened) {
      setSelectedId(outreach?.contact_id ?? contactId ?? null);
      setChannel(outreach?.channel ?? 'dm');
      setKindTouched(false);
      setOpportunityId(outreach?.opportunity_id != null ? String(outreach.opportunity_id) : null);
      setNote(outreach?.note ?? '');
      // `slice(0, 16)` drops the seconds the API sends; datetime-local rejects them.
      setOccurredAt(outreach ? outreach.occurred_at.slice(0, 16) : '');
      setTemplateId(null);
      // Off on every open — the rider is opt-in, and a previous use must not carry over.
      setRiderOn(false);
      setRider({ due_date: '', note: '' });
      setError(null);
    }
  }, [opened, contactId, outreach]);

  // Track the inferred default until the user overrides the chip (then leave their choice alone).
  // Inference is a create-time default only: an existing touch already has a resolved kind, and
  // re-deriving it from a history that has since grown would change the row's target counting.
  useEffect(() => {
    if (opened && !editing && !kindTouched) {
      setKind(inferredKind);
    }
  }, [opened, editing, inferredKind, kindTouched]);

  // In edit mode the chip starts from what was stored, not from what would be inferred now.
  useEffect(() => {
    if (opened && outreach) {
      setKind(outreach.kind);
    }
  }, [opened, outreach]);

  const contactOptions = (contacts.data ?? []).map((c) => ({
    value: String(c.id),
    label: c.name,
  }));
  const channelOptions = (catalogs.data?.outreach_channels ?? [])
    .filter((c) => c.short_name !== EMAIL_CHANNEL)
    .map((c) => ({ value: c.short_name, label: c.description }));
  const manualChannels = channelOptions.map((o) => o.value);
  const kindOptions = (catalogs.data?.outreach_kinds ?? []).map((k) => ({
    value: k.short_name,
    label: k.description,
  }));
  const oppOptions = (opportunities.data ?? []).map((o) => ({
    value: String(o.id),
    label: o.title,
  }));

  async function handleSubmit() {
    if (!selectedId) {
      setError('Pick a contact.');
      return;
    }
    if (!channel) {
      setError('Pick a channel.');
      return;
    }
    if (riderOn && !rider.due_date) {
      setError('Pick a date for the follow-up, or switch it off.');
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      if (editing) {
        // Every editable field goes with the patch, so what the form shows *is* what is stored: a
        // cleared gig or note arrives as an explicit null, which is what clears it server-side.
        await patch.mutateAsync({
          id: outreach.id,
          contactId: outreach.contact_id,
          channel,
          kind,
          opportunity_id: opportunityId ? Number(opportunityId) : null,
          note: note.trim() || null,
          occurred_at: occurredAt || undefined,
        });
      } else {
        await create.mutateAsync({
          contact_id: selectedId,
          channel,
          // Omit kind unless the user overrode the chip, so the server stays the source of
          // inference.
          kind: kindTouched ? kind : undefined,
          opportunity_id: opportunityId ? Number(opportunityId) : null,
          message_template_id: templateId,
          note: note.trim() || null,
          occurred_at: occurredAt || null,
          follow_up: riderOn ? { due_date: rider.due_date, note: rider.note.trim() || null } : null,
        });
      }
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong.');
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete() {
    if (!outreach) return;
    if (
      !window.confirm(
        'Delete this logged touch? It disappears from the timeline and from this week’s count.',
      )
    ) {
      return;
    }
    setSubmitting(true);
    try {
      await remove.mutateAsync({ id: outreach.id, contactId: outreach.contact_id });
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong.');
    } finally {
      setSubmitting(false);
    }
  }

  const title = editing
    ? `Edit outreach${resolvedName ? ` to ${resolvedName}` : ''}`
    : locked && resolvedName
      ? `Log outreach to ${resolvedName}`
      : 'Log outreach';

  return (
    <Modal opened={opened} onClose={onClose} title={title} size="lg">
      {catalogs.isPending ? (
        <Loader />
      ) : (
        <Stack>
          {error && (
            <Alert color="red" variant="light">
              {error}
            </Alert>
          )}

          <div>
            <FieldLabel>Contact</FieldLabel>
            <Select
              placeholder="Who did you reach out to?"
              data={contactOptions}
              value={selectedId != null ? String(selectedId) : null}
              onChange={(value) => setSelectedId(value ? Number(value) : null)}
              disabled={locked}
              searchable
            />
          </div>

          <div>
            <FieldLabel>Channel</FieldLabel>
            {emailTouch ? (
              <Text size="sm">
                {catalogLabel(catalogs.data?.outreach_channels, EMAIL_CHANNEL)}{' '}
                <Text span size="xs" c="dimmed">
                  — logged by the composer when this was sent, so it stays as it is
                </Text>
              </Text>
            ) : (
              <SegmentedControl
                data={channelOptions}
                value={channel}
                onChange={(value) => setChannel(value)}
              />
            )}
          </div>

          <div>
            <FieldLabel>Kind</FieldLabel>
            <SegmentedControl
              data={kindOptions}
              value={kind}
              onChange={(value) => {
                setKind(value);
                setKindTouched(true);
              }}
            />
            {!editing && (
              <Text size="xs" c="dimmed" mt={4}>
                Auto-detected from prior touches — change it if this is a fresh pitch.
              </Text>
            )}
          </div>

          {!editing && (
            <TemplatePicker
              contactName={resolvedName}
              allowedChannels={manualChannels}
              onTemplateSelected={(template) => {
                setTemplateId(template?.id ?? null);
                if (template) {
                  setChannel(template.channel);
                }
              }}
            />
          )}

          <Group grow align="flex-start">
            <div>
              <FieldLabel>Date</FieldLabel>
              <TextInput
                type={editing ? 'datetime-local' : 'date'}
                value={occurredAt}
                onChange={(event) => setOccurredAt(event.currentTarget.value)}
              />
            </div>
            <div>
              <FieldLabel helper="optional">Opportunity</FieldLabel>
              <Select
                placeholder="Link this touch to a gig"
                data={oppOptions}
                value={opportunityId}
                onChange={setOpportunityId}
                clearable
                searchable
              />
            </div>
          </Group>

          <div>
            <FieldLabel>Note</FieldLabel>
            <Textarea
              placeholder="Optional — what you said or how it went"
              autosize
              minRows={2}
              value={note}
              onChange={(event) => setNote(event.currentTarget.value)}
            />
          </div>

          {!editing && (
            <FollowUpRiderFields
              enabled={riderOn}
              onEnabledChange={setRiderOn}
              value={rider}
              onChange={setRider}
              description="Pick a date to be reminded to chase this touch."
            />
          )}

          <Group justify="space-between" mt="sm">
            <Group>
              <Button onClick={handleSubmit} loading={submitting}>
                {editing ? 'Save changes' : 'Log touch'}
              </Button>
              <Button variant="default" onClick={onClose}>
                Cancel
              </Button>
            </Group>
            {editing ? (
              <Button variant="light" color="red" onClick={handleDelete} disabled={submitting}>
                Delete
              </Button>
            ) : (
              <Text size="xs" c="dimmed">
                Counts toward this week&apos;s outreach target
              </Text>
            )}
          </Group>
        </Stack>
      )}
    </Modal>
  );
}