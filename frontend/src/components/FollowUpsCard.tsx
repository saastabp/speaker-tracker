import { ActionIcon, Anchor, Badge, Card, Group, Loader, Stack, Text } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconCheck, IconPencil } from '@tabler/icons-react';
import { useState } from 'react';
import { useFollowUps, usePatchFollowUp, type FollowUp } from '../api/followUps';
import { CardTitle } from './detailCards';
import { FollowUpFormModal } from './FollowUpFormModal';

interface FollowUpsCardProps {
  /** Scope to a contact's reminders; the modal then preselects and locks that contact. */
  contactId?: number;
  /** Scope to an opportunity's reminders; the modal preselects and locks that opportunity. */
  opportunityId?: number;
}

function parseDateLocal(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d);
}

function startOfToday(): Date {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

/**
 * The follow-up panel on a contact or opportunity detail page.
 *
 * Shows **pending** reminders only, soonest first. Completed ones are deliberately not listed here
 * — this card is a short "what is still owed on this relationship" answer, and the Follow-ups page
 * is where history lives. The mockup calls it "Next follow-up" and shows one; it lists all pending
 * because there can legitimately be several and hiding the rest would make them easy to forget.
 *
 * The parent is passed through to the modal, so a reminder created from here is already attached to
 * the thing being looked at and cannot be created dangling.
 */
export function FollowUpsCard({ contactId, opportunityId }: FollowUpsCardProps) {
  const followUps = useFollowUps({ contactId, opportunityId, pendingOnly: true });
  const patch = usePatchFollowUp();
  const [editing, setEditing] = useState<FollowUp | null>(null);
  const [formOpen, formHandlers] = useDisclosure(false);

  const today = startOfToday();

  function openCreate() {
    setEditing(null);
    formHandlers.open();
  }

  function openEdit(followUp: FollowUp) {
    setEditing(followUp);
    formHandlers.open();
  }

  function closeForm() {
    formHandlers.close();
    setEditing(null);
  }

  return (
    <Card withBorder radius="md">
      <CardTitle
        action={
          <Anchor size="sm" onClick={openCreate} style={{ cursor: 'pointer' }}>
            + Schedule follow-up
          </Anchor>
        }
      >
        Follow-ups
      </CardTitle>

      {followUps.isPending ? (
        <Loader size="sm" />
      ) : (followUps.data?.length ?? 0) === 0 ? (
        <Text c="dimmed" size="sm">
          Nothing scheduled.
        </Text>
      ) : (
        <Stack gap="xs">
          {followUps.data!.map((f) => {
            const overdue = parseDateLocal(f.due_date) < today;
            return (
              <Group key={f.id} justify="space-between" wrap="nowrap" align="flex-start">
                <div style={{ minWidth: 0 }}>
                  <Group gap="xs" wrap="nowrap">
                    <Text size="sm" fw={600}>
                      {parseDateLocal(f.due_date).toLocaleDateString()}
                    </Text>
                    {overdue && (
                      <Badge color="warn" variant="light" size="xs">
                        overdue
                      </Badge>
                    )}
                    {f.reminder_failed_at && (
                      <Badge color="terracotta" variant="filled" size="xs">
                        reminder didn't send
                      </Badge>
                    )}
                    {!f.remind_by_email && (
                      <Badge color="gray" variant="light" size="xs">
                        no email
                      </Badge>
                    )}
                  </Group>
                  <Text size="sm" c="dimmed" style={{ whiteSpace: 'pre-wrap' }}>
                    {f.note}
                  </Text>
                </div>
                <Group gap={4} wrap="nowrap" style={{ flexShrink: 0 }}>
                  <ActionIcon
                    variant="subtle"
                    aria-label="Mark done"
                    title="Mark done"
                    onClick={() => patch.mutate({ id: f.id, completed: true })}
                  >
                    <IconCheck size={16} />
                  </ActionIcon>
                  <ActionIcon
                    variant="subtle"
                    aria-label="Edit"
                    title="Edit"
                    onClick={() => openEdit(f)}
                  >
                    <IconPencil size={16} />
                  </ActionIcon>
                </Group>
              </Group>
            );
          })}
        </Stack>
      )}

      <FollowUpFormModal
        opened={formOpen}
        onClose={closeForm}
        followUp={editing}
        contactId={contactId}
        opportunityId={opportunityId}
      />
    </Card>
  );
}