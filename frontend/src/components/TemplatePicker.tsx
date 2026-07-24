import { Button, Group, Select, Textarea } from '@mantine/core';
import { IconCheck, IconCopy } from '@tabler/icons-react';
import { useEffect, useState } from 'react';
import { useTemplates, type MessageTemplate } from '../api/templates';
import { FieldLabel } from './FieldLabel';

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

/** Pick a message template, preview it with merge fields filled from the contact in an editable
 *  textarea, and copy the merged (possibly hand-edited) text to the clipboard for the DM paste
 *  flow (acceptance #3). Renders the mockup's two `.frm` fields: Template select + Message. */
export function TemplatePicker({
  contactName,
  onTemplateSelected,
  allowedChannels,
}: TemplatePickerProps) {
  const templates = useTemplates();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [message, setMessage] = useState('');
  const [copied, setCopied] = useState(false);

  const visible = (templates.data ?? []).filter(
    (t) => !allowedChannels || allowedChannels.includes(t.channel),
  );

  const options = visible.map((t) => ({
    value: String(t.id),
    label: `${t.name} (${t.channel})`,
  }));

  // Refill the editable message from the merged template body whenever the picked template or the
  // contact changes. This discards manual edits by design — you choose contact + template first,
  // then tweak — and keeps `[Name]` in sync once the contact resolves.
  useEffect(() => {
    const tmpl = (templates.data ?? []).find((t) => String(t.id) === selectedId) ?? null;
    if (tmpl) {
      // Fall back to a neutral greeting name until a contact is picked, so the merged text reads
      // "Hi there," rather than a dangling "Hi ,".
      const mergeName = contactName.trim() || 'there';
      const subject = tmpl.subject ? fillMerge(tmpl.subject, mergeName) : null;
      const body = fillMerge(tmpl.body, mergeName);
      setMessage(subject ? `Subject: ${subject}\n\n${body}` : body);
    } else {
      setMessage('');
    }
    setCopied(false);
  }, [selectedId, contactName, templates.data]);

  function handleChange(value: string | null) {
    setSelectedId(value);
    onTemplateSelected(visible.find((t) => String(t.id) === value) ?? null);
  }

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(message);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard can be blocked (permissions / insecure context); the message is still selectable.
      setCopied(false);
    }
  }

  return (
    <>
      <div>
        <FieldLabel helper="merges & copies for a DM">Template</FieldLabel>
        <Select
          placeholder="Optional — start from a saved template"
          data={options}
          value={selectedId}
          onChange={handleChange}
          clearable
          searchable
        />
      </div>

      <div>
        <FieldLabel helper="merged — edit before you copy">Message</FieldLabel>
        <Textarea
          value={message}
          onChange={(event) => setMessage(event.currentTarget.value)}
          autosize
          minRows={4}
          placeholder="Pick a template above, or write the note you'll paste into the DM"
        />
        <Group justify="flex-end" mt={4}>
          <Button
            size="xs"
            variant="light"
            leftSection={copied ? <IconCheck size={14} /> : <IconCopy size={14} />}
            onClick={handleCopy}
            disabled={!message.trim()}
          >
            {copied ? 'Copied!' : 'Copy to clipboard'}
          </Button>
        </Group>
      </div>
    </>
  );
}