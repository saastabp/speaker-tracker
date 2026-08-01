import {
  ActionIcon,
  Alert,
  Button,
  FileButton,
  Group,
  Loader,
  Modal,
  Select,
  Stack,
  Text,
  TextInput,
} from '@mantine/core';
import { IconPaperclip, IconX } from '@tabler/icons-react';
import { useEffect, useState } from 'react';
import { ApiError } from '../api/client';
import { useContacts } from '../api/contacts';
import { useSendEmail, useUploadAttachment, type EmailAttachmentInput } from '../api/emails';
import { useSignatures } from '../api/signatures';
import { useTemplates } from '../api/templates';
import { FieldLabel } from './FieldLabel';
import { FollowUpRiderFields, type FollowUpRiderValue } from './FollowUpRiderFields';
import { RichTextField } from './RichTextEditor';
import { fillMerge } from './TemplatePicker';

/** Templates are stored as plain text with `\n`; the composer body is HTML. Blank lines become
 *  paragraphs and single newlines line breaks — the inverse of `common/mail.py`'s html_to_text,
 *  so a template round-trips recognisably rather than collapsing into one run-on paragraph. */
function textToHtml(text: string): string {
  const escape = (s: string) =>
    s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  return text
    .split(/\n{2,}/)
    .map((block) => `<p>${escape(block).split('\n').join('<br>')}</p>`)
    .join('');
}

/** Split a comma- or semicolon-separated address field into individual addresses. */
function splitAddresses(value: string): string[] {
  return value
    .split(/[,;]/)
    .map((a) => a.trim())
    .filter(Boolean);
}

function formatSize(bytes: number | null | undefined): string {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

interface EmailComposerProps {
  opened: boolean;
  onClose: () => void;
  /** Preselected recipient, when composing from a contact or opportunity. Omit to let the user
   *  pick one (opened from the Emails inbox) — the same pattern as `LogOutreachModal`. */
  contactId?: number | null;
  contactName?: string;
  contactEmail?: string | null;
  /** Attribute the send (and its logged outreach) to a gig. */
  opportunityId?: number | null;
  onSent?: (threadId: number) => void;
}

/**
 * Compose and send a new email, opening a thread.
 *
 * **A contact is required.** Not merely a scope choice: `outreaches.contact_id` is NOT NULL, so a
 * send with no contact writes no outreach row and is therefore invisible to the outreach journal,
 * the contact timeline, and the dashboard's touch targets. Email the CRM cannot report on defeats
 * the point of sending it from here. (The nullable `contact_id` on `email_threads`/`email_messages`
 * exists for *inbound* mail from an unknown sender — 6b's drop-folder import — not for outbound.)
 * `To` stays editable so a contact can be reached at a different address, but the link is fixed.
 *
 * The body carries the **signature inline**: the default signature is appended into the editable
 * body when the composer opens, so Donna can edit or delete it before sending. The server never
 * appends one — doing it here is what keeps "what you see is what is sent" true.
 *
 * Not offered, deliberately: **Save draft** (no drafts table exists) and **Schedule follow-up**
 * (`follow_ups` arrives in a later migration). Both appear in the mockup; wiring a control to
 * neither storage nor behaviour would be worse than omitting it.
 */
export function EmailComposer({
  opened,
  onClose,
  contactId,
  contactName,
  contactEmail,
  opportunityId,
  onSent,
}: EmailComposerProps) {
  const templates = useTemplates();
  const signatures = useSignatures();
  const send = useSendEmail();
  const upload = useUploadAttachment();
  // Only fetched when the caller did not name a contact, so opening from a contact page costs
  // nothing extra.
  const contacts = useContacts(undefined, opened && !contactId);

  const [pickedContactId, setPickedContactId] = useState<string | null>(null);
  const [to, setTo] = useState('');
  const [cc, setCc] = useState('');
  const [subject, setSubject] = useState('');
  const [bodyHtml, setBodyHtml] = useState('');
  const [templateId, setTemplateId] = useState<string | null>(null);
  const [attachments, setAttachments] = useState<EmailAttachmentInput[]>([]);
  const [riderOn, setRiderOn] = useState(false);
  const [rider, setRider] = useState<FollowUpRiderValue>({ due_date: '', note: '' });
  const [error, setError] = useState<string | null>(null);
  // Minted per compose and held across retries. If the send fails ambiguously (a timeout, where
  // the mail may already have gone out), pressing Send again reuses this key, so the server
  // recognises a retry and 409s instead of sending the venue a second copy. Regenerated only when
  // the composer reopens on a fresh draft.
  const [idempotencyKey, setIdempotencyKey] = useState(() => crypto.randomUUID());

  const defaultSignature = (signatures.data ?? []).find((s) => s.is_default) ?? null;
  const emailTemplates = (templates.data ?? []).filter((t) => t.channel === 'email');

  const picked = (contacts.data ?? []).find((c) => String(c.id) === pickedContactId) ?? null;
  const effectiveContactId = contactId ?? (picked ? picked.id : null);
  const effectiveContactName = contactId ? (contactName ?? '') : (picked?.name ?? '');

  // Reset to a clean draft each time the composer opens, seeded with the recipient and the
  // signature. Keyed on `opened` so a previous send's content never leaks into the next one.
  useEffect(() => {
    if (!opened) return;
    setPickedContactId(null);
    setTo(contactEmail ?? '');
    setCc('');
    setSubject('');
    setTemplateId(null);
    setAttachments([]);
    setError(null);
    // Off on every open. Acceptance #6 is that sending never silently schedules anything, so a
    // rider left on from a previous compose would be exactly the wrong carry-over.
    setRiderOn(false);
    setRider({ due_date: '', note: '' });
    setIdempotencyKey(crypto.randomUUID()); // a new draft is a new message, not a retry
    setBodyHtml(defaultSignature ? `<p></p>${defaultSignature.body_html}` : '<p></p>');
  }, [opened, contactEmail, defaultSignature?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // Prefill `To` from a contact chosen in the picker. Only when the field is still untouched, so
  // an address the user typed or corrected is never overwritten.
  useEffect(() => {
    if (picked?.email && !to) setTo(picked.email);
  }, [picked?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  function applyTemplate(value: string | null) {
    setTemplateId(value);
    const template = emailTemplates.find((t) => String(t.id) === value);
    if (!template) return;

    const mergeName = effectiveContactName.trim() || 'there';
    if (template.subject) setSubject(fillMerge(template.subject, mergeName));
    // `[Your signature]` is dropped rather than merged: the composer appends the real signature
    // below, so leaving the placeholder would put both in the message.
    const body = fillMerge(template.body, mergeName).split('[Your signature]').join('').trimEnd();
    setBodyHtml(
      defaultSignature ? `${textToHtml(body)}${defaultSignature.body_html}` : textToHtml(body),
    );
  }

  async function handleAttach(file: File | null) {
    if (!file) return;
    setError(null);
    try {
      const attached = await upload.mutateAsync(file);
      setAttachments((current) => [...current, attached]);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : `Could not attach ${file.name}.`);
    }
  }

  const recipients = splitAddresses(to);
  const canSend =
    effectiveContactId !== null &&
    recipients.length > 0 &&
    subject.trim().length > 0 &&
    !send.isPending &&
    !upload.isPending;

  // Why Send is disabled, in the order the fields appear. A dead button with no explanation reads
  // as a broken app: filling in the recipient is the obvious move, and it alone is not enough.
  const blockedBecause = (): string | null => {
    if (effectiveContactId === null) return 'Choose a contact to send.';
    if (recipients.length === 0) return 'Add a recipient to send.';
    if (subject.trim().length === 0) return 'Add a subject to send.';
    if (upload.isPending) return 'Waiting for the attachment to finish uploading…';
    return null;
  };
  const blocker = blockedBecause();

  async function handleSend() {
    setError(null);
    if (riderOn && !rider.due_date) {
      setError('Pick a date for the follow-up, or switch it off.');
      return;
    }
    try {
      const result = await send.mutateAsync({
        idempotency_key: idempotencyKey,
        follow_up: riderOn ? { due_date: rider.due_date, note: rider.note.trim() || null } : null,
        to: recipients,
        cc: splitAddresses(cc),
        subject: subject.trim(),
        body_html: bodyHtml,
        contact_id: effectiveContactId,
        opportunity_id: opportunityId ?? null,
        message_template_id: templateId ? Number(templateId) : null,
        attachments,
      });
      onSent?.(result.thread_id);
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not send the email.');
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Compose email" size="xl" centered>
      <Stack gap="sm">
        {contactId ? (
          <div>
            <FieldLabel>Contact</FieldLabel>
            <Text size="sm" fw={600}>
              {contactName || `Contact #${contactId}`}
            </Text>
          </div>
        ) : (
          <div>
            <FieldLabel helper="required — the send is logged as a touch against this contact">
              Contact
            </FieldLabel>
            <Select
              value={pickedContactId}
              onChange={setPickedContactId}
              data={(contacts.data ?? []).map((c) => ({
                value: String(c.id),
                label: c.email ? `${c.name} — ${c.email}` : c.name,
              }))}
              placeholder={contacts.isLoading ? 'Loading…' : 'Choose a contact'}
              searchable
              nothingFoundMessage="No contacts"
            />
          </div>
        )}

        <div>
          <FieldLabel helper="required">To</FieldLabel>
          <TextInput
            value={to}
            onChange={(e) => setTo(e.currentTarget.value)}
            placeholder="venue@example.com"
          />
        </div>

        <div>
          <FieldLabel helper="optional">Cc</FieldLabel>
          <TextInput value={cc} onChange={(e) => setCc(e.currentTarget.value)} />
        </div>

        <div>
          <FieldLabel helper="required">Subject</FieldLabel>
          <TextInput value={subject} onChange={(e) => setSubject(e.currentTarget.value)} />
        </div>

        <div>
          <FieldLabel helper="optional">Template</FieldLabel>
          <Select
            value={templateId}
            onChange={applyTemplate}
            data={emailTemplates.map((t) => ({ value: String(t.id), label: t.name }))}
            placeholder={templates.isLoading ? 'Loading…' : 'Start from a template'}
            clearable
          />
        </div>

        <div>
          <FieldLabel>Message</FieldLabel>
          {signatures.isLoading ? (
            <Loader size="sm" />
          ) : (
            <RichTextField value={bodyHtml} onChange={setBodyHtml} />
          )}
        </div>

        <Group gap="xs" wrap="wrap">
          {attachments.map((a) => (
            <Group key={a.s3_key} gap={6} px={8} py={4} bg="gray.1" style={{ borderRadius: 6 }}>
              <Text size="xs">{a.filename}</Text>
              <Text size="xs" c="dimmed">
                {formatSize(a.size_bytes)}
              </Text>
              <ActionIcon
                size="xs"
                variant="subtle"
                aria-label={`Remove ${a.filename}`}
                onClick={() =>
                  setAttachments((current) => current.filter((x) => x.s3_key !== a.s3_key))
                }
              >
                <IconX size={12} />
              </ActionIcon>
            </Group>
          ))}
          <FileButton onChange={handleAttach}>
            {(props) => (
              <Button
                {...props}
                size="xs"
                variant="light"
                leftSection={<IconPaperclip size={14} />}
                loading={upload.isPending}
              >
                Attach
              </Button>
            )}
          </FileButton>
        </Group>

        {error && (
          <Alert color="red" variant="light">
            {error}
          </Alert>
        )}

        <FollowUpRiderFields
          enabled={riderOn}
          onEnabledChange={setRiderOn}
          value={rider}
          onChange={setRider}
          description="Pick a date to be reminded to chase this email."
        />

        <Group justify="space-between" mt="xs">
          <Text size="xs" c={blocker ? 'orange.7' : 'dimmed'}>
            {blocker ?? 'Sends via WorkMail (SES) and logs an outreach touch.'}
          </Text>
          <Group gap="xs">
            <Button variant="subtle" onClick={onClose} disabled={send.isPending}>
              Cancel
            </Button>
            <Button onClick={handleSend} disabled={!canSend} loading={send.isPending}>
              Send
            </Button>
          </Group>
        </Group>
      </Stack>
    </Modal>
  );
}