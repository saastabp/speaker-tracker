import {
  Alert,
  Button,
  Group,
  Modal,
  SegmentedControl,
  Select,
  Stack,
  Text,
  Textarea,
  TextInput,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { useEffect, useState } from 'react';
import { useCatalogs } from '../api/catalogs';
import { ApiError } from '../api/client';
import type { Opportunity, OpportunityInput } from '../api/opportunities';
import { useOrganizations } from '../api/organizations';
import { useTalks } from '../api/talks';

// The form works in strings (Select/TextInput values); it maps to OpportunityInput on submit.
interface FormValues {
  title: string;
  organization_id: string;
  talk_id: string;
  opportunity_format: string;
  comp_type: string;
  event_date: string;
  fee_amount: string;
  currency: string;
  angle: string;
}

function toFormValues(opp?: Opportunity): FormValues {
  return {
    title: opp?.title ?? '',
    organization_id: opp ? String(opp.organization_id) : '',
    talk_id: opp?.talk_id != null ? String(opp.talk_id) : '',
    opportunity_format: opp?.opportunity_format ?? '',
    comp_type: opp?.comp_type ?? 'paid',
    event_date: opp?.event_date ?? '',
    fee_amount: opp?.fee_amount ?? '',
    currency: opp?.currency ?? 'USD',
    angle: opp?.angle ?? '',
  };
}

function toInput(values: FormValues): OpportunityInput {
  const fee = values.fee_amount.trim();
  return {
    title: values.title.trim(),
    organization_id: Number(values.organization_id),
    opportunity_format: values.opportunity_format,
    comp_type: values.comp_type,
    talk_id: values.talk_id ? Number(values.talk_id) : null,
    event_date: values.event_date || null,
    fee_amount: fee || null,
    currency: values.currency.trim() || 'USD',
    angle: values.angle.trim() || null,
  };
}

interface OpportunityFormModalProps {
  opened: boolean;
  onClose: () => void;
  title: string;
  submitLabel: string;
  /** Edit mode seeds the form from an existing opportunity; omit for create. */
  initialValues?: Opportunity;
  onSubmit: (values: OpportunityInput) => Promise<unknown>;
}

export function OpportunityFormModal({
  opened,
  onClose,
  title,
  submitLabel,
  initialValues,
  onSubmit,
}: OpportunityFormModalProps) {
  const catalogs = useCatalogs();
  const venues = useOrganizations();
  const talks = useTalks();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const form = useForm<FormValues>({
    initialValues: toFormValues(initialValues),
    validate: {
      title: (value) => (value.trim() ? null : 'Title is required'),
      organization_id: (value) => (value ? null : 'Venue is required'),
      opportunity_format: (value) => (value ? null : 'Format is required'),
      comp_type: (value) => (value ? null : 'Compensation is required'),
      fee_amount: (value) =>
        !value.trim() || /^\d+(\.\d{1,2})?$/.test(value.trim()) ? null : 'Enter an amount like 1500 or 1500.00',
    },
  });

  // Mantine's useForm doesn't auto-sync initialValues; refresh on each open.
  useEffect(() => {
    if (opened) {
      const values = toFormValues(initialValues);
      // The Format segmented control has no empty state — default it to the first format on create.
      if (!values.opportunity_format) {
        const first = catalogs.data?.opportunity_formats?.[0]?.short_name;
        if (first) values.opportunity_format = first;
      }
      form.setValues(values);
      setError(null);
    }
  }, [opened]); // eslint-disable-line react-hooks/exhaustive-deps

  const venueOptions = (venues.data ?? []).map((v) => ({ value: String(v.id), label: v.name }));
  const talkOptions = (talks.data ?? []).map((t) => ({ value: String(t.id), label: t.title }));
  const formatOptions = (catalogs.data?.opportunity_formats ?? []).map((f) => ({
    value: f.short_name,
    label: f.description,
  }));
  const compOptions = (catalogs.data?.comp_types ?? []).map((c) => ({
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
          <TextInput label="Title" withAsterisk {...form.getInputProps('title')} />
          <Select
            label="Venue / organization"
            placeholder="Select a venue"
            data={venueOptions}
            withAsterisk
            searchable
            {...form.getInputProps('organization_id')}
          />
          <Group grow align="flex-start">
            <Select
              label="Talk / offer"
              placeholder="Optional"
              data={talkOptions}
              clearable
              searchable
              {...form.getInputProps('talk_id')}
            />
            <TextInput label="Event date" type="date" {...form.getInputProps('event_date')} />
          </Group>
          <div>
            <Text size="sm" fw={500} mb={4}>
              Format{' '}
              <Text span c="red">
                *
              </Text>
            </Text>
            <SegmentedControl
              data={formatOptions}
              value={form.values.opportunity_format}
              onChange={(value) => form.setFieldValue('opportunity_format', value)}
            />
          </div>
          <Textarea
            label="Angle for this gig"
            description="Seeded from the venue's approach; edit as needed"
            autosize
            minRows={2}
            {...form.getInputProps('angle')}
          />
          <Text
            fw={700}
            size="xs"
            tt="uppercase"
            c="terracotta.7"
            mt="xs"
            style={{ letterSpacing: '0.04em' }}
          >
            Compensation
          </Text>
          <Group grow align="flex-start">
            <div>
              <Text size="sm" fw={500} mb={4}>
                Type
              </Text>
              <SegmentedControl
                data={compOptions}
                value={form.values.comp_type}
                onChange={(value) => form.setFieldValue('comp_type', value)}
              />
            </div>
            <TextInput
              label="Fee"
              description="if paid"
              placeholder="e.g. 1500.00"
              {...form.getInputProps('fee_amount')}
            />
          </Group>
          <Text size="xs" c="dimmed" fs="italic">
            Pro bono still counts as a booking — it just carries no fee and shows a “Pro bono” chip
            instead of a dollar amount.
          </Text>
          <Group justify="space-between" mt="sm">
            <Group>
              <Button type="submit" loading={submitting}>
                {submitLabel}
              </Button>
              <Button variant="default" onClick={onClose}>
                Cancel
              </Button>
            </Group>
            {!initialValues && (
              <Text size="xs" c="dimmed">
                Starts in Researching — drag it across the board as it advances
              </Text>
            )}
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}