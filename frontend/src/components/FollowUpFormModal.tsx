import {
  Alert,
  Button,
  Group,
  Modal,
  Select,
  Stack,
  Switch,
  Text,
  Textarea,
  TextInput,
} from '@mantine/core';
import { useEffect, useState } from 'react';
import { ApiError } from '../api/client';
import { useContacts } from '../api/contacts';
import { useCreateFollowUp, usePatchFollowUp, type FollowUp } from '../api/followUps';
import { useOpportunities } from '../api/opportunities';
import { FieldLabel } from './FieldLabel';

interface FollowUpFormModalProps {
  opened: boolean;
  onClose: () => void;
  /** Pass an existing follow-up to edit it; omit to create a new one. */
  followUp?: FollowUp | null;
  /** Preselect and lock the contact (opened from a contact page). */
  contactId?: number;
  /** Preselect and lock the opportunity (opened from an opportunity page). */
  opportunityId?: number;
}

/**
 * Create or edit a follow-up reminder.
 *
 * One modal for both, because the fields are identical — except that **the contact and opportunity
 * links are not editable once the follow-up exists**. The API deliberately does not accept them on
 * a patch (re-linking would mean re-validating the at-least-one-parent constraint on every edit,
 * for no use case this app has), so offering the selects in edit mode would be an control that
 * silently does nothing. They are disabled with the reason shown instead.
 *
 * The reminder always fires at 07:00 in the user's own timezone; there is no time picker because
 * the hour is not stored — it is applied server-side when the schedule is built.
 */
export function FollowUpFormModal({
  opened,
  onClose,
  followUp,
  contactId,
  opportunityId,
}: FollowUpFormModalProps) {
  const contacts = useContacts();
  const opportunities = useOpportunities(false);
  const create = useCreateFollowUp();
  const patch = usePatchFollowUp();

  const editing = followUp != null;
  const contactLocked = editing || contactId != null;
  const opportunityLocked = editing || opportunityId != null;

  const [selectedContact, setSelectedContact] = useState<string | null>(null);
  const [selectedOpportunity, setSelectedOpportunity] = useState<string | null>(null);
  const [dueDate, setDueDate] = useState('');
  const [note, setNote] = useState('');
  const [remind, setRemind] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Reset each time it opens, from the follow-up being edited or from the page's context.
  useEffect(() => {
    if (!opened) return;
    setSelectedContact(
      followUp?.contact_id != null
        ? String(followUp.contact_id)
        : contactId != null
          ? String(contactId)
          : null,
    );
    setSelectedOpportunity(
      followUp?.opportunity_id != null
        ? String(followUp.opportunity_id)
        : opportunityId != null
          ? String(opportunityId)
          : null,
    );
    setDueDate(followUp?.due_date ?? '');
    setNote(followUp?.note ?? '');
    setRemind(followUp?.remind_by_email ?? true);
    setError(null);
  }, [opened, followUp, contactId, opportunityId]);

  const contactOptions = (contacts.data ?? []).map((c) => ({
    value: String(c.id),
    label: c.name,
  }));
  const opportunityOptions = (opportunities.data ?? []).map((o) => ({
    value: String(o.id),
    label: o.title,
  }));

  async function handleSubmit() {
    if (!dueDate) {
      setError('Pick a due date.');
      return;
    }
    if (!note.trim()) {
      setError('Add a note — it becomes the body of the reminder.');
      return;
    }
    // Mirrors the server's check (and the database CHECK behind it) so the message names the fix
    // rather than surfacing a generic 400.
    if (!editing && !selectedContact && !selectedOpportunity) {
      setError('Attach the follow-up to a contact, an opportunity, or both.');
      return;
    }

    setError(null);
    setSubmitting(true);
    try {
      if (editing) {
        await patch.mutateAsync({
          id: followUp.id,
          due_date: dueDate,
          note: note.trim(),
          remind_by_email: remind,
        });
      } else {
        await create.mutateAsync({
          due_date: dueDate,
          note: note.trim(),
          contact_id: selectedContact ? Number(selectedContact) : null,
          opportunity_id: selectedOpportunity ? Number(selectedOpportunity) : null,
          remind_by_email: remind,
        });
      }
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
      title={editing ? 'Edit follow-up' : 'Schedule follow-up'}
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
            placeholder="— none —"
            data={contactOptions}
            value={selectedContact}
            onChange={setSelectedContact}
            disabled={contactLocked}
            searchable
            clearable
          />
        </div>

        <div>
          <FieldLabel>Opportunity</FieldLabel>
          <Select
            placeholder="— none —"
            data={opportunityOptions}
            value={selectedOpportunity}
            onChange={setSelectedOpportunity}
            disabled={opportunityLocked}
            searchable
            clearable
          />
        </div>

        {editing && (
          <Text size="xs" c="dimmed">
            What a follow-up is attached to is fixed when it is created. Delete it and make a new
            one to move it.
          </Text>
        )}

        <div>
          <FieldLabel>Due date</FieldLabel>
          <TextInput
            type="date"
            value={dueDate}
            onChange={(event) => setDueDate(event.currentTarget.value)}
          />
        </div>

        <div>
          <FieldLabel>Note</FieldLabel>
          <Textarea
            placeholder="What to do or say when this comes due…"
            autosize
            minRows={3}
            value={note}
            onChange={(event) => setNote(event.currentTarget.value)}
          />
        </div>

        <Switch
          checked={remind}
          onChange={(event) => setRemind(event.currentTarget.checked)}
          label="Remind me"
          description="Shows on the Dashboard when it comes due, and emails you at 7am that morning. Turn off to keep it on the Dashboard only."
        />

        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} loading={submitting}>
            {editing ? 'Save changes' : 'Schedule follow-up'}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}