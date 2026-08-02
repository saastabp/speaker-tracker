import {
  DndContext,
  DragOverlay,
  PointerSensor,
  pointerWithin,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/core';
import {
  ActionIcon,
  Alert,
  Badge,
  Box,
  Button,
  Group,
  Loader,
  Paper,
  Select,
  Stack,
  Switch,
  Text,
  Title,
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconAlertTriangle, IconMessagePlus, IconPlus, IconX } from '@tabler/icons-react';
import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { catalogLabel, useCatalogs } from '../api/catalogs';
import { useFunnel } from '../api/funnel';
import {
  useCreateOpportunity,
  useOpportunities,
  usePatchStatus,
  type OpportunityCreateInput,
  type OpportunitySummary,
} from '../api/opportunities';
import { CloseOpportunityModal, type CloseTarget } from '../components/CloseOpportunityModal';
import { FilterBar, type FilterPill } from '../components/FilterBar';
import { LogOutreachModal } from '../components/LogOutreachModal';
import { OpportunityFormModal } from '../components/OpportunityFormModal';
import { parseDateLocal, shortDate } from '../dates';
import { formatMoney } from '../format';
import { STAGE_DOT, paymentColor } from '../opportunityChips';
import { BRAND_LINE, BRAND_PANEL } from '../theme';

const COLUMN_WIDTH = 264;

interface CardLabels {
  paymentLabel: (shortName: string) => string;
  paymentSettled: (shortName: string) => boolean;
  orgTypeLabel: (shortName: string) => string;
  formatLabel: (shortName: string) => string;
}

/** Presentational card body, shared by the draggable card and the drag overlay. */
function CardBody({
  opp,
  labels,
  onOpen,
  onClose,
}: {
  opp: OpportunitySummary;
  labels: CardLabels;
  onOpen?: () => void;
  onClose?: () => void;
}) {
  const money = formatMoney(opp.fee_amount, opp.currency);
  // The gig's identity beneath the venue: "<format>: <title>" (title falls back to the talk name).
  const identityLine = `${labels.formatLabel(opp.opportunity_format)}: ${opp.title}`;
  const isProBono = opp.comp_type === 'pro_bono';
  const isTrade = opp.comp_type === 'trade';
  const showMoney = Boolean(money) || isProBono || isTrade;
  return (
    <Paper
      withBorder
      p="sm"
      radius="md"
      shadow="xs"
      onClick={onOpen}
      style={{ backgroundColor: 'white', cursor: 'grab' }}
    >
      <Stack gap={6}>
        <Group justify="space-between" wrap="nowrap" align="flex-start" gap={4}>
          <Text fw={600} size="sm" c="navy.9" lineClamp={2}>
            {opp.organization_name}
          </Text>
          {onClose && (
            <ActionIcon
              variant="subtle"
              color="gray"
              size="sm"
              aria-label="Close opportunity"
              onPointerDown={(event) => event.stopPropagation()}
              onClick={(event) => {
                event.stopPropagation();
                onClose();
              }}
            >
              <IconX size={14} />
            </ActionIcon>
          )}
        </Group>
        <Text size="xs" c="dimmed" lineClamp={1}>
          {identityLine}
        </Text>
        <Group gap={6} wrap="wrap">
          <Badge variant="light" color="gray" size="sm">
            {labels.orgTypeLabel(opp.organization_type)}
          </Badge>
          {opp.event_date && (
            <Text size="xs" c="dimmed">
              {shortDate(parseDateLocal(opp.event_date))}
            </Text>
          )}
        </Group>
        {showMoney && (
          <Group gap={6}>
            {isProBono ? (
              <Badge color="gold" variant="light" size="sm">
                Pro bono
              </Badge>
            ) : isTrade ? (
              <Badge color="gold" variant="light" size="sm">
                Trade
              </Badge>
            ) : (
              <>
                <Text size="sm" fw={600} c="navy.9">
                  {money}
                </Text>
                <Badge
                  variant="light"
                  size="sm"
                  color={paymentColor(
                    opp.payment_status,
                    labels.paymentSettled(opp.payment_status),
                  )}
                >
                  {labels.paymentLabel(opp.payment_status)}
                </Badge>
              </>
            )}
          </Group>
        )}
      </Stack>
    </Paper>
  );
}

function DraggableCard({
  opp,
  labels,
  onOpen,
  onClose,
}: {
  opp: OpportunitySummary;
  labels: CardLabels;
  onOpen: () => void;
  onClose: () => void;
}) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id: opp.id });
  return (
    <div
      ref={setNodeRef}
      {...attributes}
      {...listeners}
      style={{ opacity: isDragging ? 0.4 : 1, touchAction: 'none' }}
    >
      <CardBody opp={opp} labels={labels} onOpen={onOpen} onClose={onClose} />
    </div>
  );
}

function Column({
  shortName,
  label,
  cards,
  labels,
  flashTitle,
  onOpen,
  onClose,
}: {
  shortName: string;
  label: string;
  cards: OpportunitySummary[];
  labels: CardLabels;
  flashTitle: string | null;
  onOpen: (opp: OpportunitySummary) => void;
  onClose: (opp: OpportunitySummary) => void;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: shortName });
  return (
    <Box
      ref={setNodeRef}
      style={{
        flex: `0 0 ${COLUMN_WIDTH}px`,
        borderRadius: 12,
        padding: 10,
        border: `1px solid ${BRAND_LINE}`,
        backgroundColor: isOver ? 'var(--mantine-color-gold-2)' : BRAND_PANEL,
        transition: 'background-color 120ms ease',
      }}
    >
      <Group justify="space-between" mb="xs" px={4} wrap="nowrap">
        <Group gap={7} wrap="nowrap">
          <Box
            w={8}
            h={8}
            style={{
              borderRadius: '50%',
              background: STAGE_DOT[shortName] ?? 'var(--mantine-color-gray-5)',
              flexShrink: 0,
            }}
          />
          <Text fw={700} size="xs" tt="uppercase" c="dimmed" style={{ letterSpacing: '0.05em' }}>
            {label}
          </Text>
        </Group>
        <Badge variant="light" color="navy" size="sm">
          {cards.length}
        </Badge>
      </Group>
      <Stack gap="xs">
        {flashTitle && (
          <Box
            style={{
              border: '1px solid var(--mantine-color-green-6)',
              borderRadius: 6,
              padding: '6px 8px',
              backgroundColor: 'var(--mantine-color-green-0)',
            }}
          >
            <Text size="xs" fw={600} c="green.8">
              "{flashTitle}" moved to History
            </Text>
          </Box>
        )}
        {cards.map((opp) => (
          <DraggableCard
            key={opp.id}
            opp={opp}
            labels={labels}
            onOpen={() => onOpen(opp)}
            onClose={() => onClose(opp)}
          />
        ))}
      </Stack>
    </Box>
  );
}

/** The dedicated "Recently closed" column (mockup) — read-only, non-draggable, muted cards. */
function ClosedColumn({
  cards,
  labels,
  onOpen,
}: {
  cards: OpportunitySummary[];
  labels: CardLabels;
  onOpen: (opp: OpportunitySummary) => void;
}) {
  return (
    <Box
      style={{
        flex: `0 0 ${COLUMN_WIDTH}px`,
        borderRadius: 12,
        padding: 10,
        border: `1px solid ${BRAND_LINE}`,
        backgroundColor: BRAND_PANEL,
      }}
    >
      <Group justify="space-between" mb="xs" px={4} wrap="nowrap">
        <Group gap={7} wrap="nowrap">
          <Box
            w={8}
            h={8}
            style={{ borderRadius: '50%', background: 'var(--mantine-color-gray-5)', flexShrink: 0 }}
          />
          <Text fw={700} size="xs" tt="uppercase" c="dimmed" style={{ letterSpacing: '0.05em' }}>
            Recently closed
          </Text>
        </Group>
        <Badge variant="light" color="navy" size="sm">
          {cards.length}
        </Badge>
      </Group>
      <Stack gap="xs">
        {cards.map((opp) => (
          <div key={opp.id} style={{ opacity: 0.72 }}>
            <CardBody opp={opp} labels={labels} onOpen={() => onOpen(opp)} />
          </div>
        ))}
      </Stack>
    </Box>
  );
}

export function Pipeline() {
  const funnel = useFunnel();
  const catalogs = useCatalogs();
  // The whole filter row lives in the URL, not in useState (the Contacts pattern). Slice 8 turns
  // each dashboard aggregate into a link into the list behind it, which only works if a link can
  // *tell* this board what to show — local state would leave /pipeline?reached=booked quietly
  // rendering the unfiltered board. "Show closed" belongs to that row for the same reason: the
  // funnel counts gigs that have since closed, so a drill-down link has to be able to ask for them.
  const [searchParams, setSearchParams] = useSearchParams();
  const search = searchParams.get('q') ?? '';
  const reached = searchParams.get('reached') ?? '';
  const comp = searchParams.get('comp') ?? '';
  const pay = searchParams.get('pay') ?? '';
  const showClosed = searchParams.get('closed') === 'all';
  const opps = useOpportunities(showClosed ? undefined : false);
  const patchStatus = usePatchStatus();
  const createOpp = useCreateOpportunity();
  const navigate = useNavigate();

  const [addOpen, addHandlers] = useDisclosure(false);
  const [logOpen, logHandlers] = useDisclosure(false);
  const [closeTarget, setCloseTarget] = useState<CloseTarget | null>(null);
  const [activeOpp, setActiveOpp] = useState<OpportunitySummary | null>(null);
  // A transient "moved to History" flash anchored to the column a drag archived from.
  const [flash, setFlash] = useState<{ status: string; title: string } | null>(null);
  const flashTimer = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearTimeout(flashTimer.current), []);

  /** Write one filter key, dropping it from the URL when it returns to its default. */
  const setParam = (key: string, value: string, fallback: string) => {
    const next = new URLSearchParams(searchParams);
    if (!value || value === fallback) {
      next.delete(key);
    } else {
      next.set(key, value);
    }
    // replace: filtering is not navigation, and each keystroke should not be a Back-button stop.
    setSearchParams(next, { replace: true });
  };

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));

  const paymentStatuses = catalogs.data?.payment_statuses ?? [];
  const labels: CardLabels = {
    paymentLabel: (sn) => catalogLabel(paymentStatuses, sn),
    // Not a label lookup — `is_settled` is a flag only this catalog carries, so it stays local.
    paymentSettled: (sn) => paymentStatuses.find((p) => p.short_name === sn)?.is_settled ?? false,
    orgTypeLabel: (sn) => catalogLabel(catalogs.data?.organization_types, sn),
    formatLabel: (sn) => catalogLabel(catalogs.data?.opportunity_formats, sn),
  };

  const statusSort = (shortName: string | null) =>
    shortName
      ? ((catalogs.data?.opportunity_statuses ?? []).find((s) => s.short_name === shortName)
          ?.sort_order ?? null)
      : null;
  const reachFloor = statusSort(reached || null);

  // Reached-or-beyond, mirroring core.funnel.reached_or_beyond: a gig counts toward a stage when
  // the furthest status it ever reached sits at or past that stage. Comparing the high-water mark
  // rather than current_status is what makes this board's count match a dashboard funnel bar — a
  // gig cancelled after being booked counts toward Booked while sitting in no board column at all.
  //
  // A card with no high-water mark (an older backend predating the field) drops out of a reach
  // filter rather than passing it: failing visibly beats a board that silently claims to be the set
  // behind an aggregate while in fact showing every gig.
  const matchesReach = (o: OpportunitySummary) => {
    if (reachFloor == null) return true;
    const mark = statusSort(o.max_reached_status);
    return mark != null && mark >= reachFloor;
  };

  const term = search.trim().toLowerCase();
  // Outstanding is `invoiced`, not "unsettled": the dashboard counts exactly the billable gigs
  // already invoiced, and an unbilled gig is unsettled too — matching on settlement would overshoot
  // the very aggregate this pill exists to open.
  const matches = (o: OpportunitySummary) =>
    matchesReach(o) &&
    (!comp || o.comp_type === comp) &&
    (pay !== 'outstanding' || (o.comp_type === 'paid' && o.payment_status === 'invoiced')) &&
    (!term ||
      o.title.toLowerCase().includes(term) ||
      (o.talk_title ?? '').toLowerCase().includes(term) ||
      o.organization_name.toLowerCase().includes(term));

  const all = opps.data ?? [];
  const visible = all.filter(matches);
  const isFiltered = Boolean(term || reached || comp || pay);

  const byStatus = (shortName: string) =>
    visible.filter((o) => o.current_status === shortName && !o.closed_at);

  const openCount = visible.filter((o) => !o.closed_at).length;
  // Closed gigs leave their status columns and gather in a dedicated column, most-recent first.
  const closedCards = visible
    .filter((o) => o.closed_at)
    .sort((a, b) => (b.closed_at ?? '').localeCompare(a.closed_at ?? ''));

  const pills: FilterPill[] = [
    { value: 'paid', label: 'Paid', active: comp === 'paid' },
    { value: 'pro_bono', label: 'Pro bono', active: comp === 'pro_bono' },
    { value: 'outstanding', label: 'Outstanding', active: pay === 'outstanding' },
  ];
  function handlePill(value: string) {
    if (value === 'outstanding') setParam('pay', pay === 'outstanding' ? '' : 'outstanding', '');
    else setParam('comp', comp === value ? '' : value, '');
  }

  function handleDragStart(event: DragStartEvent) {
    setActiveOpp((opps.data ?? []).find((o) => o.id === Number(event.active.id)) ?? null);
  }

  function handleDragEnd(event: DragEndEvent) {
    setActiveOpp(null);
    const { active, over } = event;
    if (!over) return;
    const card = (opps.data ?? []).find((o) => o.id === Number(active.id));
    const target = String(over.id);
    if (card && card.current_status !== target) {
      patchStatus.mutate(
        { id: card.id, status: target },
        {
          onSuccess: (updated) => {
            // A move that settles a delivered gig closes it — flash at the column it left from so
            // the user sees why the card disappeared (their eye is on the drop target).
            if (updated.closed_at) {
              window.clearTimeout(flashTimer.current);
              setFlash({ status: target, title: updated.title });
              flashTimer.current = window.setTimeout(() => setFlash(null), 4000);
            }
          },
        },
      );
    }
  }

  async function handleCreate(values: OpportunityCreateInput) {
    await createOpp.mutateAsync(values);
  }

  return (
    <Stack>
      <Group justify="space-between" align="flex-start">
        <div>
          <Title order={2} c="navy.9">
            Pipeline
          </Title>
          <Text c="dimmed" size="sm">
            {/* Under a filter the headline number is the size of the matched set, not the open
                count — that is the number the reader compares against the dashboard aggregate they
                clicked to get here (slice 8 acceptance #1). */}
            {isFiltered
              ? `${visible.length} of ${all.length} ${all.length === 1 ? 'gig' : 'gigs'} match`
              : `${openCount} open ${openCount === 1 ? 'opportunity' : 'opportunities'}`}{' '}
            · drag cards between stages
          </Text>
        </div>
        <Group>
          <Switch
            label="Show closed"
            checked={showClosed}
            onChange={(event) => setParam('closed', event.currentTarget.checked ? 'all' : '', '')}
          />
          <Button
            variant="default"
            leftSection={<IconMessagePlus size={16} />}
            onClick={logHandlers.open}
          >
            Log outreach
          </Button>
          <Button leftSection={<IconPlus size={16} />} onClick={addHandlers.open}>
            New opportunity
          </Button>
        </Group>
      </Group>

      {all.length > 0 && (
        <FilterBar
          search={search}
          onSearch={(value) => setParam('q', value, '')}
          searchPlaceholder="Search venues, gigs…"
          pills={pills}
          onPillClick={handlePill}
          extra={
            // The stage vocabulary comes from the server's funnel, never a hardcoded list
            // (slice 3 acceptance #9) — and "or beyond" is the funnel's own reading of a stage.
            <Select
              size="xs"
              w={210}
              clearable
              placeholder="Reached any stage"
              aria-label="Filter by furthest stage reached"
              value={reached || null}
              onChange={(value) => setParam('reached', value ?? '', '')}
              data={(funnel.data ?? []).map((stage) => ({
                value: stage.short_name,
                label: `${stage.label} or beyond`,
              }))}
            />
          }
        />
      )}

      {(funnel.isLoading || opps.isLoading) && (
        <Group>
          <Loader size="sm" />
          <Text>Loading the board…</Text>
        </Group>
      )}
      {funnel.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={18} />}>
          {funnel.error.message}
        </Alert>
      )}
      {opps.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={18} />}>
          {opps.error.message}
        </Alert>
      )}

      {funnel.data && opps.data && (
        <DndContext
          sensors={sensors}
          collisionDetection={pointerWithin}
          onDragStart={handleDragStart}
          onDragEnd={handleDragEnd}
        >
          <Group align="flex-start" wrap="nowrap" gap="sm" style={{ overflowX: 'auto', paddingBottom: 8 }}>
            {funnel.data.map((stage) => (
              <Column
                key={stage.short_name}
                shortName={stage.short_name}
                label={stage.label}
                cards={byStatus(stage.short_name)}
                labels={labels}
                flashTitle={flash?.status === stage.short_name ? flash.title : null}
                onOpen={(opp) => navigate(`/pipeline/${opp.id}`)}
                onClose={(opp) => setCloseTarget(opp)}
              />
            ))}
            {showClosed && closedCards.length > 0 && (
              <ClosedColumn
                cards={closedCards}
                labels={labels}
                onOpen={(opp) => navigate(`/pipeline/${opp.id}`)}
              />
            )}
          </Group>
          <DragOverlay>
            {activeOpp ? <CardBody opp={activeOpp} labels={labels} /> : null}
          </DragOverlay>
        </DndContext>
      )}

      {opps.data && (
        <Text size="xs" c="dimmed" mt={4}>
          Closed gigs (Delivered &amp; settled, Cancelled, Lost) leave the board and live in History —
          toggle Show closed to peek at recently-closed cards here.
        </Text>
      )}

      <OpportunityFormModal
        opened={addOpen}
        onClose={addHandlers.close}
        title="New opportunity"
        submitLabel="Create opportunity"
        onSubmit={handleCreate}
      />
      <CloseOpportunityModal
        opened={closeTarget !== null}
        onClose={() => setCloseTarget(null)}
        opportunity={closeTarget}
      />
      <LogOutreachModal opened={logOpen} onClose={logHandlers.close} />
    </Stack>
  );
}