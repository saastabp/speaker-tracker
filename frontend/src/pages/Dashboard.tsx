import { useEffect, type ReactNode } from 'react';
import {
  Alert,
  Anchor,
  Badge,
  Box,
  Button,
  Card,
  Grid,
  Group,
  Loader,
  Progress,
  SimpleGrid,
  ActionIcon,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import {
  IconAlertTriangle,
  IconChevronLeft,
  IconChevronRight,
  IconCircleCheck,
} from '@tabler/icons-react';
import { Link } from 'react-router-dom';
import { catalogLabel, useCatalogs } from '../api/catalogs';
import {
  useDashboard,
  type ComingUpEvent,
  type NeedsAttentionItem,
  type TargetTile as TargetTileData,
  type Week as WeekData,
} from '../api/dashboard';
import { usePatchFollowUp, type FollowUp as FollowUpItem } from '../api/followUps';
import { useAuthSession } from '../auth/session';
import {
  addDays,
  clockTime,
  daysSince,
  isOverdue,
  isoDate,
  longDate,
  parseDateLocal,
  shortDate,
  startOfToday,
} from '../dates';
import { formatMoney } from '../format';
import { useFilterParams } from '../urlFilters';
import { BRAND_LINE } from '../theme';

function greeting(name: string | null): string {
  const h = new Date().getHours();
  const part = h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
  return name ? `${part}, ${name}` : part;
}

/** "Week of Jul 19 – 25", collapsing the month when the week stays inside one.
 *  Takes the server's bounds rather than computing the week: `core/periods.py` owns where a week
 *  starts, and this having its own opinion was how the two could drift. */
function weekLabel(startIso: string): string {
  const start = parseDateLocal(startIso);
  const end = addDays(start, 6); // Saturday — the payload's `end` is exclusive
  const sameMonth = start.getMonth() === end.getMonth();
  return `Week of ${shortDate(start)} – ${sameMonth ? end.getDate() : shortDate(end)}`;
}

/** Whether a `[start, end)` window contains today. String compare is safe on `YYYY-MM-DD`. */
function contains(startIso: string, endIso: string, day: string): boolean {
  return startIso <= day && day < endIso;
}

function WeekNavigator({ week, onChange }: { week: WeekData; onChange: (weekOf: string) => void }) {
  const start = parseDateLocal(week.start);
  const isCurrent = contains(week.start, week.end, isoDate(startOfToday()));
  return (
    <Group gap={4}>
      <ActionIcon
        variant="subtle"
        c="navy.7"
        aria-label="Previous week"
        onClick={() => onChange(isoDate(addDays(start, -7)))}
      >
        <IconChevronLeft size={16} />
      </ActionIcon>
      <Text fw={650} fz={13} ta="center" miw={150}>
        {weekLabel(week.start)}
      </Text>
      <ActionIcon
        variant="subtle"
        c="navy.7"
        aria-label="Next week"
        onClick={() => onChange(isoDate(addDays(start, 7)))}
      >
        <IconChevronRight size={16} />
      </ActionIcon>
      {/* Only offered when it would do something — and it clears the key rather than writing
          today's date, so the default view keeps a clean URL. */}
      {!isCurrent && (
        <Button variant="subtle" size="compact-xs" onClick={() => onChange('')}>
          This week
        </Button>
      )}
    </Group>
  );
}

/** The viewer's short timezone name (e.g. "HST") — the same zone the API buckets metrics in. */
function timezoneAbbrev(): string {
  const parts = new Intl.DateTimeFormat('en-US', { timeZoneName: 'short' }).formatToParts(new Date());
  return parts.find((p) => p.type === 'timeZoneName')?.value ?? '';
}

/** sessionStorage key holding the week the Dashboard was last showing.
 *
 *  Session-scoped rather than persisted: opening the app tomorrow should land on this week, but
 *  stepping out to a drill-down list and back should not silently reset what you were reading.
 *  The URL still wins when it carries a week, so a shared or reloaded link is unaffected. */
const WEEK_KEY = 'dashboard.weekOf';

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
  awaiting_reply: { label: 'Awaiting reply', color: 'blue' },
  stale: { label: 'Gone quiet', color: 'gray' },
};

/**
 * Where a needs-attention row goes. The `reason` is what says which id-space `id` is in, so this
 * switch has to grow whenever a reason does — a missing case would link a thread id into /pipeline
 * and land on someone else's gig, or a 404.
 */
function needsAttentionHref(n: NeedsAttentionItem): string {
  switch (n.reason) {
    case 'research_incomplete':
      return `/venues/${n.id}`;
    case 'awaiting_reply':
      return `/emails/${n.id}`;
    default:
      return `/pipeline/${n.id}`;
  }
}

// Where each aggregate opens. Kept together so the link and the SQL it has to agree with can be
// audited side by side — the whole point of the drill-down is that the list you land on is the
// same size as the number you clicked.
//
// **`closed=all` on every gig link is load-bearing.** None of these aggregates stop at the open
// board: `funnel_counts` counts a gig by the furthest stage it ever reached, and `money_rollup`
// counts a delivered-and-paid gig that has already closed. Without it each list comes up short.

/** Money figures → the gigs behind them, mirroring `repositories.dashboard.money_rollup`. */
const MONEY_LINKS = {
  // `st.short_name IN ('booked','delivered')` — where the gig sits *now*, not how far it got.
  booked: '/pipeline?comp=paid&status=booked,delivered&closed=all',
  received: '/pipeline?pay=received&closed=all',
  outstanding: '/pipeline?pay=outstanding&closed=all',
  proBono: '/pipeline?comp=pro_bono&status=booked,delivered&closed=all',
} as const;

/**
 * Target tiles → the records counted toward them, using the tile's own window.
 *
 * The tile already knows the period its number was counted over — the server sends it — so the
 * link just passes it through as the list's date range. That is the same shape a date-range picker
 * would produce later; nothing here waits on windowed metrics.
 *
 * `outreaches` returns undefined and is **deliberately not linked**: it counts logged touches, and
 * there is no outreaches list page to open. That is a missing destination, not a missing filter,
 * and pointing it at Emails would be wrong — a touch can be a DM or a call, and Emails lists
 * threads rather than touches.
 */
function targetHref(tile: TargetTileData): string | undefined {
  // Windowed since slice 10's follow-up: the tile counts venues that crossed the research-ready
  // bar inside its period, so the link has to ask for that period too — a current-state
  // `?ready=1` list would no longer be the number it sits under.
  if (tile.target_type === 'venues_researched') {
    if (!tile.period_start || !tile.period_end) {
      return undefined;
    }
    return `/venues?ready_from=${tile.period_start}&ready_to=${tile.period_end}`;
  }
  const entered = { pitches: 'pitched', bookings: 'booked' }[tile.target_type];
  // No window, no link. Against a backend that predates these fields they are undefined, and
  // interpolating that gives `entered_from=undefined`, which the API rejects as a malformed date —
  // so the tile would look clickable and 400 on arrival. Better not to offer the link.
  if (!entered || !tile.period_start || !tile.period_end) {
    return undefined;
  }
  return (
    `/pipeline?entered=${entered}` +
    `&entered_from=${tile.period_start}&entered_to=${tile.period_end}&closed=all`
  );
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
  // "this week" stops being true the moment the navigator moves off the current one.
  const current = contains(tile.period_start, tile.period_end, isoDate(startOfToday()));
  const href = targetHref(tile);
  const card = (
    <Card withBorder radius="md" padding="md" h="100%" style={{ borderColor: BRAND_LINE }}>
      <Text tt="uppercase" fw={700} c="navy.6" style={{ fontSize: 11, letterSpacing: '0.05em' }}>
        {label}
      </Text>
      <Group align="baseline" gap={6} mt="xs">
        <Text fz="1.9rem" fw={700} c="navy.9" lh={1}>
          {tile.actual}
        </Text>
        <Text c="dimmed">
          / {tile.goal} {current ? 'this' : 'that'} {period}
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
  // Tiles whose actual cannot be reproduced by a filter stay unlinked — see TARGET_LINKS.
  if (!href) {
    return card;
  }
  return (
    <Anchor component={Link} to={href} underline="never" c="inherit" display="block" h="100%">
      {card}
    </Anchor>
  );
}

/** One money-card figure with its supporting gig sub-count, opening the gigs behind it. */
function MoneyStat({
  label,
  value,
  sub,
  color,
  href,
}: {
  label: string;
  value: string;
  sub: string;
  color?: string;
  href?: string;
}) {
  const body = (
    <>
      <Text size="xs" c="dimmed">
        {label}
      </Text>
      <Text fw={700} c={color ?? 'navy.9'}>
        {value}
      </Text>
      <Text size="xs" c="dimmed">
        {sub}
      </Text>
    </>
  );
  // Figures with no filter that reproduces them stay plain text rather than linking somewhere
  // approximate — a number that opens a list of a different size is worse than one that does not
  // open at all.
  if (!href) {
    return <div>{body}</div>;
  }
  return (
    <Anchor component={Link} to={href} underline="never" c="inherit">
      {body}
    </Anchor>
  );
}

/** One row of "Coming up" — a gig or an appointment, told apart by `item_type`.
 *
 * A gig links to its own detail page. An appointment has none, so it links to the Appointments
 * page, which is where it can be edited — a link that lands somewhere you can act is worth more
 * than no link at all.
 */
function ComingUpRow({ event, style }: { event: ComingUpEvent; style?: React.CSSProperties }) {
  const d = parseDateLocal(event.event_date);
  const month = d.toLocaleDateString(undefined, { month: 'short' });
  const appointment = event.item_type === 'appointment';
  const to = appointment ? '/appointments' : `/pipeline/${event.id}`;
  // Who it is with (or where it is), then the time when there is one. A gig's event_date carries
  // no hour, so the second half is simply absent for gigs.
  const subtitle = [
    appointment ? event.contact_name : event.organization_name,
    event.event_time ? clockTime(event.event_time) : null,
  ]
    .filter(Boolean)
    .join(' · ');
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
        <Anchor component={Link} to={to} size="sm" lineClamp={1}>
          {event.title}
        </Anchor>
        <Text size="xs" c="dimmed" lineClamp={1}>
          {subtitle}
        </Text>
      </div>
    </Group>
  );
}

/** One due reminder, with the mark-done control that removes it from this card.
 *
 * Marking done is the whole point of the row: it is what cancels the emailed reminder, so a
 * follow-up Donna has already dealt with stops nagging her. The button is disabled while the
 * mutation is in flight rather than optimistically hidden — the row disappearing before the
 * schedule was actually cancelled would be the one misleading state here.
 */
function FollowUpDueRow({
  followUp,
  style,
}: {
  followUp: FollowUpItem;
  style?: React.CSSProperties;
}) {
  const patch = usePatchFollowUp();
  const overdue = isOverdue(followUp.due_date);
  const target = followUp.opportunity_id
    ? `/pipeline/${followUp.opportunity_id}`
    : followUp.contact_id
      ? `/contacts/${followUp.contact_id}`
      : null;
  const label = followUp.contact_name ?? followUp.opportunity_title ?? 'Follow-up';

  return (
    <Group justify="space-between" wrap="nowrap" align="flex-start" py="xs" style={style}>
      <div style={{ minWidth: 0 }}>
        <Group gap="xs" wrap="nowrap">
          {target ? (
            <Anchor component={Link} to={target} size="sm" lineClamp={1}>
              {label}
            </Anchor>
          ) : (
            <Text size="sm" lineClamp={1}>
              {label}
            </Text>
          )}
          {overdue && (
            <Badge color="warn" variant="light" size="xs" style={{ flexShrink: 0 }}>
              overdue
            </Badge>
          )}
          {/* Filled, not light: this is the one state where the app failed the user rather than
              merely reporting a date, and it should not read as another neutral chip. */}
          {followUp.reminder_failed_at && (
            <Badge color="terracotta" variant="filled" size="xs" style={{ flexShrink: 0 }}>
              reminder didn't send
            </Badge>
          )}
        </Group>
        <Text size="xs" c="dimmed" lineClamp={2}>
          {followUp.note}
        </Text>
      </div>
      <Button
        size="compact-xs"
        variant="subtle"
        loading={patch.isPending}
        onClick={() => patch.mutate({ id: followUp.id, completed: true })}
        style={{ flexShrink: 0 }}
      >
        Done
      </Button>
    </Group>
  );
}

export function Dashboard() {
  const catalogs = useCatalogs();
  const filters = useFilterParams();
  // Absent means the current week — the hook then omits the param entirely, so the default view
  // both keeps a clean URL and shares a cache entry with every other page that invalidates it.
  const weekOf = filters.get('week_of');
  const dash = useDashboard(weekOf || undefined);
  const { user } = useAuthSession();

  // Mount-only: restore the week this session was last showing when arriving without one, and
  // remember one that arrived by link. Deliberately not re-run — after mount the navigator owns
  // the value, and re-running would fight the user the moment they cleared it.
  useEffect(() => {
    if (weekOf) {
      sessionStorage.setItem(WEEK_KEY, weekOf);
    } else {
      const remembered = sessionStorage.getItem(WEEK_KEY);
      if (remembered) {
        filters.set('week_of', remembered);
      }
    }
  }, []);

  function chooseWeek(next: string) {
    // Clearing must forget, not just navigate: otherwise "This week" would be undone the instant
    // you visited a list and came back.
    if (next) {
      sessionStorage.setItem(WEEK_KEY, next);
    } else {
      sessionStorage.removeItem(WEEK_KEY);
    }
    filters.set('week_of', next);
  }

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
          {longDate(new Date())} · {timezoneAbbrev()}
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
        // The navigator sits with the tiles, not in the page subtitle: it moves these numbers and
        // nothing else on the page, and a control at the top would claim to move all of it.
        <Stack gap="xs">
          <WeekNavigator week={d.week} onChange={chooseWeek} />
          <SimpleGrid cols={{ base: 1, xs: 2, md: 4 }}>
            {d.targets.map((t) => (
              <TargetTile
                key={`${t.target_type}:${t.cadence}`}
                tile={t}
                label={catalogLabel(catalogs.data?.target_types, t.target_type)}
              />
            ))}
          </SimpleGrid>
        </Stack>
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
                    // Links to `status`, not `reached`. The bar's length is reached-or-beyond, but
                    // that set only ever narrows, so linking it made the first stage a filter
                    // matching everything. `current` is the actionable set and the number sitting
                    // right beside the click.
                    <Anchor
                      key={f.status}
                      component={Link}
                      to={`/pipeline?status=${f.status}&closed=all`}
                      underline="never"
                      c="inherit"
                    >
                      <Group gap="sm" wrap="nowrap">
                        <Text size="sm" w={140} style={{ flexShrink: 0 }}>
                          {catalogLabel(catalogs.data?.opportunity_statuses, f.status)}
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
                        {/* "5 reached · 1 now": the reach count carries the funnel maths, the
                            current count is what the row opens. Both are shown because the
                            difference between them is the drop-off, which is the thing worth
                            noticing and is not visible anywhere else. */}
                        <Text size="sm" w={116} ta="right" style={{ flexShrink: 0 }}>
                          <Text span fw={600}>
                            {f.count}
                          </Text>
                          <Text span size="xs" c="dimmed" fw={400}>
                            {' reached'}
                            {pct !== null && ` · ${pct}%`}
                          </Text>
                        </Text>
                        <Text size="sm" fw={600} w={52} ta="right" style={{ flexShrink: 0 }}>
                          {f.current}
                          <Text span size="xs" c="dimmed" fw={400}>
                            {' now'}
                          </Text>
                        </Text>
                      </Group>
                    </Anchor>
                  );
                })}
              </Stack>
            </DashCard>

            {/* Revenue & payments */}
            <DashCard title="Revenue & payments">
              <SimpleGrid cols={{ base: 2, sm: 4 }}>
                {/* `?? '—'` because the shared formatter returns null for an absent amount; a
                    zero total is the string "0" and still formats as currency. */}
                <MoneyStat
                  label="Booked"
                  value={formatMoney(money.booked, money.currency) ?? '—'}
                  sub={`${money.booked_count} paid ${money.booked_count === 1 ? 'gig' : 'gigs'}`}
                  href={MONEY_LINKS.booked}
                />
                <MoneyStat
                  label="Received"
                  value={formatMoney(money.received, money.currency) ?? '—'}
                  sub={`${money.received_count} collected`}
                  color="good.7"
                  href={MONEY_LINKS.received}
                />
                <MoneyStat
                  label="Outstanding"
                  value={formatMoney(money.outstanding, money.currency) ?? '—'}
                  sub={`${money.invoiced_count} invoiced`}
                  color="warn.7"
                  href={MONEY_LINKS.outstanding}
                />
                <MoneyStat
                  label="Pro bono"
                  value={String(money.pro_bono_count)}
                  sub="visibility gigs"
                  href={MONEY_LINKS.proBono}
                />
              </SimpleGrid>
            </DashCard>

            {/* Follow-ups due — pending reminders due today or earlier (slice 7). Its own card
                rather than folded into "Coming up": that panel is future-facing, and an overdue
                reminder has to get louder rather than scroll off the top of it. */}
            <DashCard title="Follow-ups due">
              {d.follow_ups.length === 0 ? (
                <Text c="dimmed" size="sm">
                  Nothing due — you are caught up.
                </Text>
              ) : (
                <Stack gap={0}>
                  {d.follow_ups.map((f, i) => (
                    <FollowUpDueRow key={f.id} followUp={f} style={rowDivider(i)} />
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
                  All clear — nothing needs chasing right now.
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
                      <Group gap={6} wrap="nowrap" style={{ flexShrink: 0 }}>
                        {/* Duration-shaped reasons say how long, so "Gone quiet" reads as
                            specifically as its neighbours rather than as a vague mood. */}
                        {n.since && (
                          <Text size="xs" c="dimmed">
                            {daysSince(n.since)}d
                          </Text>
                        )}
                        <Badge color={REASON[n.reason].color} variant="light">
                          {REASON[n.reason].label}
                        </Badge>
                      </Group>
                    </Group>
                  ))}
                </Stack>
              )}
            </DashCard>

            {/* Coming up */}
            <DashCard title="Coming up">
              {d.coming_up.length === 0 ? (
                <Text c="dimmed" size="sm">
                  Nothing scheduled — no upcoming gigs or appointments.
                </Text>
              ) : (
                <Stack gap={0}>
                  {/* Keyed on both fields: ids are unique per type, so a gig and an appointment
                      can share one. */}
                  {d.coming_up.map((e, i) => (
                    <ComingUpRow key={`${e.item_type}-${e.id}`} event={e} style={rowDivider(i)} />
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