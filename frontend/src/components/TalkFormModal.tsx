import { Alert, Button, Group, Modal, Stack, Textarea, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { useEffect, useState } from 'react';
import { ApiError } from '../api/client';
import type { TalkInput } from '../api/talks';
import { FieldLabel } from './FieldLabel';

interface FormValues {
  title: string;
  duration: string;
  one_liner: string;
}

/** Coerce nulls to '' so every field is a controlled string input. */
function toFormValues(values?: TalkInput): FormValues {
  return {
    title: values?.title ?? '',
    duration: values?.duration ?? '',
    one_liner: values?.one_liner ?? '',
  };
}

interface TalkFormModalProps {
  opened: boolean;
  onClose: () => void;
  title: string;
  submitLabel: string;
  initialValues?: TalkInput;
  onSubmit: (values: TalkInput) => Promise<unknown>;
}

/** Create or edit a talk — the reusable offers an opportunity can point at. */
export function TalkFormModal({
  opened,
  onClose,
  title,
  submitLabel,
  initialValues,
  onSubmit,
}: TalkFormModalProps) {
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const form = useForm<FormValues>({
    initialValues: toFormValues(initialValues),
    validate: { title: (value) => (value.trim() ? null : 'A title is required') },
  });

  // Mantine's useForm does not auto-sync initialValues; refresh on each open.
  useEffect(() => {
    if (opened) {
      form.setValues(toFormValues(initialValues));
      setError(null);
    }
  }, [opened]); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleSubmit(values: FormValues) {
    setError(null);
    setSubmitting(true);
    try {
      await onSubmit({
        title: values.title.trim(),
        duration: values.duration.trim() || null,
        one_liner: values.one_liner.trim() || null,
      });
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title={title}>
      <form onSubmit={form.onSubmit(handleSubmit)}>
        <Stack>
          {error && (
            <Alert color="red" variant="light">
              {error}
            </Alert>
          )}

          <div>
            <FieldLabel>Title</FieldLabel>
            <TextInput placeholder="e.g. “I'm Fine Is a Lie”" {...form.getInputProps('title')} />
          </div>

          <div>
            {/* Free text rather than a number of minutes: the real answers are "45–60 min" and
                "flexible length", and nothing in the app computes on a duration. */}
            <FieldLabel helper="optional">Duration</FieldLabel>
            <TextInput
              placeholder="e.g. 45–60 min, or flexible length"
              {...form.getInputProps('duration')}
            />
          </div>

          <div>
            <FieldLabel helper="optional">What it is</FieldLabel>
            <Textarea
              placeholder="A sentence you would actually say to a venue"
              autosize
              minRows={3}
              {...form.getInputProps('one_liner')}
            />
          </div>

          <Group mt="sm">
            <Button type="submit" loading={submitting}>
              {submitLabel}
            </Button>
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}