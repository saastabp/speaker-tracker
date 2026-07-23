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
  Stack,
  Switch,
  Text,
  Title,
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconAlertTriangle, IconPlus, IconX } from '@tabler/icons-react';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useCatalogs } from '../api/catalogs';
import { useFunnel } from '../api/funnel';
import {
  useCreateOpportunity,
  useOpportunities,
  usePatchStatus,
  type OpportunityInput,
  type OpportunitySummary,
} from '../api/opportunities';
import { CloseOpportunityModal, type CloseTarget } from '../components/CloseOpportunityModal';
import { OpportunityFormModal } from '../components/OpportunityFormModal';

const COLUMN_WIDTH = 264;

function formatMoney(fee: string | null, currency: string): string | null {
  if (!fee) return null;
  const amount = Number(fee);
  if (Number.isNaN(amount)) return `${currency} ${fee}`;
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(amount);
  } catch {
    return `${currency} ${amount.toFixed(2)}`;
  }
}

interface CardLabels {
  paymentLabel: (shortName: string) => string;
  paymentSettled: (shortName: string) => boolean;
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
          <Text fw={600} size="sm" lineClamp={2}>
            {opp.title}
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
        <Text size="xs" c="dimmed">
          {opp.organization_name}
        </Text>
        <Group gap={6}>
          {opp.comp_type === 'pro_bono' ? (
            <Badge color="gold" variant="light" size="sm">
              Pro bono
            </Badge>
          ) : opp.comp_type === 'trade' ? (
            <Badge color="gold" variant="light" size="sm">
              Trade
            </Badge>
          ) : (
            money && (
              <Text size="sm" fw={600} c="navy.9">
                {money}
              </Text>
            )
          )}
          <Badge
            variant="light"
            size="sm"
            color={labels.paymentSettled(opp.payment_status) ? 'green' : 'gray'}
          >
            {labels.paymentLabel(opp.payment_status)}
          </Badge>
          {opp.event_date && (
            <Text size="xs" c="dimmed">
              {opp.event_date}
            </Text>
          )}
        </Group>
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
  onOpen,
  onClose,
}: {
  shortName: string;
  label: string;
  cards: OpportunitySummary[];
  labels: CardLabels;
  onOpen: (opp: OpportunitySummary) => void;
  onClose: (opp: OpportunitySummary) => void;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: shortName });
  return (
    <Box
      ref={setNodeRef}
      style={{
        flex: `0 0 ${COLUMN_WIDTH}px`,
        borderRadius: 8,
        padding: 8,
        backgroundColor: isOver ? 'var(--mantine-color-gold-1)' : 'var(--mantine-color-navy-0)',
        transition: 'background-color 120ms ease',
      }}
    >
      <Group justify="space-between" mb="xs" px={4}>
        <Text fw={600} size="sm" c="navy.9">
          {label}
        </Text>
        <Badge variant="light" color="navy" size="sm">
          {cards.length}
        </Badge>
      </Group>
      <Stack gap="xs">
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

export function Pipeline() {
  const funnel = useFunnel();
  const catalogs = useCatalogs();
  const [showClosed, setShowClosed] = useState(false);
  const opps = useOpportunities(showClosed ? undefined : false);
  const patchStatus = usePatchStatus();
  const createOpp = useCreateOpportunity();
  const navigate = useNavigate();

  const [addOpen, addHandlers] = useDisclosure(false);
  const [closeTarget, setCloseTarget] = useState<CloseTarget | null>(null);
  const [activeOpp, setActiveOpp] = useState<OpportunitySummary | null>(null);

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));

  const paymentStatuses = catalogs.data?.payment_statuses ?? [];
  const labels: CardLabels = {
    paymentLabel: (sn) => paymentStatuses.find((p) => p.short_name === sn)?.description ?? sn,
    paymentSettled: (sn) => paymentStatuses.find((p) => p.short_name === sn)?.is_settled ?? false,
  };

  const byStatus = (shortName: string) =>
    (opps.data ?? []).filter((o) => o.current_status === shortName);

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
      patchStatus.mutate({ id: card.id, status: target });
    }
  }

  async function handleCreate(values: OpportunityInput) {
    await createOpp.mutateAsync(values);
  }

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={2} c="navy.9">
          Pipeline
        </Title>
        <Group>
          <Switch
            label="Show closed"
            checked={showClosed}
            onChange={(event) => setShowClosed(event.currentTarget.checked)}
          />
          <Button leftSection={<IconPlus size={16} />} onClick={addHandlers.open}>
            Add opportunity
          </Button>
        </Group>
      </Group>

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
                onOpen={(opp) => navigate(`/pipeline/${opp.id}`)}
                onClose={(opp) => setCloseTarget(opp)}
              />
            ))}
          </Group>
          <DragOverlay>
            {activeOpp ? <CardBody opp={activeOpp} labels={labels} /> : null}
          </DragOverlay>
        </DndContext>
      )}

      <OpportunityFormModal
        opened={addOpen}
        onClose={addHandlers.close}
        title="Add opportunity"
        submitLabel="Create"
        onSubmit={handleCreate}
      />
      <CloseOpportunityModal
        opened={closeTarget !== null}
        onClose={() => setCloseTarget(null)}
        opportunity={closeTarget}
      />
    </Stack>
  );
}