import { Alert, Card, Group, Loader, SimpleGrid, Stack, Text, Title } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useCatalogs } from '../api/catalogs';

/**
 * Slice-1 placeholder dashboard. Its real job is to exercise the same-origin API path end to end:
 * it loads `/catalogs` and renders one tile per vocabulary. Real dashboard widgets (targets vs
 * actuals, funnel, money rollup) arrive in a later slice.
 */
export function Dashboard() {
  const catalogs = useCatalogs();

  return (
    <Stack>
      <Title order={2} c="navy.9">
        Dashboard
      </Title>
      <Text c="dimmed" size="sm">
        Slice 1 shell — proves the same-origin API path by loading the catalog vocabularies.
      </Text>

      {catalogs.isLoading && (
        <Group>
          <Loader size="sm" />
          <Text>Loading catalogs…</Text>
        </Group>
      )}

      {catalogs.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={18} />} title="Failed to load catalogs">
          {catalogs.error.message}
        </Alert>
      )}

      {catalogs.data && (
        <SimpleGrid cols={{ base: 2, sm: 3, md: 4 }}>
          {Object.entries(catalogs.data).map(([name, items]) => (
            <Card key={name} withBorder padding="sm" radius="md">
              <Text fw={600} size="sm">
                {name}
              </Text>
              <Text size="xl" c="terracotta.6">
                {items.length}
              </Text>
              <Text size="xs" c="dimmed">
                entries
              </Text>
            </Card>
          ))}
        </SimpleGrid>
      )}
    </Stack>
  );
}