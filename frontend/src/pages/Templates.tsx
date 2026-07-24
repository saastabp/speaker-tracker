import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Group,
  Loader,
  Stack,
  Table,
  Text,
  Title,
  Tooltip,
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconAlertTriangle, IconCopy, IconPencil, IconPlus, IconTrash } from '@tabler/icons-react';
import { useState } from 'react';
import { useCatalogs } from '../api/catalogs';
import {
  useCreateTemplate,
  useDeleteTemplate,
  useDuplicateTemplate,
  useTemplates,
  useUpdateTemplate,
  type MessageTemplate,
  type MessageTemplateInput,
} from '../api/templates';
import { TemplateFormModal } from '../components/TemplateFormModal';

export function Templates() {
  const templates = useTemplates();
  const catalogs = useCatalogs();
  const create = useCreateTemplate();
  const duplicate = useDuplicateTemplate();
  const remove = useDeleteTemplate();
  const [editing, setEditing] = useState<MessageTemplate | null>(null);
  const update = useUpdateTemplate(editing?.id ?? 0);
  const [formOpen, formHandlers] = useDisclosure(false);

  const kindLabel = (shortName: string) =>
    catalogs.data?.message_template_kinds.find((k) => k.short_name === shortName)?.description ??
    shortName;
  const channelLabel = (shortName: string) =>
    catalogs.data?.outreach_channels.find((c) => c.short_name === shortName)?.description ??
    shortName;

  function openCreate() {
    setEditing(null);
    formHandlers.open();
  }

  function openEdit(template: MessageTemplate) {
    setEditing(template);
    formHandlers.open();
  }

  function closeForm() {
    formHandlers.close();
    setEditing(null);
  }

  async function handleSubmit(values: MessageTemplateInput) {
    if (editing) {
      await update.mutateAsync(values);
    } else {
      await create.mutateAsync(values);
    }
  }

  function handleDelete(template: MessageTemplate) {
    if (window.confirm(`Delete “${template.name}”?`)) {
      remove.mutate(template.id);
    }
  }

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={2} c="navy.9">
          Templates
        </Title>
        <Button leftSection={<IconPlus size={16} />} onClick={openCreate}>
          New Template
        </Button>
      </Group>

      <Text c="dimmed" size="sm">
        Reusable outreach copy. Shared templates are edited in place; Duplicate makes a personal copy
        you can tweak.
      </Text>

      {templates.isLoading && (
        <Group>
          <Loader size="sm" />
          <Text>Loading templates…</Text>
        </Group>
      )}
      {templates.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={18} />}>
          {templates.error.message}
        </Alert>
      )}

      {templates.data && templates.data.length > 0 && (
        <Table highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Name</Table.Th>
              <Table.Th>Purpose</Table.Th>
              <Table.Th>Channel</Table.Th>
              <Table.Th>Scope</Table.Th>
              <Table.Th ta="right">Actions</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {templates.data.map((template) => (
              <Table.Tr key={template.id}>
                <Table.Td>{template.name}</Table.Td>
                <Table.Td>{kindLabel(template.kind)}</Table.Td>
                <Table.Td>{channelLabel(template.channel)}</Table.Td>
                <Table.Td>
                  {template.is_shared ? (
                    <Badge variant="light">Shared</Badge>
                  ) : (
                    <Badge variant="light" color="gray">
                      Personal
                    </Badge>
                  )}
                </Table.Td>
                <Table.Td>
                  <Group gap="xs" justify="flex-end" wrap="nowrap">
                    <Tooltip label="Edit">
                      <ActionIcon variant="subtle" onClick={() => openEdit(template)}>
                        <IconPencil size={16} />
                      </ActionIcon>
                    </Tooltip>
                    <Tooltip label="Duplicate">
                      <ActionIcon
                        variant="subtle"
                        onClick={() => duplicate.mutate(template.id)}
                        loading={duplicate.isPending && duplicate.variables === template.id}
                      >
                        <IconCopy size={16} />
                      </ActionIcon>
                    </Tooltip>
                    {!template.is_shared && (
                      <Tooltip label="Delete">
                        <ActionIcon
                          variant="subtle"
                          color="red"
                          onClick={() => handleDelete(template)}
                        >
                          <IconTrash size={16} />
                        </ActionIcon>
                      </Tooltip>
                    )}
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      <TemplateFormModal
        opened={formOpen}
        onClose={closeForm}
        title={editing ? 'Edit Template' : 'New Template'}
        submitLabel={editing ? 'Save' : 'Create'}
        initialValues={editing ?? undefined}
        onSubmit={handleSubmit}
      />
    </Stack>
  );
}