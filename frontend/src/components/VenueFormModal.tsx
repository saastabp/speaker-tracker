import { Alert, Button, Group, Modal, Select, Stack, Text, Textarea, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { useEffect, useState } from 'react';
import { useCatalogs } from '../api/catalogs';
import { ApiError } from '../api/client';
import type { OrganizationInput } from '../api/organizations';
import { BRAND_LINE, BRAND_PANEL } from '../theme';
import { FieldLabel } from './FieldLabel';

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
  const isCreate = !initialValues;

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

          <div>
            <FieldLabel>Organization name</FieldLabel>
            <TextInput {...form.getInputProps('name')} />
          </div>

          <Group grow align="flex-start">
            <div>
              <FieldLabel>Type</FieldLabel>
              <Select
                placeholder="Select a type"
                data={typeOptions}
                searchable
                {...form.getInputProps('organization_type')}
              />
            </div>
            <div>
              <FieldLabel>Location</FieldLabel>
              <TextInput {...form.getInputProps('location')} />
            </div>
          </Group>

          <Group grow align="flex-start">
            <div>
              <FieldLabel helper="optional">Website</FieldLabel>
              <TextInput placeholder="https://…" {...form.getInputProps('website_url')} />
            </div>
            <div>
              <FieldLabel helper="optional">Email domain</FieldLabel>
              <TextInput placeholder="venue.com" {...form.getInputProps('email_domain')} />
            </div>
          </Group>

          <Text
            fw={700}
            size="xs"
            tt="uppercase"
            c="terracotta.7"
            mt="xs"
            pb={6}
            style={{ letterSpacing: '0.05em', borderBottom: `1px solid ${BRAND_LINE}` }}
          >
            Research — Kindling
          </Text>

          <div>
            <FieldLabel>What it is</FieldLabel>
            <Textarea autosize minRows={2} {...form.getInputProps('what_it_is')} />
          </div>
          <div>
            <FieldLabel>Why it fits</FieldLabel>
            <Textarea autosize minRows={2} {...form.getInputProps('why_it_fits')} />
          </div>
          <div>
            <FieldLabel>How to approach</FieldLabel>
            <Textarea
              autosize
              minRows={2}
              placeholder="What's the play? (attend an event first, warm intro, who to ask for…)"
              {...form.getInputProps('how_to_approach')}
            />
          </div>
          <div>
            <FieldLabel helper="optional">Notes</FieldLabel>
            <Textarea autosize minRows={2} {...form.getInputProps('notes')} />
          </div>

          <Text size="xs" c="dimmed" p="sm" style={{ background: BRAND_PANEL, borderRadius: 8 }}>
            Fill in all three research fields and add at least one contact to count this venue toward
            the new-venues-researched target.
          </Text>

          <Group justify="flex-end" mt="sm">
            {isCreate ? (
              <>
                <Button type="submit" variant="default" loading={submitting}>
                  Save, finish research later
                </Button>
                <Button type="submit" loading={submitting}>
                  {submitLabel}
                </Button>
              </>
            ) : (
              <>
                <Button variant="default" onClick={onClose}>
                  Cancel
                </Button>
                <Button type="submit" loading={submitting}>
                  {submitLabel}
                </Button>
              </>
            )}
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}