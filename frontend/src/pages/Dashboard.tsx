import {
  Alert,
  Anchor,
  Badge,
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
  type NeedsAttentionItem,
  type TargetTile as TargetTileData,
} from '../api/dashboard';

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

const CADENCE_LABEL: Record<string, string> = {
  weekly: 'Weekly',
  monthly: 'Monthly',
  quarterly: 'Quarterly',
};

const REASON: Record<NeedsAttentionItem['reason'], { label: string; color: string }> = {
  awaiting_payment: { label: 'Awaiting payment', color: 'yellow' },
  overdue_unbooked: { label: 'Overdue', color: 'red' },
};

function TargetTile({ tile, label }: { tile: TargetTileData; label: string }) {
  const pct = tile.goal > 0 ? Math.min(100, Math.round((tile.actual / tile.goal) * 100)) : 0;
  const met = tile.goal > 0 && tile.actual >= tile.goal;
  return (
    <Card withBorder radius="md" padding="md">
      <Group justify="space-between" wrap="nowrap">
        <Text fw={600} size="sm">
          {label}
        </Text>
        <Badge size="sm" variant="light" color="gray">
          {CADENCE_LABEL[tile.cadence] ?? tile.cadence}
        </Badge>
      </Group>
      <Group align="baseline" gap={6} mt="xs">
        <Text fz="1.9rem" fw={700} c="navy.9" lh={1}>
          {tile.actual}
        </Text>
        <Text c="dimmed">/ {tile.goal}</Text>
      </Group>
      <Progress value={pct} color={met ? 'teal' : 'terracotta'} radius="xl" mt="sm" />
      <Group gap={4} mt={6}>
        {met && <IconCircleCheck size={14} color="var(--mantine-color-teal-6)" />}
        <Text size="xs" c={met ? 'teal.7' : 'dimmed'}>
          {met ? 'Goal met' : `${pct}% of goal`}
        </Text>
      </Group>
    </Card>
  );
}

export function Dashboard() {
  const catalogs = useCatalogs();
  const dash = useDashboard();

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

  return (
    <Stack>
      <Title order={2} c="navy.9">
        Dashboard
      </Title>

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

      <Grid>
        {/* Funnel */}
        <Grid.Col span={{ base: 12, md: 7 }}>
          <Card withBorder radius="md" h="100%">
            <Text fw={600} mb="sm">
              Funnel — reached or beyond
            </Text>
            <Stack gap="sm">
              {d.funnel.map((f) => (
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
                        background: 'var(--mantine-color-navy-6)',
                        borderRadius: 8,
                      }}
                    />
                  </div>
                  <Text size="sm" fw={600} w={28} ta="right">
                    {f.count}
                  </Text>
                </Group>
              ))}
            </Stack>
          </Card>
        </Grid.Col>

        {/* Money */}
        <Grid.Col span={{ base: 12, md: 5 }}>
          <Card withBorder radius="md" h="100%">
            <Text fw={600} mb="sm">
              Money
            </Text>
            <SimpleGrid cols={3}>
              <div>
                <Text size="xs" c="dimmed">
                  Booked
                </Text>
                <Text fw={700} c="navy.9">
                  {formatMoney(d.money.booked, d.money.currency)}
                </Text>
              </div>
              <div>
                <Text size="xs" c="dimmed">
                  Received
                </Text>
                <Text fw={700} c="navy.9">
                  {formatMoney(d.money.received, d.money.currency)}
                </Text>
              </div>
              <div>
                <Text size="xs" c="dimmed">
                  Outstanding
                </Text>
                <Text fw={700} c="terracotta.7">
                  {formatMoney(d.money.outstanding, d.money.currency)}
                </Text>
              </div>
            </SimpleGrid>
            <Text size="sm" c="dimmed" mt="md">
              Pro bono: {d.money.pro_bono_count} {d.money.pro_bono_count === 1 ? 'gig' : 'gigs'}
            </Text>
          </Card>
        </Grid.Col>
      </Grid>

      <Grid>
        {/* Stale */}
        <Grid.Col span={{ base: 12, md: 6 }}>
          <Card withBorder radius="md" h="100%">
            <Text fw={600} mb="sm">
              Stale — needs a nudge
            </Text>
            {d.stale.length === 0 ? (
              <Text c="dimmed" size="sm">
                Nothing stale — every active gig has recent activity.
              </Text>
            ) : (
              <Stack gap="xs">
                {d.stale.map((s) => (
                  <Group key={s.id} justify="space-between" wrap="nowrap">
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
          </Card>
        </Grid.Col>

        {/* Needs attention */}
        <Grid.Col span={{ base: 12, md: 6 }}>
          <Card withBorder radius="md" h="100%">
            <Text fw={600} mb="sm">
              Needs attention
            </Text>
            {d.needs_attention.length === 0 ? (
              <Text c="dimmed" size="sm">
                All clear — nothing awaiting payment or overdue.
              </Text>
            ) : (
              <Stack gap="xs">
                {d.needs_attention.map((n) => (
                  <Group key={`${n.reason}:${n.id}`} justify="space-between" wrap="nowrap">
                    <Anchor component={Link} to={`/pipeline/${n.id}`} size="sm" lineClamp={1}>
                      {n.title}
                    </Anchor>
                    <Badge color={REASON[n.reason].color} variant="light" style={{ flexShrink: 0 }}>
                      {REASON[n.reason].label}
                    </Badge>
                  </Group>
                ))}
              </Stack>
            )}
          </Card>
        </Grid.Col>
      </Grid>
    </Stack>
  );
}