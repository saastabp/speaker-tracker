import {
  Alert,
  Anchor,
  Button,
  Group,
  Modal,
  Select,
  Stack,
  Text,
  Textarea,
  TextInput,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { useDebouncedValue } from '@mantine/hooks';
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useCatalogs } from '../api/catalogs';
import { ApiError } from '../api/client';
import { useContacts, type ContactInput } from '../api/contacts';

const EMPTY: ContactInput = {
  name: '',
  email: '',
  phone: '',
  warmth_tier: '',
  source: '',
  how_you_know: '',
  notes: '',
};

/** Coerce nulls to '' so every field is a controlled input. */
function normalize(values?: ContactInput): ContactInput {
  const base = values ?? EMPTY;
  return {
    name: base.name ?? '',
    email: base.email ?? '',
    phone: base.phone ?? '',
    warmth_tier: base.warmth_tier ?? '',
    source: base.source ?? '',
    how_you_know: base.how_you_know ?? '',
    notes: base.notes ?? '',
  };
}

/** Live "this person may already exist" hint — the add-contact dedupe (acceptance #2). */
function DuplicateHints({ name }: { name: string }) {
  const [debounced] = useDebouncedValue(name.trim(), 300);
  const search = useContacts(debounced, debounced.length >= 2);
  if (debounced.length < 2 || !search.data || search.data.length === 0) {
    return null;
  }
  return (
    <Alert color="yellow" variant="light" title="Possible existing contacts">
      <Stack gap={4}>
        {search.data.map((contact) => (
          <Anchor key={contact.id} component={Link} to={`/contacts/${contact.id}`} size="sm">
            {contact.name}
            {contact.email ? ` · ${contact.email}` : ''} · {contact.organization_count} venue(s)
          </Anchor>
        ))}
        <Text size="xs" c="dimmed">
          If it's one of these, open it and add the venue there instead of creating a duplicate.
        </Text>
      </Stack>
    </Alert>
  );
}

interface ContactFormModalProps {
  opened: boolean;
  onClose: () => void;
  title: string;
  submitLabel: string;
  initialValues?: ContactInput;
  /** Show the live duplicate-search hint (used when adding, not editing). */
  dedupe?: boolean;
  onSubmit: (values: ContactInput) => Promise<unknown>;
}

export function ContactFormModal({
  opened,
  onClose,
  title,
  submitLabel,
  initialValues,
  dedupe,
  onSubmit,
}: ContactFormModalProps) {
  const catalogs = useCatalogs();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const form = useForm<ContactInput>({
    initialValues: normalize(initialValues),
    validate: { name: (value) => (value.trim() ? null : 'Name is required') },
  });

  useEffect(() => {
    if (opened) {
      form.setValues(normalize(initialValues));
      setError(null);
    }
  }, [opened]); // eslint-disable-line react-hooks/exhaustive-deps

  const warmthOptions = (catalogs.data?.warmth_tiers ?? []).map((tier) => ({
    value: tier.short_name,
    label: tier.description,
  }));

  async function handleSubmit(values: ContactInput) {
    setError(null);
    setSubmitting(true);
    try {
      // warmth_tier is a catalog short_name — send null (not '') when unset.
      await onSubmit({ ...values, warmth_tier: values.warmth_tier || null });
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
          {dedupe && <DuplicateHints name={form.values.name} />}
          <Group grow>
            <TextInput label="Email" {...form.getInputProps('email')} />
            <TextInput label="Phone" {...form.getInputProps('phone')} />
          </Group>
          <Select
            label="Warmth"
            placeholder="Not set"
            data={warmthOptions}
            clearable
            {...form.getInputProps('warmth_tier')}
          />
          <TextInput
            label="Source"
            description="How you met, or where they came from"
            {...form.getInputProps('source')}
          />
          <Textarea
            label="How you know them"
            autosize
            minRows={2}
            {...form.getInputProps('how_you_know')}
          />
          <Textarea label="Notes" autosize minRows={2} {...form.getInputProps('notes')} />
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