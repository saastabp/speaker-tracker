import {
  Alert,
  Anchor,
  Button,
  Group,
  Modal,
  SegmentedControl,
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
import { FieldLabel } from './FieldLabel';

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

  // Refresh on open; default Warmth to the first tier (the segmented control has no empty state).
  useEffect(() => {
    if (opened) {
      const values = normalize(initialValues);
      if (!values.warmth_tier) {
        const first = catalogs.data?.warmth_tiers?.[0]?.short_name;
        if (first) values.warmth_tier = first;
      }
      form.setValues(values);
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

          <div>
            <FieldLabel>Name</FieldLabel>
            <TextInput {...form.getInputProps('name')} />
          </div>
          {dedupe && <DuplicateHints name={form.values.name} />}

          <Group grow align="flex-start">
            <div>
              <FieldLabel>Email</FieldLabel>
              <TextInput {...form.getInputProps('email')} />
            </div>
            <div>
              <FieldLabel helper="optional">Phone</FieldLabel>
              <TextInput placeholder="(808) …" {...form.getInputProps('phone')} />
            </div>
          </Group>

          <div>
            <FieldLabel>Warmth</FieldLabel>
            <SegmentedControl
              data={warmthOptions}
              value={form.values.warmth_tier ?? ''}
              onChange={(value) => form.setFieldValue('warmth_tier', value)}
            />
          </div>

          <div>
            <FieldLabel helper="optional">Warm intro / mutual connection</FieldLabel>
            <TextInput
              placeholder="e.g. Jay Nakamura (BNI) offered an introduction"
              {...form.getInputProps('how_you_know')}
            />
          </div>

          <div>
            <FieldLabel helper="optional">Source</FieldLabel>
            <TextInput
              placeholder="How you met, or where they came from"
              {...form.getInputProps('source')}
            />
          </div>

          <div>
            <FieldLabel helper="optional">Notes</FieldLabel>
            <Textarea autosize minRows={2} {...form.getInputProps('notes')} />
          </div>

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