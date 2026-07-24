import { Button, Card, Group, Select, Stack, Text } from '@mantine/core';
import { IconCheck, IconCopy } from '@tabler/icons-react';
import { useState } from 'react';
import { useTemplates, type MessageTemplate } from '../api/templates';

/** Fill the merge fields that resolve from the contact. Only `[Name]` is contact-derived today;
 *  other bracketed placeholders (e.g. `[Your signature]`) are left for the sender to complete. */
function fillMerge(text: string, contactName: string): string {
  return text.split('[Name]').join(contactName);
}

interface TemplatePickerProps {
  contactName: string;
  /** Reports the chosen template (or null when cleared) so the modal can set channel + link it. */
  onTemplateSelected: (template: MessageTemplate | null) => void;
  /** When given, only templates for these channel short_names are offered (e.g. the manual
   *  Log-Outreach channels — email templates are owned by the composer, not shown here). */
  allowedChannels?: string[];
}

/** Pick a message template, preview it with merge fields filled from the contact, and copy the
 *  merged text to the clipboard for the DM paste flow (acceptance #3). */
export function TemplatePicker({
  contactName,
  onTemplateSelected,
  allowedChannels,
}: TemplatePickerProps) {
  const templates = useTemplates();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const visible = (templates.data ?? []).filter(
    (t) => !allowedChannels || allowedChannels.includes(t.channel),
  );
  const selected = visible.find((t) => String(t.id) === selectedId) ?? null;
  const mergedBody = selected ? fillMerge(selected.body, contactName) : '';
  const mergedSubject = selected?.subject ? fillMerge(selected.subject, contactName) : null;

  const options = visible.map((t) => ({
    value: String(t.id),
    label: `${t.name} (${t.channel})`,
  }));

  function handleChange(value: string | null) {
    setSelectedId(value);
    setCopied(false);
    onTemplateSelected(visible.find((t) => String(t.id) === value) ?? null);
  }

  async function handleCopy() {
    const text = mergedSubject ? `Subject: ${mergedSubject}\n\n${mergedBody}` : mergedBody;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard can be blocked (permissions / insecure context); the preview is still selectable.
      setCopied(false);
    }
  }

  return (
    <Stack gap="xs">
      <Select
        label="Start from a template"
        placeholder="Optional — merge & copy, then paste into the DM or email"
        data={options}
        value={selectedId}
        onChange={handleChange}
        clearable
        searchable
      />
      {selected && (
        <Card withBorder radius="md" padding="sm" bg="var(--mantine-color-gray-0)">
          <Group justify="space-between" mb={4}>
            <Text size="xs" fw={600} c="dimmed">
              Preview (merge fields filled from {contactName})
            </Text>
            <Button
              size="xs"
              variant="light"
              leftSection={copied ? <IconCheck size={14} /> : <IconCopy size={14} />}
              onClick={handleCopy}
            >
              {copied ? 'Copied!' : 'Copy to clipboard'}
            </Button>
          </Group>
          {mergedSubject && (
            <Text size="sm" mb={4}>
              <Text span fw={600}>
                Subject:{' '}
              </Text>
              {mergedSubject}
            </Text>
          )}
          <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
            {mergedBody}
          </Text>
        </Card>
      )}
    </Stack>
  );
}