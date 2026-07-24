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
import { useCatalogs } from '../api/catalogs';
import { ApiError } from '../api/client';
import { useContacts } from '../api/contacts';
import { useContactOutreaches, useCreateOutreach } from '../api/outreaches';
import { useOpportunities } from '../api/opportunities';
import { FieldLabel } from './FieldLabel';
import { TemplatePicker } from './TemplatePicker';

// Email is owned by the composer (it auto-logs an outreach on send), so it is not a manual
// Log-Outreach channel. The catalog still defines it; we just don't offer it here.
const EMAIL_CHANNEL = 'email';

interface LogOutreachModalProps {
  opened: boolean;
  onClose: () => void;
  /** When set, the contact is preselected and its selector is locked (opened from a contact).
   *  Omit to let the user pick the contact (opened from the pipeline, venues, etc.). */
  contactId?: number;
  /** Display-name fallback for the locked contact, so merge fields resolve before the contact
   *  list finishes loading. */
  contactName?: string;
}

export function LogOutreachModal({
  opened,
  onClose,
  contactId,
  contactName,
}: LogOutreachModalProps) {
  const catalogs = useCatalogs();
  const contacts = useContacts();
  const opportunities = useOpportunities(false);
  const create = useCreateOutreach();

  const locked = contactId != null;
  const [selectedId, setSelectedId] = useState<number | null>(contactId ?? null);

  // The chosen contact's prior touches drive the inferred kind default (contact 0 → empty list).
  const priorOutreaches = useContactOutreaches(selectedId ?? 0);
  const hasPriorOutreach = (priorOutreaches.data?.length ?? 0) > 0;
  const inferredKind = hasPriorOutreach ? 'correspondence' : 'initial';
  const resolvedName =
    contacts.data?.find((c) => c.id === selectedId)?.name ?? contactName ?? '';

  const [channel, setChannel] = useState('dm');
  const [kind, setKind] = useState(inferredKind);
  const [kindTouched, setKindTouched] = useState(false);
  const [opportunityId, setOpportunityId] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const [occurredOn, setOccurredOn] = useState('');
  const [templateId, setTemplateId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Reset the form each time the modal opens (preselecting the locked contact, if any).
  useEffect(() => {
    if (opened) {
      setSelectedId(contactId ?? null);
      setChannel('dm');
      setKindTouched(false);
      setOpportunityId(null);
      setNote('');
      setOccurredOn('');
      setTemplateId(null);
      setError(null);
    }
  }, [opened, contactId]);

  // Track the inferred default until the user overrides the chip (then leave their choice alone).
  useEffect(() => {
    if (opened && !kindTouched) {
      setKind(inferredKind);
    }
  }, [opened, inferredKind, kindTouched]);

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
    setError(null);
    setSubmitting(true);
    try {
      await create.mutateAsync({
        contact_id: selectedId,
        channel,
        // Omit kind unless the user overrode the chip, so the server stays the source of inference.
        kind: kindTouched ? kind : undefined,
        opportunity_id: opportunityId ? Number(opportunityId) : null,
        message_template_id: templateId,
        note: note.trim() || null,
        occurred_at: occurredOn || null,
      });
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong.');
    } finally {
      setSubmitting(false);
    }
  }

  const title = locked && resolvedName ? `Log outreach to ${resolvedName}` : 'Log outreach';

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
            <SegmentedControl
              data={channelOptions}
              value={channel}
              onChange={(value) => setChannel(value)}
            />
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
            <Text size="xs" c="dimmed" mt={4}>
              Auto-detected from prior touches — change it if this is a fresh pitch.
            </Text>
          </div>

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

          <Group grow align="flex-start">
            <div>
              <FieldLabel>Date</FieldLabel>
              <TextInput
                type="date"
                value={occurredOn}
                onChange={(event) => setOccurredOn(event.currentTarget.value)}
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

          <Group justify="space-between" mt="sm">
            <Group>
              <Button onClick={handleSubmit} loading={submitting}>
                Log touch
              </Button>
              <Button variant="default" onClick={onClose}>
                Cancel
              </Button>
            </Group>
            <Text size="xs" c="dimmed">
              Counts toward this week&apos;s outreach target
            </Text>
          </Group>
        </Stack>
      )}
    </Modal>
  );
}