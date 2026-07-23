import { Alert, Button, Group, Modal, Select, Stack, Textarea, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { useEffect, useState } from 'react';
import { useCatalogs } from '../api/catalogs';
import { ApiError } from '../api/client';
import type { OrganizationInput } from '../api/organizations';

const EMPTY: OrganizationInput = {
  organization_type: '',
  name: '',
  location: '',
  website_url: '',
  email_domain: '',
  what_it_is: '',
  why_it_fits: '',
  how_to_approach: '',
  notes: '',
};

/** Coerce nulls to '' so every field is a controlled string input. */
function normalize(values?: OrganizationInput): OrganizationInput {
  const base = values ?? EMPTY;
  return {
    organization_type: base.organization_type ?? '',
    name: base.name ?? '',
    location: base.location ?? '',
    website_url: base.website_url ?? '',
    email_domain: base.email_domain ?? '',
    what_it_is: base.what_it_is ?? '',
    why_it_fits: base.why_it_fits ?? '',
    how_to_approach: base.how_to_approach ?? '',
    notes: base.notes ?? '',
  };
}

interface VenueFormModalProps {
  opened: boolean;
  onClose: () => void;
  title: string;
  submitLabel: string;
  initialValues?: OrganizationInput;
  /** Perform the create/update; may throw ApiError (e.g. 409 duplicate name). */
  onSubmit: (values: OrganizationInput) => Promise<unknown>;
}

export function VenueFormModal({
  opened,
  onClose,
  title,
  submitLabel,
  initialValues,
  onSubmit,
}: VenueFormModalProps) {
  const catalogs = useCatalogs();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const form = useForm<OrganizationInput>({
    initialValues: normalize(initialValues),
    validate: {
      organization_type: (value) => (value ? null : 'Type is required'),
      name: (value) => (value.trim() ? null : 'Name is required'),
    },
  });

  // Mantine's useForm doesn't auto-sync initialValues; refresh on each open.
  useEffect(() => {
    if (opened) {
      form.setValues(normalize(initialValues));
      setError(null);
    }
  }, [opened]); // eslint-disable-line react-hooks/exhaustive-deps

  const typeOptions = (catalogs.data?.organization_types ?? []).map((type) => ({
    value: type.short_name,
    label: type.description,
  }));

  async function handleSubmit(values: OrganizationInput) {
    setError(null);
    setSubmitting(true);
    try {
      await onSubmit(values);
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
          <Select
            label="Type"
            placeholder="Select a type"
            data={typeOptions}
            withAsterisk
            searchable
            {...form.getInputProps('organization_type')}
          />
          <TextInput label="Name" withAsterisk {...form.getInputProps('name')} />
          <Group grow>
            <TextInput label="Location" {...form.getInputProps('location')} />
            <TextInput label="Website" {...form.getInputProps('website_url')} />
          </Group>
          <TextInput
            label="Email domain"
            description="Matches inbound email later, e.g. venue.com"
            {...form.getInputProps('email_domain')}
          />
          <Textarea label="What it is" autosize minRows={2} {...form.getInputProps('what_it_is')} />
          <Textarea
            label="Why it fits"
            autosize
            minRows={2}
            {...form.getInputProps('why_it_fits')}
          />
          <Textarea
            label="How to approach"
            autosize
            minRows={2}
            {...form.getInputProps('how_to_approach')}
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