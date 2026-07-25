import { Alert, Anchor, Badge, Button, Group, Loader, Stack, Table, Text, Title } from '@mantine/core';
import { IconAlertTriangle, IconDownload } from '@tabler/icons-react';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useCatalogs } from '../api/catalogs';
import { useOpportunities, type OpportunitySummary } from '../api/opportunities';
import { FilterBar, type FilterPill } from '../components/FilterBar';
import { formatMoney, paymentColor } from '../opportunityChips';

type Catalog = { short_name: string; description: string }[] | undefined;
const label = (list: Catalog, sn: string) => list?.find((c) => c.short_name === sn)?.description ?? sn;

function outcomeColor(status: string): string {
  if (status === 'delivered') return 'good';
  if (status === 'cancelled') return 'red';
  return 'gray'; // lost / other
}
function compColor(compType: string): string {
  if (compType === 'paid') return 'good';
  if (compType === 'pro_bono') return 'gold';
  return 'gray'; // trade
}
function eventDate(o: OpportunitySummary): string {
  const raw = o.event_date ?? o.closed_at;
  if (!raw) return '—';
  const d = new Date(raw);
  return Number.isNaN(d.getTime())
    ? raw
    : d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}
function eventYear(o: OpportunitySummary): number | null {
  const raw = o.event_date ?? o.closed_at;
  if (!raw) return null;
  const d = new Date(raw);
  return Number.isNaN(d.getTime()) ? null : d.getFullYear();
}
function csvCell(v: string): string {
  return `"${v.replace(/"/g, '""')}"`;
}

export function History() {
  const history = useOpportunities(true);
  const catalogs = useCatalogs();
  const navigate = useNavigate();
  const [search, setSearch] = useState('');
  const [outcome, setOutcome] = useState('all');
  const [comp, setComp] = useState<string | null>(null);
  const [year, setYear] = useState<number | null>(null);

  const settled = (sn: string) =>
    (catalogs.data?.payment_statuses ?? []).find((p) => p.short_name === sn)?.is_settled ?? false;

  const all = history.data ?? [];
  const delivered = all.filter((o) => o.current_status === 'delivered').length;
  const cancelled = all.filter((o) => o.current_status === 'cancelled').length;
  const lost = all.filter((o) => o.current_status === 'lost').length;
  const proBono = all.filter((o) => o.comp_type === 'pro_bono').length;
  const collected = all
    .filter((o) => settled(o.payment_status))
    .reduce((sum, o) => sum + Number(o.fee_amount ?? 0), 0);
  const years = [...new Set(all.map(eventYear).filter((y): y is number => y != null))].sort(
    (a, b) => b - a,
  );

  const pills: FilterPill[] = [
    { value: 'all', label: 'All outcomes', active: outcome === 'all' },
    { value: 'delivered', label: 'Delivered', active: outcome === 'delivered' },
    { value: 'cancelled', label: 'Cancelled', active: outcome === 'cancelled' },
    { value: 'lost', label: 'Lost', active: outcome === 'lost' },
    { value: 'paid', label: 'Paid', active: comp === 'paid' },
    { value: 'pro_bono', label: 'Pro bono', active: comp === 'pro_bono' },
    ...years.map((y) => ({ value: `y${y}`, label: String(y), active: year === y })),
  ];
  function handlePill(value: string) {
    if (value === 'paid' || value === 'pro_bono') setComp((c) => (c === value ? null : value));
    else if (value.startsWith('y')) {
      const y = Number(value.slice(1));
      setYear((cur) => (cur === y ? null : y));
    } else setOutcome(value);
  }

  const term = search.trim().toLowerCase();
  const filtered = all.filter(
    (o) =>
      (outcome === 'all' || o.current_status === outcome) &&
      (!comp || o.comp_type === comp) &&
      (year == null || eventYear(o) === year) &&
      (!term ||
        o.title.toLowerCase().includes(term) ||
        (o.talk_title ?? '').toLowerCase().includes(term) ||
        o.organization_name.toLowerCase().includes(term)),
  );

  function exportCsv() {
    const header = ['Gig', 'Venue', 'Talk', 'Format', 'Outcome', 'Event date', 'Comp', 'Fee', 'Payment'];
    const rows = filtered.map((o) => [
      o.title,
      o.organization_name,
      o.talk_title ?? '',
      label(catalogs.data?.opportunity_formats, o.opportunity_format),
      label(catalogs.data?.opportunity_statuses, o.current_status),
      o.event_date ?? '',
      label(catalogs.data?.comp_types, o.comp_type),
      o.fee_amount ?? '',
      label(catalogs.data?.payment_statuses, o.payment_status),
    ]);
    const csv = [header, ...rows].map((r) => r.map((c) => csvCell(String(c))).join(',')).join('\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8;' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = 'history.csv';
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <Stack>
      <Group justify="space-between" align="flex-start">
        <div>
          <Title order={2} c="navy.9">
            History
          </Title>
          <Text c="dimmed" size="sm">
            {all.length} closed {all.length === 1 ? 'gig' : 'gigs'} · {delivered} delivered ·{' '}
            {cancelled} cancelled · {lost} lost · {formatMoney(String(collected), 'USD')} collected ·{' '}
            {proBono} pro bono
          </Text>
        </div>
        {all.length > 0 && (
          <Button variant="default" leftSection={<IconDownload size={16} />} onClick={exportCsv}>
            Export CSV
          </Button>
        )}
      </Group>

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

      {all.length > 0 && (
        <>
          <FilterBar
            search={search}
            onSearch={setSearch}
            searchPlaceholder="Search closed gigs…"
            pills={pills}
            onPillClick={handlePill}
          />
          {filtered.length === 0 ? (
            <Text c="dimmed">No closed gigs match these filters.</Text>
          ) : (
            <Table highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Gig</Table.Th>
                  <Table.Th>Outcome</Table.Th>
                  <Table.Th>Date</Table.Th>
                  <Table.Th>Format</Table.Th>
                  <Table.Th>Comp</Table.Th>
                  <Table.Th>Amount</Table.Th>
                  <Table.Th>Payment</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {filtered.map((o) => {
                  const talkLine = [
                    o.talk_title,
                    label(catalogs.data?.opportunity_formats, o.opportunity_format),
                  ]
                    .filter(Boolean)
                    .join(' · ');
                  return (
                    <Table.Tr
                      key={o.id}
                      style={{ cursor: 'pointer' }}
                      onClick={() => navigate(`/pipeline/${o.id}`)}
                    >
                      <Table.Td>
                        <Anchor
                          component={Link}
                          to={`/pipeline/${o.id}`}
                          fw={600}
                          onClick={(e) => e.stopPropagation()}
                        >
                          {o.title}
                        </Anchor>
                        {talkLine && (
                          <Text size="xs" c="dimmed">
                            {talkLine}
                          </Text>
                        )}
                      </Table.Td>
                      <Table.Td>
                        <Badge color={outcomeColor(o.current_status)} variant="light">
                          {label(catalogs.data?.opportunity_statuses, o.current_status)}
                        </Badge>
                      </Table.Td>
                      <Table.Td>{eventDate(o)}</Table.Td>
                      <Table.Td>
                        {label(catalogs.data?.opportunity_formats, o.opportunity_format)}
                      </Table.Td>
                      <Table.Td>
                        <Badge color={compColor(o.comp_type)} variant="light">
                          {label(catalogs.data?.comp_types, o.comp_type)}
                        </Badge>
                      </Table.Td>
                      <Table.Td>{formatMoney(o.fee_amount, o.currency) ?? '—'}</Table.Td>
                      <Table.Td>
                        {o.payment_status === 'n_a' ? (
                          '—'
                        ) : (
                          <Badge
                            color={paymentColor(o.payment_status, settled(o.payment_status))}
                            variant="light"
                          >
                            {label(catalogs.data?.payment_statuses, o.payment_status)}
                          </Badge>
                        )}
                      </Table.Td>
                    </Table.Tr>
                  );
                })}
              </Table.Tbody>
            </Table>
          )}
        </>
      )}
    </Stack>
  );
}
