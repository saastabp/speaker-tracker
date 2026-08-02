import { Alert, Anchor, Badge, Button, Group, Loader, Stack, Table, Text, Title } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconAlertTriangle, IconPlus, IconStar } from '@tabler/icons-react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { catalogLabel, useCatalogs } from '../api/catalogs';
import { useContacts, useCreateContact, type ContactInput } from '../api/contacts';
import { ContactFormModal } from '../components/ContactFormModal';
import { FilterBar, type FilterPill } from '../components/FilterBar';
import { warmthColor } from '../contactChips';
import { isOverdue, parseDateLocal, shortDate } from '../dates';

export function Contacts() {
  const contacts = useContacts();
  const catalogs = useCatalogs();
  const create = useCreateContact();
  const navigate = useNavigate();
  const [addOpen, addHandlers] = useDisclosure(false);
  // Filter state lives in the URL, not in useState.
  //
  // Slice 8 makes Dashboard aggregates clickable, opening a list already narrowed to the records
  // behind them — which only works if a list page can be *told* its filter by a link. Keeping it
  // in component state would mean /contacts?filter=follow_up silently showing everyone. It also
  // makes the view shareable and survive a reload, which local state never did.
  const [searchParams, setSearchParams] = useSearchParams();
  const search = searchParams.get('q') ?? '';
  const filter = searchParams.get('filter') ?? 'everyone';

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

  async function handleCreate(values: ContactInput) {
    const created = await create.mutateAsync(values);
    navigate(`/contacts/${created.id}`);
  }

  const all = contacts.data ?? [];
  const powerCount = all.filter((c) => c.is_power_partner).length;
  // Boolean(), not `!== null`: against a backend that predates this field the value is `undefined`,
  // and `undefined !== null` is true — which would quietly mark every contact as needing follow-up.
  const followUpCount = all.filter((c) => Boolean(c.next_follow_up_date)).length;
  const pills: FilterPill[] = [
    { value: 'everyone', label: 'Everyone', active: filter === 'everyone' },
    { value: 'power', label: 'Power partners', active: filter === 'power' },
    { value: 'follow_up', label: 'Needs follow-up', active: filter === 'follow_up' },
  ];
  const term = search.trim().toLowerCase();
  const matchesFilter = (c: (typeof all)[number]) => {
    if (filter === 'power') return c.is_power_partner;
    // "Needs follow-up" means a *pending* reminder exists. The server supplies the soonest one as
    // a date, so presence is the test — a completed or deleted reminder leaves it null.
    if (filter === 'follow_up') return Boolean(c.next_follow_up_date);
    return true;
  };
  const filtered = all.filter(
    (c) =>
      matchesFilter(c) &&
      (!term ||
        c.name.toLowerCase().includes(term) ||
        (c.email ?? '').toLowerCase().includes(term)),
  );

  return (
    <Stack>
      <Group justify="space-between" align="flex-start">
        <div>
          <Title order={2} c="navy.9">
            Contacts
          </Title>
          <Text c="dimmed" size="sm">
            {all.length} {all.length === 1 ? 'person' : 'people'} · {powerCount} power partner
            {powerCount === 1 ? '' : 's'}
            {followUpCount > 0 && ` · ${followUpCount} needing follow-up`}
          </Text>
        </div>
        <Button leftSection={<IconPlus size={16} />} onClick={addHandlers.open}>
          Add contact
        </Button>
      </Group>

      {contacts.isLoading && (
        <Group>
          <Loader size="sm" />
          <Text>Loading contacts…</Text>
        </Group>
      )}
      {contacts.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={18} />}>
          {contacts.error.message}
        </Alert>
      )}

      {contacts.data && contacts.data.length > 0 && (
        <>
          <FilterBar
            search={search}
            onSearch={(value) => setParam('q', value, '')}
            searchPlaceholder="Search contacts…"
            pills={pills}
            onPillClick={(value) => setParam('filter', value, 'everyone')}
          />
          {filtered.length === 0 ? (
            <Text c="dimmed">No contacts match these filters.</Text>
          ) : (
            <Table highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Name</Table.Th>
                  <Table.Th>Role · Organization</Table.Th>
                  <Table.Th>Warmth</Table.Th>
                  <Table.Th>Source</Table.Th>
                  <Table.Th ta="right">Last touch</Table.Th>
                  <Table.Th ta="right">Next follow-up</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {filtered.map((contact) => (
                  <Table.Tr
                    key={contact.id}
                    style={{ cursor: 'pointer' }}
                    onClick={() => navigate(`/contacts/${contact.id}`)}
                  >
                    <Table.Td>
                      <Group gap={6} wrap="nowrap">
                        {contact.is_power_partner && (
                          <IconStar size={14} color="var(--mantine-color-gold-6)" />
                        )}
                        <div>
                          <Anchor
                            component={Link}
                            to={`/contacts/${contact.id}`}
                            fw={600}
                            onClick={(e) => e.stopPropagation()}
                          >
                            {contact.name}
                          </Anchor>
                          {contact.is_power_partner && (
                            <Text size="xs" c="dimmed">
                              Power partner
                            </Text>
                          )}
                        </div>
                      </Group>
                    </Table.Td>
                    <Table.Td>
                      {/* The primary affiliation stands for the person; the rest become a count,
                          since a contact wearing four hats would otherwise crowd out every other
                          column. Both fields are null until a venue is attached. */}
                      {/* Rendered only when present — an empty Text still occupies a line and
                          left the org sitting under a blank one. */}
                      {contact.primary_title && <Text size="sm">{contact.primary_title}</Text>}
                      <Group gap={6} wrap="nowrap">
                        <Text size="xs" c="dimmed">
                          {contact.primary_organization_name ?? '—'}
                        </Text>
                        {contact.organization_count > 1 && (
                          <Badge variant="light" color="gray" size="xs">
                            +{contact.organization_count - 1} orgs
                          </Badge>
                        )}
                      </Group>
                    </Table.Td>
                    <Table.Td>
                      {contact.warmth_tier && (
                        <Badge color={warmthColor(contact.warmth_tier)} variant="light">
                          {catalogLabel(catalogs.data?.warmth_tiers, contact.warmth_tier)}
                        </Badge>
                      )}
                    </Table.Td>
                    <Table.Td>
                      {/* `||`, not `??`: the column is free text and an unset one arrives as an
                          empty string as often as null, which `??` passes straight through — so
                          this cell sat blank while every other empty cell showed an em dash. */}
                      <Text size="sm">{contact.source || '—'}</Text>
                    </Table.Td>
                    <Table.Td ta="right">
                      <Text size="sm" c={contact.last_touch_date ? undefined : 'dimmed'}>
                        {contact.last_touch_date
                          ? shortDate(parseDateLocal(contact.last_touch_date))
                          : '—'}
                      </Text>
                    </Table.Td>
                    <Table.Td ta="right">
                      {/* Overdue replaces the date rather than colouring it: a date Donna has
                          already missed is not information she needs, the fact that she missed it
                          is. */}
                      {!contact.next_follow_up_date ? (
                        <Text size="sm" c="dimmed">
                          —
                        </Text>
                      ) : isOverdue(contact.next_follow_up_date) ? (
                        <Badge color="terracotta" variant="light">
                          Overdue
                        </Badge>
                      ) : (
                        <Text size="sm">
                          {shortDate(parseDateLocal(contact.next_follow_up_date))}
                        </Text>
                      )}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
        </>
      )}

      {contacts.data?.length === 0 && <Text c="dimmed">No contacts yet.</Text>}

      <ContactFormModal
        opened={addOpen}
        onClose={addHandlers.close}
        title="Add contact"
        submitLabel="Add contact"
        dedupe
        onSubmit={handleCreate}
      />
    </Stack>
  );
}