import { Alert, Button, Group, Modal, Select, Stack, Textarea, TextInput } from '@mantine/core';
import { useEffect, useState } from 'react';
import {
  useCreateAppointment,
  useDeleteAppointment,
  usePatchAppointment,
  type Appointment,
} from '../api/appointments';
import { ApiError } from '../api/client';
import { useContacts } from '../api/contacts';
import { FieldLabel } from './FieldLabel';

interface AppointmentFormModalProps {
  opened: boolean;
  onClose: () => void;
  /** Pass an existing appointment to edit it; omit to log a new one. */
  appointment?: Appointment | null;
  /** Preselect and lock the contact (opened from a contact page). */
  contactId?: number;
}

/**
 * Log or edit an appointment.
 *
 * A logging form, not a scheduler: no invite, no reminder, no conflict check — every control here
 * writes exactly one column. The **contact stays editable on an existing appointment**, unlike a
 * follow-up's links: there is one required link rather than a constraint spanning two, so
 * re-pointing it is an ordinary correction rather than a re-validation.
 *
 * The time is an `<input type="datetime-local">` and travels as the wall clock it displays. 2pm is
 * 2pm — nothing converts it on the way to the DATETIME column, which is the point of that column.
 */
export function AppointmentFormModal({
  opened,
  onClose,
  appointment,
  contactId,
}: AppointmentFormModalProps) {
  const contacts = useContacts();
  const create = useCreateAppointment();
  const patch = usePatchAppointment();
  const remove = useDeleteAppointment();

  const editing = appointment != null;
  const locked = contactId != null;

  const [selectedContact, setSelectedContact] = useState<string | null>(null);
  const [title, setTitle] = useState('');
  const [scheduledAt, setScheduledAt] = useState('');
  const [details, setDetails] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Reset each time it opens, from the appointment being edited or from the page's context.
  useEffect(() => {
    if (!opened) return;
    setSelectedContact(
      appointment?.contact_id != null
        ? String(appointment.contact_id)
        : contactId != null
          ? String(contactId)
          : null,
    );
    setTitle(appointment?.title ?? '');
    // `slice(0, 16)` drops the seconds the API sends; datetime-local rejects them.
    setScheduledAt(appointment ? appointment.scheduled_at.slice(0, 16) : '');
    setDetails(appointment?.details ?? '');
    setError(null);
  }, [opened, appointment, contactId]);

  const contactOptions = (contacts.data ?? []).map((c) => ({ value: String(c.id), label: c.name }));

  async function handleSubmit() {
    if (!selectedContact) {
      setError('Pick who the appointment is with.');
      return;
    }
    if (!title.trim()) {
      setError('Give it a title — it is what the Dashboard and the list show.');
      return;
    }
    if (!scheduledAt) {
      setError('Pick a date and time.');
      return;
    }
    setError(null);
    setSubmitting(true);
    const values = {
      contact_id: Number(selectedContact),
      title: title.trim(),
      scheduled_at: scheduledAt,
      // Blank arrives as an explicit null, which is what clears it — see AppointmentPatch.
      details: details.trim() || null,
    };
    try {
      if (editing) {
        await patch.mutateAsync({ id: appointment.id, ...values });
      } else {
        await create.mutateAsync(values);
      }
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong.');
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete() {
    if (!appointment) return;
    if (!window.confirm(`Delete “${appointment.title}”?`)) return;
    setSubmitting(true);
    try {
      await remove.mutateAsync({ id: appointment.id });
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={editing ? 'Edit appointment' : 'Add appointment'}
      size="lg"
    >
      <Stack>
        {error && (
          <Alert color="red" variant="light">
            {error}
          </Alert>
        )}

        <div>
          <FieldLabel>Contact</FieldLabel>
          <Select
            placeholder="Who is it with?"
            data={contactOptions}
            value={selectedContact}
            onChange={setSelectedContact}
            disabled={locked}
            searchable
          />
        </div>

        <div>
          <FieldLabel>Title</FieldLabel>
          <TextInput
            placeholder="Coffee, site visit, intro call…"
            value={title}
            onChange={(event) => setTitle(event.currentTarget.value)}
          />
        </div>

        <div>
          <FieldLabel>Date and time</FieldLabel>
          <TextInput
            type="datetime-local"
            value={scheduledAt}
            onChange={(event) => setScheduledAt(event.currentTarget.value)}
          />
        </div>

        <div>
          <FieldLabel helper="optional">Details</FieldLabel>
          <Textarea
            placeholder="Where, what to bring, what it is about…"
            autosize
            minRows={3}
            value={details}
            onChange={(event) => setDetails(event.currentTarget.value)}
          />
        </div>

        <Group justify="space-between" mt="sm">
          {editing ? (
            <Button variant="light" color="red" onClick={handleDelete} disabled={submitting}>
              Delete
            </Button>
          ) : (
            <span />
          )}
          <Group>
            <Button variant="subtle" onClick={onClose}>
              Cancel
            </Button>
            <Button onClick={handleSubmit} loading={submitting}>
              {editing ? 'Save changes' : 'Add appointment'}
            </Button>
          </Group>
        </Group>
      </Stack>
    </Modal>
  );
}