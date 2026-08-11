import { ActionIcon, Button, FileButton, Group, Menu, Text } from '@mantine/core';
import { IconFolder, IconPaperclip, IconX } from '@tabler/icons-react';
import { ApiError } from '../api/client';
import { useUploadAttachment, type EmailAttachmentInput } from '../api/emails';
import { useMaterials, type Material } from '../api/materials';
import { formatBytes } from '../format';

interface AttachmentPickerProps {
  /** The attachments chosen so far, owned by the caller — this component holds no list state. */
  value: EmailAttachmentInput[];
  onChange: (next: EmailAttachmentInput[]) => void;
  /** An upload failure, surfaced in whichever Alert the caller already renders. */
  onError: (message: string) => void;
  /** Raised while bytes are in flight so the caller can keep Send disabled. A send that starts
   *  mid-upload would reference an S3 key with nothing behind it yet. */
  onUploadingChange: (uploading: boolean) => void;
}

/**
 * Pick attachments for an outgoing message: upload a new file, or reference a saved material.
 *
 * Shared by the composer and the thread's inline reply. Both need the identical two-source
 * behaviour, and the two ways of attaching are genuinely different operations — `Attach` uploads
 * bytes and gets back a fresh key, while `From materials` reuses the key of an object already in
 * the library, which is the entire reason a library exists. The bytes are copied into the message
 * at send time either way, so replacing a material later never alters mail already sent.
 */
export function AttachmentPicker({
  value,
  onChange,
  onError,
  onUploadingChange,
}: AttachmentPickerProps) {
  const upload = useUploadAttachment();
  const materials = useMaterials();

  async function handleAttach(file: File | null) {
    if (!file) return;
    onUploadingChange(true);
    try {
      const attached = await upload.mutateAsync(file);
      onChange([...value, attached]);
    } catch (err) {
      onError(err instanceof ApiError ? err.message : `Could not attach ${file.name}.`);
    } finally {
      // In `finally` so a failed upload releases the caller's Send button rather than wedging it.
      onUploadingChange(false);
    }
  }

  /** Attach a material by reference — no upload, no copy. Ignored when already attached. */
  function attachMaterial(material: Material) {
    if (value.some((a) => a.s3_key === material.s3_key)) return;
    onChange([
      ...value,
      {
        s3_key: material.s3_key,
        filename: material.name,
        content_type: material.content_type,
        size_bytes: material.size_bytes,
      },
    ]);
  }

  return (
    <Group gap="xs" wrap="wrap">
      {value.map((a) => (
        <Group key={a.s3_key} gap={6} px={8} py={4} bg="gray.1" style={{ borderRadius: 6 }}>
          <Text size="xs">{a.filename}</Text>
          <Text size="xs" c="dimmed">
            {formatBytes(a.size_bytes)}
          </Text>
          <ActionIcon
            size="xs"
            variant="subtle"
            aria-label={`Remove ${a.filename}`}
            onClick={() => onChange(value.filter((x) => x.s3_key !== a.s3_key))}
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

      {/* Only offered when there is a library to offer. An empty menu is worse than no menu,
          and a first-time user has no materials yet. */}
      {(materials.data?.length ?? 0) > 0 && (
        <Menu position="bottom-start" withinPortal>
          <Menu.Target>
            <Button size="xs" variant="light" leftSection={<IconFolder size={14} />}>
              From materials
            </Button>
          </Menu.Target>
          <Menu.Dropdown>
            <Menu.Label>Attach a saved file</Menu.Label>
            {materials.data?.map((material) => {
              const already = value.some((a) => a.s3_key === material.s3_key);
              return (
                <Menu.Item
                  key={material.id}
                  disabled={already}
                  onClick={() => attachMaterial(material)}
                  rightSection={
                    <Text size="xs" c="dimmed">
                      {already ? 'attached' : formatBytes(material.size_bytes)}
                    </Text>
                  }
                >
                  {material.name}
                </Menu.Item>
              );
            })}
          </Menu.Dropdown>
        </Menu>
      )}
    </Group>
  );
}