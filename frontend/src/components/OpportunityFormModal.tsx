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
import { useContacts } from '../api/contacts';
import type { Opportunity, OpportunityCreateInput } from '../api/opportunities';
import { useOrganizations } from '../api/organizations';
import { useTalks } from '../api/talks';
import { BRAND_LINE } from '../theme';
import { FieldLabel } from './FieldLabel';

// The form works in strings (Select/SegmentedControl/TextInput values); it maps to
// OpportunityCreateInput on submit. `title` is derived from venue + talk (no free-text field), and
// the lifecycle seeds (starting_status / payment_status / lead_contact_id) are sent on create only.
interface FormValues {
  organization_id: string;
  talk_id: string;
  opportunity_format: string;
  comp_type: string;
  event_date: string;
  fee_amount: string;
  angle: string;
  starting_status: string;
  payment_status: string;
  lead_contact_id: string;
}

function toFormValues(opp?: Opportunity): FormValues {
  return {
    organization_id: opp ? String(opp.organization_id) : '',
    talk_id: opp?.talk_id != null ? String(opp.talk_id) : '',
    opportunity_format: opp?.opportunity_format ?? '',
    comp_type: opp?.comp_type ?? 'paid',
    event_date: opp?.event_date ?? '',
    fee_amount: opp?.fee_amount ?? '',
    angle: opp?.angle ?? '',
    starting_status: 'researching', // create default; ignored in edit mode
    payment_status: 'unbilled', // create default; ignored in edit mode
    lead_contact_id: '',
  };
}

interface OpportunityFormModalProps {
  opened: boolean;
  onClose: () => void;
  title: string;
  submitLabel: string;
  /** Edit mode seeds the form from an existing opportunity; omit for create. */
  initialValues?: Opportunity;
  onSubmit: (values: OpportunityCreateInput) => Promise<unknown>;
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
  const contacts = useContacts();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const isCreate = !initialValues;

  const form = useForm<FormValues>({
    initialValues: toFormValues(initialValues),
    validate: {
      organization_id: (value) => (value ? null : 'Venue is required'),
      opportunity_format: (value) => (value ? null : 'Format is required'),
      comp_type: (value) => (value ? null : 'Compensation is required'),
      fee_amount: (value) =>
        !value.trim() || /^\d+(\.\d{1,2})?$/.test(value.trim())
          ? null
          : 'Enter an amount like 1500 or 1500.00',
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
  const contactOptions = (contacts.data ?? []).map((c) => ({
    value: String(c.id),
    label: c.name,
  }));
  const formatOptions = (catalogs.data?.opportunity_formats ?? []).map((f) => ({
    value: f.short_name,
    label: f.description,
  }));
  const compOptions = (catalogs.data?.comp_types ?? []).map((c) => ({
    value: c.short_name,
    label: c.description,
  }));
  // Starting stage offers the non-terminal board stages only (a new gig never starts closed).
  const statusOptions = (catalogs.data?.opportunity_statuses ?? [])
    .filter((s) => !s.is_terminal)
    .sort((a, b) => a.sort_order - b.sort_order)
    .map((s) => ({ value: s.short_name, label: s.description }));
  // Payment status excludes n_a — that is the pro-bono / trade auto value, not a paid-gig choice.
  const paymentOptions = (catalogs.data?.payment_statuses ?? [])
    .filter((p) => p.short_name !== 'n_a')
    .sort((a, b) => a.sort_order - b.sort_order)
    .map((p) => ({ value: p.short_name, label: p.description }));

  async function handleSubmit(values: FormValues) {
    setError(null);
    setSubmitting(true);
    try {
      // Title is derived from venue + talk (mockup dropped the free-text field).
      const venueName = venueOptions.find((o) => o.value === values.organization_id)?.label ?? '';
      const talkTitle = talkOptions.find((o) => o.value === values.talk_id)?.label ?? '';
      const input: OpportunityCreateInput = {
        title: talkTitle ? `${venueName} — ${talkTitle}` : venueName,
        organization_id: Number(values.organization_id),
        opportunity_format: values.opportunity_format,
        comp_type: values.comp_type,
        talk_id: values.talk_id ? Number(values.talk_id) : null,
        event_date: values.event_date || null,
        fee_amount: values.fee_amount.trim() || null,
        currency: 'USD',
        angle: values.angle.trim() || null,
      };
      if (isCreate) {
        input.starting_status = values.starting_status;
        input.lead_contact_id = values.lead_contact_id ? Number(values.lead_contact_id) : null;
        // Payment status is a paid-gig concern; pro bono / trade derive n_a server-side.
        if (values.comp_type === 'paid') {
          input.payment_status = values.payment_status;
        }
      }
      await onSubmit(input);
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
            <FieldLabel>Venue / organization</FieldLabel>
            <Select
              placeholder="Select a venue"
              data={venueOptions}
              searchable
              {...form.getInputProps('organization_id')}
            />
          </div>

          <Group grow align="flex-start">
            <div>
              <FieldLabel helper="optional">Talk / offer</FieldLabel>
              <Select
                placeholder="Which talk was offered?"
                data={talkOptions}
                clearable
                searchable
                {...form.getInputProps('talk_id')}
              />
            </div>
            <div>
              <FieldLabel helper="optional">Event date</FieldLabel>
              <TextInput type="date" {...form.getInputProps('event_date')} />
            </div>
          </Group>

          <div>
            <FieldLabel>Format</FieldLabel>
            <SegmentedControl
              data={formatOptions}
              value={form.values.opportunity_format}
              onChange={(value) => form.setFieldValue('opportunity_format', value)}
            />
          </div>

          {isCreate && (
            <Group grow align="flex-start">
              <div>
                <FieldLabel>Starting stage</FieldLabel>
                <Select
                  data={statusOptions}
                  allowDeselect={false}
                  {...form.getInputProps('starting_status')}
                />
              </div>
              <div>
                <FieldLabel helper="optional">Lead contact</FieldLabel>
                <Select
                  placeholder="Who's the lead on this gig?"
                  data={contactOptions}
                  clearable
                  searchable
                  {...form.getInputProps('lead_contact_id')}
                />
              </div>
            </Group>
          )}

          <div>
            <FieldLabel helper="optional">Angle for this gig</FieldLabel>
            <Textarea
              placeholder="Seeded from the venue's approach; edit as needed"
              autosize
              minRows={2}
              {...form.getInputProps('angle')}
            />
          </div>

          <Text
            fw={700}
            size="xs"
            tt="uppercase"
            c="terracotta.7"
            mt="xs"
            pb={6}
            style={{ letterSpacing: '0.05em', borderBottom: `1px solid ${BRAND_LINE}` }}
          >
            Compensation
          </Text>

          <Group grow align="flex-start">
            <div>
              <FieldLabel>Type</FieldLabel>
              <SegmentedControl
                data={compOptions}
                value={form.values.comp_type}
                onChange={(value) => form.setFieldValue('comp_type', value)}
              />
            </div>
            <div>
              <FieldLabel helper="if paid">Fee</FieldLabel>
              <TextInput placeholder="e.g. 1500.00" {...form.getInputProps('fee_amount')} />
            </div>
          </Group>

          {isCreate && form.values.comp_type === 'paid' && (
            <div>
              <FieldLabel>Payment status</FieldLabel>
              <SegmentedControl
                data={paymentOptions}
                value={form.values.payment_status}
                onChange={(value) => form.setFieldValue('payment_status', value)}
              />
            </div>
          )}

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
            {isCreate && (
              <Text size="xs" c="dimmed">
                Starts in{' '}
                {statusOptions.find((s) => s.value === form.values.starting_status)?.label ??
                  'Researching'}{' '}
                — drag it across the board as it advances
              </Text>
            )}
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}