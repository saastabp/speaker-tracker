import { Stack, Switch, Textarea, TextInput } from '@mantine/core';
import { FieldLabel } from './FieldLabel';

export interface FollowUpRiderValue {
  due_date: string;
  note: string;
}

interface FollowUpRiderFieldsProps {
  enabled: boolean;
  onEnabledChange: (enabled: boolean) => void;
  value: FollowUpRiderValue;
  onChange: (value: FollowUpRiderValue) => void;
  /** Wording for what this reminder rides on, e.g. "sending this" or "logging this touch". */
  description?: string;
}

/**
 * The opt-in "schedule a follow-up" rider shared by the composer and the log-outreach modal.
 *
 * **Off by default, and the caller must send `null` when it is off** — sending an email or logging
 * a touch never silently schedules anything (DESIGN.md §7, slice 7 acceptance #6). One component
 * rather than two copies so the default cannot drift on one surface and not the other.
 *
 * The note is optional: left blank, the server derives one from what the rider is attached to (the
 * email's subject, or the touch), so the common case is picking a date and nothing else.
 */
export function FollowUpRiderFields({
  enabled,
  onEnabledChange,
  value,
  onChange,
  description = 'Pick a date and it surfaces on the Dashboard that morning, with an email nudge.',
}: FollowUpRiderFieldsProps) {
  return (
    <Stack gap="xs">
      <Switch
        checked={enabled}
        onChange={(event) => onEnabledChange(event.currentTarget.checked)}
        label="Schedule a follow-up"
        description={description}
      />

      {enabled && (
        <Stack gap="xs" pl="xl">
          <div>
            <FieldLabel>Follow-up date</FieldLabel>
            <TextInput
              type="date"
              value={value.due_date}
              onChange={(event) => onChange({ ...value, due_date: event.currentTarget.value })}
            />
          </div>
          <div>
            <FieldLabel>Follow-up note</FieldLabel>
            <Textarea
              placeholder="What to do when this comes due… (optional)"
              autosize
              minRows={2}
              value={value.note}
              onChange={(event) => onChange({ ...value, note: event.currentTarget.value })}
            />
          </div>
        </Stack>
      )}
    </Stack>
  );
}
