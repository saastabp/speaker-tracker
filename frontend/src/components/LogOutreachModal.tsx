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
import { useCreateOutreach } from '../api/outreaches';
import { useOpportunities } from '../api/opportunities';
import { TemplatePicker } from './TemplatePicker';

// Email is owned by the composer (it auto-logs an outreach on send), so it is not a manual
// Log-Outreach channel. The catalog still defines it; we just don't offer it here.
const EMAIL_CHANNEL = 'email';

interface LogOutreachModalProps {
  opened: boolean;
  onClose: () => void;
  contactId: number;
  contactName: string;
  /** Whether this contact already has an outbound touch — drives the inferred kind default. */
  hasPriorOutreach: boolean;
}

export function LogOutreachModal({
  opened,
  onClose,
  contactId,
  contactName,
  hasPriorOutreach,
}: LogOutreachModalProps) {
  const catalogs = useCatalogs();
  const opportunities = useOpportunities(false);
  const create = useCreateOutreach();

  // Client-side default preview; the server re-infers authoritatively when kind is omitted.
  const inferredKind = hasPriorOutreach ? 'correspondence' : 'initial';

  const [channel, setChannel] = useState('dm');
  const [kind, setKind] = useState(inferredKind);
  const [kindTouched, setKindTouched] = useState(false);
  const [opportunityId, setOpportunityId] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const [occurredOn, setOccurredOn] = useState('');
  const [templateId, setTemplateId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Reset the form each time the modal opens.
  useEffect(() => {
    if (opened) {
      setChannel('dm');
      setKindTouched(false);
      setOpportunityId(null);
      setNote('');
      setOccurredOn('');
      setTemplateId(null);
      setError(null);
    }
  }, [opened]);

  // Track the inferred default until the user overrides the chip (then leave their choice alone).
  useEffect(() => {
    if (opened && !kindTouched) {
      setKind(inferredKind);
    }
  }, [opened, inferredKind, kindTouched]);

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
    if (!channel) {
      setError('Pick a channel.');
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await create.mutateAsync({
        contact_id: contactId,
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

  return (
    <Modal opened={opened} onClose={onClose} title={`Log outreach to ${contactName}`} size="lg">
      {catalogs.isPending ? (
        <Loader />
      ) : (
        <Stack>
          {error && (
            <Alert color="red" variant="light">
              {error}
            </Alert>
          )}

          <Group grow align="flex-start">
            <Select
              label="Channel"
              data={channelOptions}
              value={channel}
              onChange={(value) => setChannel(value ?? '')}
              withAsterisk
            />
            <TextInput
              label="When"
              type="date"
              description="Defaults to now"
              value={occurredOn}
              onChange={(event) => setOccurredOn(event.currentTarget.value)}
            />
          </Group>

          <div>
            <Text size="sm" fw={500} mb={4}>
              Kind
            </Text>
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

          <Select
            label="About a gig"
            placeholder="Optional — link this touch to an opportunity"
            data={oppOptions}
            value={opportunityId}
            onChange={setOpportunityId}
            clearable
            searchable
          />

          <TemplatePicker
            contactName={contactName}
            allowedChannels={manualChannels}
            onTemplateSelected={(template) => {
              setTemplateId(template?.id ?? null);
              if (template) {
                setChannel(template.channel);
              }
            }}
          />

          <Textarea
            label="Note"
            placeholder="Optional — what you said or how it went"
            autosize
            minRows={2}
            value={note}
            onChange={(event) => setNote(event.currentTarget.value)}
          />

          <Group justify="flex-end" mt="sm">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button onClick={handleSubmit} loading={submitting}>
              Log outreach
            </Button>
          </Group>
        </Stack>
      )}
    </Modal>
  );
}