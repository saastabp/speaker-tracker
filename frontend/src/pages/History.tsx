import { Alert, Anchor, Badge, Button, Group, Loader, Stack, Table, Text, Title } from '@mantine/core';
import { IconAlertTriangle, IconDownload } from '@tabler/icons-react';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { catalogLabel, useCatalogs } from '../api/catalogs';
import { useOpportunities, type OpportunitySummary } from '../api/opportunities';
import { FilterBar, type FilterPill } from '../components/FilterBar';
import { longDate, parseDateLocal, parseTimestamp } from '../dates';
import { formatMoney } from '../format';
import { paymentColor } from '../opportunityChips';

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
/**
 * The day a record belongs to: its event date, else the day it closed.
 *
 * The two fields need different parsing and that is the whole point of this helper. `event_date`
 * is a bare `YYYY-MM-DD`, which `new Date()` reads as UTC midnight — in Hawaiʻi that renders the
 * *previous* day and files a Jan 1 gig under the previous year, breaking the year pills.
 * `closed_at` is a real timestamp, where `new Date()` is correct.
 */
function eventDay(o: OpportunitySummary): Date | null {
  if (o.event_date) return parseDateLocal(o.event_date);
  return o.closed_at ? parseTimestamp(o.closed_at) : null;
}
function eventDate(o: OpportunitySummary): string {
  const day = eventDay(o);
  // Unparseable is shown as-is rather than as an em dash: a malformed value should look wrong.
  return day ? longDate(day) : (o.event_date ?? o.closed_at ?? '—');
}
function eventYear(o: OpportunitySummary): number | null {
  return eventDay(o)?.getFullYear() ?? null;
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
      catalogLabel(catalogs.data?.opportunity_formats, o.opportunity_format),
      catalogLabel(catalogs.data?.opportunity_statuses, o.current_status),
      o.event_date ?? '',
      catalogLabel(catalogs.data?.comp_types, o.comp_type),
      o.fee_amount ?? '',
      catalogLabel(catalogs.data?.payment_statuses, o.payment_status),
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
                    catalogLabel(catalogs.data?.opportunity_formats, o.opportunity_format),
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
                          {catalogLabel(catalogs.data?.opportunity_statuses, o.current_status)}
                        </Badge>
                      </Table.Td>
                      <Table.Td>{eventDate(o)}</Table.Td>
                      <Table.Td>
                        {catalogLabel(catalogs.data?.opportunity_formats, o.opportunity_format)}
                      </Table.Td>
                      <Table.Td>
                        <Badge color={compColor(o.comp_type)} variant="light">
                          {catalogLabel(catalogs.data?.comp_types, o.comp_type)}
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
                            {catalogLabel(catalogs.data?.payment_statuses, o.payment_status)}
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
