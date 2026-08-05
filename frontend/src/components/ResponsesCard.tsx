import { ActionIcon, Card, Divider, Group, Text } from '@mantine/core';
import { IconMinus, IconPlus } from '@tabler/icons-react';
import { catalogLabel, useCatalogs } from '../api/catalogs';
import { useSetResponseCount, type Opportunity } from '../api/opportunities';
import { CardTitle } from './detailCards';

interface ResponsesCardProps {
  opportunity: Opportunity;
}

/**
 * The audience-growth counters on a gig: one row per response type, `-` / count / `+`, and a total.
 *
 * A grid rather than a list of entries, because that is what the data is — these *count* responses,
 * they do not record them. When each one arrived and who it was live in legacy-tracker and GHL. It
 * is also why there is no delete control: pressing `-` is the correction.
 *
 * Rows come from the **catalog**, not from the stored counters, so all three types are always
 * present and one nobody has used yet reads zero instead of being missing. The write sends the
 * resulting value rather than a delta, so a fast double-click settles on one number instead of
 * counting twice.
 */
export function ResponsesCard({ opportunity }: ResponsesCardProps) {
  const catalogs = useCatalogs();
  const setCount = useSetResponseCount();

  const types = catalogs.data?.opportunity_response_types ?? [];
  const counts = new Map(opportunity.responses.map((r) => [r.response_type, r.count]));
  const total = opportunity.responses.reduce((sum, r) => sum + r.count, 0);

  function adjust(responseType: string, next: number) {
    // Guarded here as well as by the disabled control: the database CHECK rejects a negative, and
    // there is no reason to make it do that work.
    if (next < 0) return;
    setCount.mutate({ oppId: opportunity.id, responseType, count: next });
  }

  return (
    <Card withBorder radius="md">
      <CardTitle
        action={
          <Text size="xs" c="dimmed">
            what this gig generated
          </Text>
        }
      >
        Responses
      </CardTitle>

      {types.length === 0 ? (
        <Text c="dimmed" size="sm">
          No response types configured.
        </Text>
      ) : (
        <>
          {types.map((type) => {
            const count = counts.get(type.short_name) ?? 0;
            return (
              <Group key={type.short_name} justify="space-between" wrap="nowrap" py={6}>
                <Text size="sm">{catalogLabel(types, type.short_name)}</Text>
                <Group gap="xs" wrap="nowrap" style={{ flexShrink: 0 }}>
                  <ActionIcon
                    variant="default"
                    size="sm"
                    aria-label={`One fewer ${type.description}`}
                    title="One fewer"
                    // Nothing below zero exists to record, so the control stops rather than
                    // sending a value the server would reject.
                    disabled={count === 0 || setCount.isPending}
                    onClick={() => adjust(type.short_name, count - 1)}
                  >
                    <IconMinus size={14} />
                  </ActionIcon>
                  <Text size="sm" fw={600} w={28} ta="center">
                    {count}
                  </Text>
                  <ActionIcon
                    variant="default"
                    size="sm"
                    aria-label={`One more ${type.description}`}
                    title="One more"
                    disabled={setCount.isPending}
                    onClick={() => adjust(type.short_name, count + 1)}
                  >
                    <IconPlus size={14} />
                  </ActionIcon>
                </Group>
              </Group>
            );
          })}

          <Divider my="xs" />
          <Group justify="space-between" wrap="nowrap">
            <Text size="sm" fw={600}>
              Total
            </Text>
            {/* Aligned with the counts above rather than the buttons, so the column reads down. */}
            <Text size="sm" fw={700} w={28} ta="center" mr={34}>
              {total}
            </Text>
          </Group>
        </>
      )}
    </Card>
  );
}