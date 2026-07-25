import { Alert, Card, Loader, NumberInput, Stack, Table, Text, Title } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useEffect, useState } from 'react';
import { useCatalogs } from '../api/catalogs';
import { useDeleteTarget, usePutTarget, useTargets, type Cadence } from '../api/targets';

const CADENCES: { value: Cadence; label: string }[] = [
  { value: 'weekly', label: 'Weekly' },
  { value: 'monthly', label: 'Monthly' },
  { value: 'quarterly', label: 'Quarterly' },
];

/** Uppercase, tracked column header — matches the app's column-heading style. */
function ThLabel({ children }: { children: string }) {
  return (
    <Text fz="xs" fw={700} tt="uppercase" c="dimmed" style={{ letterSpacing: '0.04em' }}>
      {children}
    </Text>
  );
}

/** One editable goal cell. Saves on blur: a number upserts the target, an empty value unsets it. */
function TargetCell({
  targetType,
  cadence,
  current,
}: {
  targetType: string;
  cadence: Cadence;
  current: number | undefined;
}) {
  const put = usePutTarget();
  const del = useDeleteTarget();
  const [value, setValue] = useState<number | string>(current ?? '');

  // Reflect the stored value once it (re)loads or changes after a save.
  useEffect(() => {
    setValue(current ?? '');
  }, [current]);

  function save() {
    const next = value === '' ? null : Number(value);
    if (next === null) {
      if (current !== undefined) {
        del.mutate({ target_type: targetType, cadence });
      }
    } else if (next >= 0 && next !== current) {
      put.mutate({ target_type: targetType, cadence, goal_count: next });
    }
  }

  return (
    <NumberInput
      value={value}
      onChange={setValue}
      onBlur={save}
      min={0}
      step={1}
      allowDecimal={false}
      placeholder="—"
      w={100}
      aria-label={`${targetType} ${cadence} goal`}
    />
  );
}

export function Targets() {
  const catalogs = useCatalogs();
  const targets = useTargets();

  const goalFor = (targetType: string, cadence: Cadence): number | undefined =>
    targets.data?.find((t) => t.target_type === targetType && t.cadence === cadence)?.goal_count;

  return (
    <Stack>
      <div>
        <Title order={2} c="navy.9">
          Targets
        </Title>
        <Text c="dimmed" size="sm">
          Set the goal for each target and cadence — leave a cell blank for no target. Progress is
          tracked on the Dashboard.
        </Text>
      </div>

      {(catalogs.isLoading || targets.isLoading) && <Loader size="sm" />}
      {catalogs.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={18} />}>
          Could not load target types.
        </Alert>
      )}

      {catalogs.data && targets.data && (
        <Card withBorder radius="md" padding="md" maw={560}>
          <Table verticalSpacing="sm">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>
                  <ThLabel>Target</ThLabel>
                </Table.Th>
                {CADENCES.map((c) => (
                  <Table.Th key={c.value}>
                    <ThLabel>{c.label}</ThLabel>
                  </Table.Th>
                ))}
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {catalogs.data.target_types.map((tt) => (
                <Table.Tr key={tt.short_name}>
                  <Table.Td>
                    <Text fw={600} c="navy.9" size="sm">
                      {tt.description}
                    </Text>
                  </Table.Td>
                  {CADENCES.map((c) => (
                    <Table.Td key={c.value}>
                      <TargetCell
                        targetType={tt.short_name}
                        cadence={c.value}
                        current={goalFor(tt.short_name, c.value)}
                      />
                    </Table.Td>
                  ))}
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Card>
      )}
    </Stack>
  );
}