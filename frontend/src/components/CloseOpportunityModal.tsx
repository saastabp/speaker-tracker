import { Alert, Button, Group, Modal, SegmentedControl, Stack, Text, Textarea } from '@mantine/core';
import { useEffect, useState } from 'react';
import { ApiError } from '../api/client';
import { useFunnel } from '../api/funnel';
import { useCloseOpportunity } from '../api/opportunities';

/** The minimum a card/detail needs to pass in to be closed. */
export interface CloseTarget {
  id: number;
  title: string;
  current_status: string;
}

interface CloseOpportunityModalProps {
  opened: boolean;
  onClose: () => void;
  opportunity: CloseTarget | null;
  /** Called after a successful close (e.g. to navigate away from a detail page). */
  onClosed?: () => void;
}

// The pre/post-booking split only picks the default outcome label; the backend accepts either.
const BOOKED = 'booked';

export function CloseOpportunityModal({
  opened,
  onClose,
  opportunity,
  onClosed,
}: CloseOpportunityModalProps) {
  const funnel = useFunnel();
  const closeOpp = useCloseOpportunity();
  const [outcome, setOutcome] = useState('lost');
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);

  // Default from where the gig sits: Cancelled once it has reached Booked, else Lost / Passed.
  useEffect(() => {
    if (!opened || !opportunity) return;
    const stages = funnel.data ?? [];
    const bookedSort = stages.find((s) => s.short_name === BOOKED)?.sort_order;
    const currentSort = stages.find((s) => s.short_name === opportunity.current_status)?.sort_order;
    const postBooking = bookedSort != null && currentSort != null && currentSort >= bookedSort;
    setOutcome(postBooking ? 'cancelled' : 'lost');
    setReason('');
    setError(null);
  }, [opened, opportunity, funnel.data]);

  if (!opportunity) return null;
  const target = opportunity;

  async function handleClose() {
    if (!reason.trim()) {
      setError('A reason is required.');
      return;
    }
    setError(null);
    try {
      await closeOpp.mutateAsync({ id: target.id, status: outcome, reason: reason.trim() });
      onClose();
      onClosed?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong.');
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title={`Close "${target.title}"`}>
      <Stack>
        {error && (
          <Alert color="red" variant="light">
            {error}
          </Alert>
        )}
        <Text size="sm" c="dimmed">
          Closing moves this gig to History and records the reason.
        </Text>
        <SegmentedControl
          value={outcome}
          onChange={setOutcome}
          data={[
            { value: 'lost', label: 'Lost / Passed' },
            { value: 'cancelled', label: 'Cancelled' },
          ]}
        />
        <Textarea
          label="Reason"
          withAsterisk
          autosize
          minRows={2}
          value={reason}
          onChange={(event) => setReason(event.currentTarget.value)}
        />
        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button color="terracotta" loading={closeOpp.isPending} onClick={handleClose}>
            Close opportunity
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}