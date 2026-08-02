import { Alert, Badge, Button, Card, Group, Loader, SimpleGrid, Stack, Text, Title } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconAlertTriangle, IconPlus } from '@tabler/icons-react';
import { useState } from 'react';
import { catalogLabel, useCatalogs } from '../api/catalogs';
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
import { BRAND_PANEL } from '../theme';

export function Templates() {
  const templates = useTemplates();
  const catalogs = useCatalogs();
  const create = useCreateTemplate();
  const duplicate = useDuplicateTemplate();
  const remove = useDeleteTemplate();
  const [editing, setEditing] = useState<MessageTemplate | null>(null);
  const update = useUpdateTemplate(editing?.id ?? 0);
  const [formOpen, formHandlers] = useDisclosure(false);

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
      <Group justify="space-between" align="flex-start">
        <div>
          <Title order={2} c="navy.9">
            Message Templates
          </Title>
          <Text c="dimmed" size="sm">
            Edit shared templates in place, or duplicate to keep your own variant. Merge fields like
            [Name] fill in when you use one.
          </Text>
        </div>
        <Button leftSection={<IconPlus size={16} />} onClick={openCreate}>
          New template
        </Button>
      </Group>

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
      {templates.data?.length === 0 && <Text c="dimmed">No templates yet.</Text>}

      {templates.data && templates.data.length > 0 && (
        <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} spacing="md">
          {templates.data.map((template) => (
            <Card key={template.id} withBorder radius="md" padding="md">
              <Group justify="space-between" mb={4} wrap="nowrap">
                <Text fw={600} c="navy.9" lineClamp={1}>
                  {template.name}
                </Text>
                {template.is_shared ? (
                  <Badge color="gray" variant="light">
                    Shared
                  </Badge>
                ) : (
                  <Badge color="good" variant="light">
                    Your copy
                  </Badge>
                )}
              </Group>
              <Text size="xs" c="dimmed" mb="sm">
                {catalogLabel(catalogs.data?.message_template_kinds, template.kind)} ·{' '}
                {catalogLabel(catalogs.data?.outreach_channels, template.channel)}
              </Text>
              <Text
                size="sm"
                lineClamp={5}
                p="sm"
                style={{ background: BRAND_PANEL, borderRadius: 8, whiteSpace: 'pre-wrap' }}
              >
                {template.body}
              </Text>
              <Group gap="xs" mt="md">
                <Button size="xs" variant="default" onClick={() => openEdit(template)}>
                  Edit
                </Button>
                <Button
                  size="xs"
                  variant="default"
                  onClick={() => duplicate.mutate(template.id)}
                  loading={duplicate.isPending && duplicate.variables === template.id}
                >
                  Duplicate
                </Button>
                {!template.is_shared && (
                  <Button
                    size="xs"
                    variant="subtle"
                    color="red"
                    onClick={() => handleDelete(template)}
                  >
                    Delete
                  </Button>
                )}
              </Group>
            </Card>
          ))}
        </SimpleGrid>
      )}

      <TemplateFormModal
        opened={formOpen}
        onClose={closeForm}
        title={editing ? 'Edit template' : 'New template'}
        submitLabel={editing ? 'Save template' : 'Create template'}
        initialValues={editing ?? undefined}
        onSubmit={handleSubmit}
        onDuplicate={editing ? () => duplicate.mutateAsync(editing.id) : undefined}
      />
    </Stack>
  );
}