import { Alert, Anchor, Badge, Group, Loader, Stack, Table, Text, Title } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { Link } from 'react-router-dom';
import { useCatalogs } from '../api/catalogs';
import { useOpportunities } from '../api/opportunities';

function formatMoney(fee: string | null, currency: string): string {
  if (!fee) return '—';
  const amount = Number(fee);
  if (Number.isNaN(amount)) return `${currency} ${fee}`;
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(amount);
  } catch {
    return `${currency} ${amount.toFixed(2)}`;
  }
}

function formatDate(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString();
}

export function History() {
  const history = useOpportunities(true);
  const catalogs = useCatalogs();

  const label = (list: { short_name: string; description: string }[] | undefined, sn: string) =>
    list?.find((c) => c.short_name === sn)?.description ?? sn;

  return (
    <Stack>
      <Title order={2} c="navy.9">
        History
      </Title>

      {history.isLoading && (
        <Group>
          <Loader size="sm" />
          <Text>Loading history…</Text>
        </Group>
      )}
      {history.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={18} />}>
          {history.error.message}
        </Alert>
      )}

      {history.data?.length === 0 && (
        <Text c="dimmed">Nothing here yet — delivered-and-paid, cancelled, and lost gigs land here.</Text>
      )}

      {history.data && history.data.length > 0 && (
        <Table highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Title</Table.Th>
              <Table.Th>Venue</Table.Th>
              <Table.Th>Outcome</Table.Th>
              <Table.Th>Fee</Table.Th>
              <Table.Th>Payment</Table.Th>
              <Table.Th>Closed</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {history.data.map((opp) => (
              <Table.Tr key={opp.id}>
                <Table.Td>
                  <Anchor component={Link} to={`/pipeline/${opp.id}`}>
                    {opp.title}
                  </Anchor>
                </Table.Td>
                <Table.Td>{opp.organization_name}</Table.Td>
                <Table.Td>
                  <Badge variant="light" color="gray">
                    {label(catalogs.data?.opportunity_statuses, opp.current_status)}
                  </Badge>
                </Table.Td>
                <Table.Td>{formatMoney(opp.fee_amount, opp.currency)}</Table.Td>
                <Table.Td>{label(catalogs.data?.payment_statuses, opp.payment_status)}</Table.Td>
                <Table.Td>{formatDate(opp.closed_at)}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  );
}