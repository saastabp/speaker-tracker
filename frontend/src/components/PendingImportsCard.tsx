import { Alert, Badge, Button, Card, Checkbox, Group, Table, Text } from '@mantine/core';
import { useState } from 'react';
import type { ContactInput } from '../api/contacts';
import { useImportPendingThread, usePendingImports, type PendingImport } from '../api/emailImports';
import { ContactFormModal } from './ContactFormModal';
import { CardTitle } from './detailCards';

/**
 * The triage queue: mail the poller was allowed to ingest but which belongs to nobody yet.
 *
 * **Hidden entirely when empty.** A permanent "nothing awaiting import" card on the main email
 * screen is noise on every visit for a state that is the norm; the card appearing *is* the signal.
 *
 * Rows arrive here two ways: Donna drags a stranger's message into the `Import` folder — the drag
 * being her per-message authorization, since the app will never decide on its own that a stranger
 * belongs in the CRM — or an inbound message's header chain joins a thread that has no contact.
 */
export function PendingImportsCard() {
  const pending = usePendingImports();
  const [importing, setImporting] = useState<PendingImport | null>(null);
  /** Threads whose suggested venue Donna has unticked. Absent means "attach it" — the suggestion
   *  is only ever offered when exactly one venue claims the domain, so accepting is the default. */
  const [venueDeclined, setVenueDeclined] = useState<Record<number, boolean>>({});
  const importThread = useImportPendingThread();

  // Nothing to triage is the ordinary state, so say nothing.
  if (!pending.data || pending.data.length === 0) return null;

  const rows = pending.data;
  const venueFor = (row: PendingImport): number | null =>
    venueDeclined[row.thread_id] ? null : row.suggested_organization_id;

  return (
    <>
      <Card withBorder radius="md" p={0}>
        <Group justify="space-between" p="md" pb="xs">
          <Group gap="xs">
            <CardTitle>Awaiting import</CardTitle>
            <Badge color="orange" variant="light">
              {rows.length}
            </Badge>
          </Group>
          <Text size="xs" c="dimmed">
            Dragged into Speaker Tracker/Import
          </Text>
        </Group>

        {importThread.isError && (
          <Alert color="red" variant="light" mx="md" mb="xs">
            Could not import that message. The contact may have been created — check Contacts
            before trying again.
          </Alert>
        )}

        <Table highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>From</Table.Th>
              <Table.Th>Subject</Table.Th>
              <Table.Th>Received</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((row) => (
              <Table.Tr key={row.thread_id}>
                <Table.Td>
                  <Text fw={500}>{row.from_name ?? row.from_addr}</Text>
                  {row.from_name && (
                    <Text size="xs" c="dimmed">
                      {row.from_addr}
                    </Text>
                  )}
                  {/* Only ever offered when exactly one venue claims the sender's domain — a
                      shared domain identifies nobody, so the server withholds rather than
                      guessing. The choice sits here, beside the evidence for it, rather than in
                      the contact form: the form is a modal, so anything below it is unreachable
                      once open, and this is a decision about the *sender*, not about the fields. */}
                  {row.suggested_organization_name && (
                    <Checkbox
                      mt={6}
                      size="xs"
                      checked={!venueDeclined[row.thread_id]}
                      onChange={(event) =>
                        setVenueDeclined((declined) => ({
                          ...declined,
                          [row.thread_id]: !event.currentTarget.checked,
                        }))
                      }
                      label={
                        <Text size="xs" c="dimmed">
                          Also link to <strong>{row.suggested_organization_name}</strong>
                        </Text>
                      }
                    />
                  )}
                </Table.Td>
                <Table.Td>
                  <Text size="sm">{row.subject || '(no subject)'}</Text>
                </Table.Td>
                <Table.Td>
                  <Text size="sm">{formatReceived(row.received_at)}</Text>
                </Table.Td>
                <Table.Td align="right">
                  <Button size="xs" variant="light" onClick={() => setImporting(row)}>
                    Add contact
                  </Button>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>

      </Card>

      {/* The shared contact form, unmodified. It carries the live dedupe hint, which is the point:
          "offers to attach rather than create, for a person we already know" IS slice 2's dedupe,
          so the import flow saves through the same path rather than growing its own. */}
      <ContactFormModal
        opened={importing !== null}
        onClose={() => setImporting(null)}
        title="Add contact from email"
        submitLabel="Add and import"
        dedupe
        initialValues={initialContact(importing)}
        onSubmit={async (values) => {
          if (!importing) return;
          await importThread.mutateAsync({
            threadId: importing.thread_id,
            contact: values,
            organizationId: venueFor(importing),
          });
          setImporting(null);
        }}
      />
    </>
  );
}

/**
 * Seed the form from the `From` header (acceptance #4).
 *
 * The address is always known; the display name often is not, and an empty name is better than
 * inventing one from the local part — `pat.host@` is not reliably "Pat Host", and a wrong name is
 * harder to notice than a blank one.
 */
function initialContact(row: PendingImport | null): ContactInput | undefined {
  if (!row) return undefined;
  return { name: row.from_name ?? '', email: row.from_addr };
}

function formatReceived(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}