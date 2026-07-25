import { Alert, Button, Card, Group, Loader, Stack, Text, TextInput, Title } from '@mantine/core';
import { useEffect, useState } from 'react';
import { ApiError } from '../api/client';
import { useCreateSignature, useSignatures, useUpdateSignature } from '../api/signatures';
import { CardTitle } from '../components/detailCards';
import { FieldLabel } from '../components/FieldLabel';
import { RichTextField } from '../components/RichTextEditor';

export function Emails() {
  const signatures = useSignatures();
  const create = useCreateSignature();
  const [name, setName] = useState('Default');
  const [bodyHtml, setBodyHtml] = useState('');
  const [error, setError] = useState<string | null>(null);

  // The single default signature (or the first one) is what we edit.
  const list = signatures.data ?? [];
  const current = list.find((s) => s.is_default) ?? list[0] ?? null;
  const update = useUpdateSignature(current?.id ?? 0);

  // Seed the form when the stored signature (identity) loads/changes.
  useEffect(() => {
    if (current) {
      setName(current.name);
      setBodyHtml(current.body_html);
    }
  }, [current?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const saving = create.isPending || update.isPending;
  const isEmpty = !bodyHtml || bodyHtml === '<p></p>';

  async function handleSave() {
    setError(null);
    const payload = { name: name.trim() || 'Default', body_html: bodyHtml, is_default: true };
    try {
      if (current) {
        await update.mutateAsync(payload);
      } else {
        await create.mutateAsync(payload);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong.');
    }
  }

  return (
    <Stack>
      <div>
        <Title order={2} c="navy.9">
          Emails
        </Title>
        <Text c="dimmed" size="sm">
          Your inbox and composer arrive with the email send path. For now, set the signature the
          composer will append to outgoing mail.
        </Text>
      </div>

      <Card withBorder radius="md">
        <CardTitle>Signature</CardTitle>
        {signatures.isPending ? (
          <Loader size="sm" />
        ) : (
          <Stack>
            {error && (
              <Alert color="red" variant="light">
                {error}
              </Alert>
            )}
            <div>
              <FieldLabel>Name</FieldLabel>
              <TextInput value={name} onChange={(e) => setName(e.currentTarget.value)} maw={320} />
            </div>
            <div>
              <FieldLabel helper="styled — appended to outgoing email">Signature</FieldLabel>
              <RichTextField value={bodyHtml} onChange={setBodyHtml} />
            </div>
            <Group>
              <Button onClick={handleSave} loading={saving} disabled={isEmpty}>
                Save signature
              </Button>
            </Group>
          </Stack>
        )}
      </Card>
    </Stack>
  );
}