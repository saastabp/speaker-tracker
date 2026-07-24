import { Alert, Button, Group, Modal, Select, Stack, Textarea, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { useEffect, useState } from 'react';
import { useCatalogs } from '../api/catalogs';
import { ApiError } from '../api/client';
import type { MessageTemplate, MessageTemplateInput } from '../api/templates';

interface FormValues {
  name: string;
  kind: string;
  channel: string;
  subject: string;
  body: string;
}

function toFormValues(template?: MessageTemplate): FormValues {
  return {
    name: template?.name ?? '',
    kind: template?.kind ?? '',
    channel: template?.channel ?? '',
    subject: template?.subject ?? '',
    body: template?.body ?? '',
  };
}

function toInput(values: FormValues): MessageTemplateInput {
  return {
    name: values.name.trim(),
    kind: values.kind,
    channel: values.channel,
    subject: values.subject.trim() || null,
    body: values.body,
  };
}

interface TemplateFormModalProps {
  opened: boolean;
  onClose: () => void;
  title: string;
  submitLabel: string;
  /** Edit mode seeds the form from an existing template; omit for create. */
  initialValues?: MessageTemplate;
  onSubmit: (values: MessageTemplateInput) => Promise<unknown>;
}

export function TemplateFormModal({
  opened,
  onClose,
  title,
  submitLabel,
  initialValues,
  onSubmit,
}: TemplateFormModalProps) {
  const catalogs = useCatalogs();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const form = useForm<FormValues>({
    initialValues: toFormValues(initialValues),
    validate: {
      name: (value) => (value.trim() ? null : 'Name is required'),
      kind: (value) => (value ? null : 'Purpose is required'),
      channel: (value) => (value ? null : 'Channel is required'),
      body: (value) => (value.trim() ? null : 'Body is required'),
    },
  });

  // Mantine's useForm doesn't auto-sync initialValues; refresh on each open.
  useEffect(() => {
    if (opened) {
      form.setValues(toFormValues(initialValues));
      setError(null);
    }
  }, [opened]); // eslint-disable-line react-hooks/exhaustive-deps

  const kindOptions = (catalogs.data?.message_template_kinds ?? []).map((k) => ({
    value: k.short_name,
    label: k.description,
  }));
  const channelOptions = (catalogs.data?.outreach_channels ?? []).map((c) => ({
    value: c.short_name,
    label: c.description,
  }));

  async function handleSubmit(values: FormValues) {
    setError(null);
    setSubmitting(true);
    try {
      await onSubmit(toInput(values));
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title={title} size="lg">
      <form onSubmit={form.onSubmit(handleSubmit)}>
        <Stack>
          {error && (
            <Alert color="red" variant="light">
              {error}
            </Alert>
          )}
          <TextInput label="Name" withAsterisk {...form.getInputProps('name')} />
          <Group grow>
            <Select
              label="Purpose"
              placeholder="Select a purpose"
              data={kindOptions}
              withAsterisk
              {...form.getInputProps('kind')}
            />
            <Select
              label="Channel"
              placeholder="How it is sent"
              data={channelOptions}
              withAsterisk
              {...form.getInputProps('channel')}
            />
          </Group>
          <TextInput
            label="Subject"
            placeholder="Email subject — leave blank for DM templates"
            {...form.getInputProps('subject')}
          />
          <Textarea
            label="Body"
            description="Merge fields like [Name] fill from the contact when the template is used"
            withAsterisk
            autosize
            minRows={6}
            {...form.getInputProps('body')}
          />
          <Group justify="flex-end" mt="sm">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" loading={submitting}>
              {submitLabel}
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}