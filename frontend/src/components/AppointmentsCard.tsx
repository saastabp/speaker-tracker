import { ActionIcon, Anchor, Card, Group, Loader, Stack, Text } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconPencil } from '@tabler/icons-react';
import { useState } from 'react';
import { useAppointments, type Appointment } from '../api/appointments';
import { timestampDateTime } from '../dates';
import { AppointmentFormModal } from './AppointmentFormModal';
import { CardTitle } from './detailCards';

interface AppointmentsCardProps {
  /** The person whose appointments to show; the modal preselects and locks them. */
  contactId: number;
}

/**
 * Upcoming appointments with one person, on their detail page.
 *
 * **Upcoming only**, like the Dashboard card and for the same reason: this answers "what is still
 * ahead with them". Past appointments are history and live on the Appointments page behind its
 * toggle, rather than lengthening a panel meant to be read at a glance.
 *
 * The contact is passed through to the modal, so an appointment created from here is already
 * attached to the person being looked at and cannot be created against the wrong one.
 */
export function AppointmentsCard({ contactId }: AppointmentsCardProps) {
  const appointments = useAppointments({ contactId, scope: 'upcoming' });
  const [editing, setEditing] = useState<Appointment | null>(null);
  const [formOpen, formHandlers] = useDisclosure(false);

  function openCreate() {
    setEditing(null);
    formHandlers.open();
  }

  function openEdit(appointment: Appointment) {
    setEditing(appointment);
    formHandlers.open();
  }

  /** Close only — clearing `editing` here would flash the create form during the exit transition;
   *  see the note in `pages/Appointments.tsx`. Both open paths set the target explicitly. */
  function closeForm() {
    formHandlers.close();
  }

  return (
    <Card withBorder radius="md">
      <CardTitle
        action={
          <Anchor size="sm" onClick={openCreate} style={{ cursor: 'pointer' }}>
            + Add appointment
          </Anchor>
        }
      >
        Appointments
      </CardTitle>

      {appointments.isPending ? (
        <Loader size="sm" />
      ) : (appointments.data?.length ?? 0) === 0 ? (
        <Text c="dimmed" size="sm">
          Nothing scheduled.
        </Text>
      ) : (
        <Stack gap="xs">
          {appointments.data!.map((a) => (
            <Group key={a.id} justify="space-between" wrap="nowrap" align="flex-start">
              <div style={{ minWidth: 0 }}>
                <Text size="sm" fw={600}>
                  {timestampDateTime(a.scheduled_at)}
                </Text>
                <Text size="sm">{a.title}</Text>
                {a.details && (
                  <Text size="xs" c="dimmed" style={{ whiteSpace: 'pre-wrap' }}>
                    {a.details}
                  </Text>
                )}
              </div>
              <ActionIcon
                variant="subtle"
                aria-label="Edit"
                title="Edit"
                onClick={() => openEdit(a)}
                style={{ flexShrink: 0 }}
              >
                <IconPencil size={16} />
              </ActionIcon>
            </Group>
          ))}
        </Stack>
      )}

      <AppointmentFormModal
        opened={formOpen}
        onClose={closeForm}
        appointment={editing}
        contactId={contactId}
      />
    </Card>
  );
}