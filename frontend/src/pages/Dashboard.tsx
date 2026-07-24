import type { ReactNode } from 'react';
import {
  Alert,
  Anchor,
  Badge,
  Box,
  Card,
  Grid,
  Group,
  Loader,
  Progress,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { IconAlertTriangle, IconCircleCheck } from '@tabler/icons-react';
import { Link } from 'react-router-dom';
import { useCatalogs } from '../api/catalogs';
import {
  useDashboard,
  type ComingUpEvent,
  type NeedsAttentionItem,
  type TargetTile as TargetTileData,
} from '../api/dashboard';
import { useAuthSession } from '../auth/session';
import { BRAND_LINE } from '../theme';

function formatMoney(amount: string, currency: string): string {
  const n = Number(amount);
  if (Number.isNaN(n)) {
    return amount;
  }
  return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(n);
}

function formatDate(iso: string | null): string {
  if (!iso) {
    return '—';
  }
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString();
}

/** Parse a bare ``YYYY-MM-DD`` as a *local* date (avoids the UTC-midnight day-shift `new Date(iso)`
 *  would cause in a negative-offset zone like Kauaʻi). */
function parseDateLocal(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d);
}

function greeting(name: string | null): string {
  const h = new Date().getHours();
  const part = h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
  return name ? `${part}, ${name}` : part;
}

/** The current week as the backend defines it — Sunday-start (matches the weekly-target window),
 *  formatted like "Week of Jul 19 – 25" (collapsing the month when the week stays within one). */
function currentWeekLabel(): string {
  const now = new Date();
  const start = new Date(now);
  start.setDate(now.getDate() - now.getDay()); // back to Sunday
  const end = new Date(start);
  end.setDate(start.getDate() + 6); // Saturday
  const startMonth = start.toLocaleDateString(undefined, { month: 'short' });
  const endLabel =
    start.getMonth() === end.getMonth()
      ? `${end.getDate()}`
      : `${end.toLocaleDateString(undefined, { month: 'short' })} ${end.getDate()}`;
  return `Week of ${startMonth} ${start.getDate()} – ${endLabel}`;
}

/** The viewer's short timezone name (e.g. "HST") — the same zone the API buckets metrics in. */
function timezoneAbbrev(): string {
  const parts = new Intl.DateTimeFormat('en-US', { timeZoneName: 'short' }).formatToParts(new Date());
  return parts.find((p) => p.type === 'timeZoneName')?.value ?? '';
}

/** Cadence → the period word folded into the tile value ("… this month"). */
const PERIOD_WORD: Record<string, string> = {
  weekly: 'week',
  monthly: 'month',
  quarterly: 'quarter',
};

const REASON: Record<NeedsAttentionItem['reason'], { label: string; color: string }> = {
  awaiting_payment: { label: 'Awaiting payment', color: 'warn' },
  overdue_unbooked: { label: 'Overdue', color: 'terracotta' },
  research_incomplete: { label: 'Research incomplete', color: 'gray' },
};

/** Needs-attention rows link to the gig, except research rows which link to the venue. */
function needsAttentionHref(n: NeedsAttentionItem): string {
  return n.reason === 'research_incomplete' ? `/venues/${n.id}` : `/pipeline/${n.id}`;
}

/** Funnel bar opacity per stage — fades as reach narrows (mockup `.fstep` opacities). */
const FUNNEL_OPACITY = [0.92, 0.75, 0.58, 0.42, 0.34];

/** A card with the approved hairline separating its title from its body. */
function DashCard({ title, children, h }: { title: string; children: ReactNode; h?: string }) {
  return (
    <Card withBorder radius="md" padding={0} h={h} style={{ borderColor: BRAND_LINE }}>
      <Box px="md" py="sm" style={{ borderBottom: `1px solid ${BRAND_LINE}` }}>
        <Text fw={650} fz={13}>
          {title}
        </Text>
      </Box>
      <Box p="md">{children}</Box>
    </Card>
  );
}

/** Top border on every row after the first (mockup row dividers). */
function rowDivider(i: number): React.CSSProperties | undefined {
  return i > 0 ? { borderTop: `1px solid ${BRAND_LINE}` } : undefined;
}

function TargetTile({ tile, label }: { tile: TargetTileData; label: string }) {
  const pct = tile.goal > 0 ? Math.min(100, Math.round((tile.actual / tile.goal) * 100)) : 0;
  const met = tile.goal > 0 && tile.actual >= tile.goal;
  const period = PERIOD_WORD[tile.cadence] ?? tile.cadence;
  return (
    <Card withBorder radius="md" padding="md" style={{ borderColor: BRAND_LINE }}>
      <Text tt="uppercase" fw={700} c="navy.6" style={{ fontSize: 11, letterSpacing: '0.05em' }}>
        {label}
      </Text>
      <Group align="baseline" gap={6} mt="xs">
        <Text fz="1.9rem" fw={700} c="navy.9" lh={1}>
          {tile.actual}
        </Text>
        <Text c="dimmed">
          / {tile.goal} this {period}
        </Text>
      </Group>
      <Progress value={pct} color={met ? 'good' : 'terracotta'} radius="xl" mt="sm" />
      <Group gap={4} mt={6}>
        {met && <IconCircleCheck size={14} color="var(--mantine-color-good-6)" />}
        <Text size="xs" c={met ? 'good.7' : 'dimmed'}>
          {met ? 'Goal met' : `${pct}% of goal`}
        </Text>
      </Group>
    </Card>
  );
}

/** One money-card figure with its supporting gig sub-count. */
function MoneyStat({
  label,
  value,
  sub,
  color,
}: {
  label: string;
  value: string;
  sub: string;
  color?: string;
}) {
  return (
    <div>
      <Text size="xs" c="dimmed">
        {label}
      </Text>
      <Text fw={700} c={color ?? 'navy.9'}>
        {value}
      </Text>
      <Text size="xs" c="dimmed">
        {sub}
      </Text>
    </div>
  );
}

function ComingUpRow({ event, style }: { event: ComingUpEvent; style?: React.CSSProperties }) {
  const d = parseDateLocal(event.event_date);
  const month = d.toLocaleDateString(undefined, { month: 'short' });
  return (
    <Group gap="sm" wrap="nowrap" align="center" py="xs" style={style}>
      <Card withBorder radius="sm" padding={4} w={44} ta="center" style={{ flexShrink: 0 }}>
        <Text size="9px" tt="uppercase" c="dimmed" fw={700} lh={1.2}>
          {month}
        </Text>
        <Text fw={700} c="navy.9" lh={1.1}>
          {d.getDate()}
        </Text>
      </Card>
      <div style={{ minWidth: 0 }}>
        <Anchor component={Link} to={`/pipeline/${event.id}`} size="sm" lineClamp={1}>
          {event.title}
        </Anchor>
        <Text size="xs" c="dimmed" lineClamp={1}>
          {event.organization_name}
        </Text>
      </div>
    </Group>
  );
}

export function Dashboard() {
  const catalogs = useCatalogs();
  const dash = useDashboard();
  const { user } = useAuthSession();

  const label = (list: { short_name: string; description: string }[] | undefined, short: string) =>
    list?.find((i) => i.short_name === short)?.description ?? short;

  if (dash.isPending || catalogs.isPending) {
    return (
      <Group>
        <Loader size="sm" />
        <Text c="dimmed">Loading dashboard…</Text>
      </Group>
    );
  }
  if (dash.isError) {
    return (
      <Alert color="red" icon={<IconAlertTriangle size={18} />}>
        {dash.error.message}
      </Alert>
    );
  }

  const d = dash.data;
  const funnelMax = Math.max(1, ...d.funnel.map((f) => f.count));
  const money = d.money;

  return (
    <Stack>
      <div>
        <Title order={2} c="navy.9">
          {greeting(user?.name ?? null)}
        </Title>
        <Text c="dimmed" size="sm">
          {currentWeekLabel()} · {timezoneAbbrev()}
        </Text>
      </div>

      {/* Actual-vs-target tiles */}
      {d.targets.length === 0 ? (
        <Text c="dimmed" size="sm">
          No targets set.{' '}
          <Anchor component={Link} to="/targets">
            Set targets
          </Anchor>{' '}
          to track progress here.
        </Text>
      ) : (
        <SimpleGrid cols={{ base: 1, xs: 2, md: 4 }}>
          {d.targets.map((t) => (
            <TargetTile
              key={`${t.target_type}:${t.cadence}`}
              tile={t}
              label={label(catalogs.data?.target_types, t.target_type)}
            />
          ))}
        </SimpleGrid>
      )}

      {/* Two column stacks — align at the top like the approved layout (no row stagger). */}
      <Grid align="stretch">
        <Grid.Col span={{ base: 12, md: 7 }}>
          <Stack>
            {/* Funnel */}
            <DashCard title="Pipeline funnel">
              <Stack gap="sm">
                {d.funnel.map((f, i) => {
                  const prev = i > 0 ? d.funnel[i - 1].count : null;
                  const pct = prev && prev > 0 ? Math.round((f.count / prev) * 100) : null;
                  return (
                    <Group key={f.status} gap="sm" wrap="nowrap">
                      <Text size="sm" w={140} style={{ flexShrink: 0 }}>
                        {label(catalogs.data?.opportunity_statuses, f.status)}
                      </Text>
                      <div
                        style={{
                          flex: 1,
                          height: 20,
                          background: 'var(--mantine-color-gray-1)',
                          borderRadius: 8,
                        }}
                      >
                        <div
                          style={{
                            width: `${(f.count / funnelMax) * 100}%`,
                            minWidth: f.count > 0 ? 4 : 0,
                            height: '100%',
                            background: 'var(--mantine-color-terracotta-6)',
                            opacity: FUNNEL_OPACITY[i] ?? 0.34,
                            borderRadius: 8,
                          }}
                        />
                      </div>
                      <Text size="sm" fw={600} w={64} ta="right" style={{ flexShrink: 0 }}>
                        {f.count}
                        {pct !== null && (
                          <Text span size="xs" c="dimmed" fw={400}>
                            {' · '}
                            {pct}%
                          </Text>
                        )}
                      </Text>
                    </Group>
                  );
                })}
              </Stack>
            </DashCard>

            {/* Revenue & payments */}
            <DashCard title="Revenue & payments">
              <SimpleGrid cols={{ base: 2, sm: 4 }}>
                <MoneyStat
                  label="Booked"
                  value={formatMoney(money.booked, money.currency)}
                  sub={`${money.booked_count} paid ${money.booked_count === 1 ? 'gig' : 'gigs'}`}
                />
                <MoneyStat
                  label="Received"
                  value={formatMoney(money.received, money.currency)}
                  sub={`${money.received_count} collected`}
                  color="good.7"
                />
                <MoneyStat
                  label="Outstanding"
                  value={formatMoney(money.outstanding, money.currency)}
                  sub={`${money.invoiced_count} invoiced`}
                  color="warn.7"
                />
                <MoneyStat
                  label="Pro bono"
                  value={String(money.pro_bono_count)}
                  sub="visibility gigs"
                />
              </SimpleGrid>
            </DashCard>

            {/* Stale — a beyond-mockup addition; kept, fit to the left column. */}
            <DashCard title="Stale — needs a nudge">
              {d.stale.length === 0 ? (
                <Text c="dimmed" size="sm">
                  Nothing stale — every active gig has recent activity.
                </Text>
              ) : (
                <Stack gap={0}>
                  {d.stale.map((s, i) => (
                    <Group
                      key={s.id}
                      justify="space-between"
                      wrap="nowrap"
                      py="xs"
                      style={rowDivider(i)}
                    >
                      <Anchor component={Link} to={`/pipeline/${s.id}`} size="sm" lineClamp={1}>
                        {s.title}
                      </Anchor>
                      <Text size="xs" c="dimmed" style={{ flexShrink: 0 }}>
                        {s.organization_name} · {formatDate(s.last_activity_at)}
                      </Text>
                    </Group>
                  ))}
                </Stack>
              )}
            </DashCard>
          </Stack>
        </Grid.Col>

        <Grid.Col span={{ base: 12, md: 5 }}>
          <Stack>
            {/* Needs attention */}
            <DashCard title="Needs attention">
              {d.needs_attention.length === 0 ? (
                <Text c="dimmed" size="sm">
                  All clear — nothing awaiting payment or overdue.
                </Text>
              ) : (
                <Stack gap={0}>
                  {d.needs_attention.map((n, i) => (
                    <Group
                      key={`${n.reason}:${n.id}`}
                      justify="space-between"
                      wrap="nowrap"
                      py="xs"
                      style={rowDivider(i)}
                    >
                      <Anchor component={Link} to={needsAttentionHref(n)} size="sm" lineClamp={1}>
                        {n.title}
                      </Anchor>
                      <Badge
                        color={REASON[n.reason].color}
                        variant="light"
                        style={{ flexShrink: 0 }}
                      >
                        {REASON[n.reason].label}
                      </Badge>
                    </Group>
                  ))}
                </Stack>
              )}
            </DashCard>

            {/* Coming up */}
            <DashCard title="Coming up">
              {d.coming_up.length === 0 ? (
                <Text c="dimmed" size="sm">
                  Nothing scheduled — no gigs with an upcoming date.
                </Text>
              ) : (
                <Stack gap={0}>
                  {d.coming_up.map((e, i) => (
                    <ComingUpRow key={e.id} event={e} style={rowDivider(i)} />
                  ))}
                </Stack>
              )}
            </DashCard>
          </Stack>
        </Grid.Col>
      </Grid>
    </Stack>
  );
}