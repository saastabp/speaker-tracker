import { Alert, Button, Group, Modal, Select, Stack, Text, Textarea, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { useEffect, useState } from 'react';
import { useCatalogs } from '../api/catalogs';
import { ApiError } from '../api/client';
import type { MessageTemplate, MessageTemplateInput } from '../api/templates';
import { BRAND_PANEL } from '../theme';
import { FieldLabel } from './FieldLabel';

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
  /** When editing a shared template, forks it into a personal copy (then the modal closes). */
  onDuplicate?: () => Promise<unknown>;
}

export function TemplateFormModal({
  opened,
  onClose,
  title,
  submitLabel,
  initialValues,
  onSubmit,
  onDuplicate,
}: TemplateFormModalProps) {
  const catalogs = useCatalogs();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [duplicating, setDuplicating] = useState(false);
  const isShared = initialValues?.is_shared ?? false;

  const form = useForm<FormValues>({
    initialValues: toFormValues(initialValues),
    validate: {
      name: (value) => (value.trim() ? null : 'Name is required'),
      kind: (value) => (value ? null : 'Purpose is required'),
      channel: (value) => (value ? null : 'Channel is required'),
      body: (value) => (value.trim() ? null : 'Body is required'),
    },
  });

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

  async function handleDuplicate() {
    if (!onDuplicate) return;
    setError(null);
    setDuplicating(true);
    try {
      await onDuplicate();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong.');
    } finally {
      setDuplicating(false);
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

          <div>
            <FieldLabel>Name</FieldLabel>
            <TextInput {...form.getInputProps('name')} />
          </div>

          <Group grow align="flex-start">
            <div>
              <FieldLabel>Purpose</FieldLabel>
              <Select
                placeholder="Select a purpose"
                data={kindOptions}
                {...form.getInputProps('kind')}
              />
            </div>
            <div>
              <FieldLabel>Channel</FieldLabel>
              <Select
                placeholder="How it is sent"
                data={channelOptions}
                {...form.getInputProps('channel')}
              />
            </div>
          </Group>

          <div>
            <FieldLabel helper="email only">Subject</FieldLabel>
            <TextInput
              placeholder="Email subject — leave blank for DM templates"
              {...form.getInputProps('subject')}
            />
          </div>

          <div>
            <FieldLabel helper="use [Name] and other merge fields">Body</FieldLabel>
            <Textarea autosize minRows={6} {...form.getInputProps('body')} />
          </div>

          <Text size="xs" c="dimmed" p="sm" style={{ background: BRAND_PANEL, borderRadius: 8 }}>
            Merge field available now:{' '}
            <Text span fw={600}>
              [Name]
            </Text>{' '}
            (the contact's first name), filled when you use the template.
            {isShared &&
              " You're editing the shared template — the change applies everywhere. Prefer Duplicate to keep a personal variant."}
          </Text>

          <Group justify="space-between" mt="sm">
            <Group>
              <Button type="submit" loading={submitting}>
                {submitLabel}
              </Button>
              {isShared && onDuplicate && (
                <Button variant="default" onClick={handleDuplicate} loading={duplicating}>
                  Duplicate as my copy
                </Button>
              )}
              <Button variant="default" onClick={onClose}>
                Cancel
              </Button>
            </Group>
            {isShared && (
              <Text size="xs" c="dimmed">
                Shared template · editable in place
              </Text>
            )}
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}