import {
  ActionIcon,
  Alert,
  Badge,
  Box,
  Button,
  Card,
  FileButton,
  Group,
  Loader,
  Menu,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import {
  IconAlertTriangle,
  IconDotsVertical,
  IconDownload,
  IconPencil,
  IconPlus,
  IconTrash,
  IconUpload,
} from '@tabler/icons-react';
import { useRef, useState } from 'react';
import {
  useDeleteMaterial,
  useMaterialUrl,
  useMaterials,
  useReplaceMaterialFile,
  useUploadMaterial,
  type Material,
} from '../api/materials';
import {
  useCreateTalk,
  useDeleteTalk,
  useTalks,
  useUpdateTalk,
  type TalkInput,
  type TalkSummary,
} from '../api/talks';
import { MaterialPreview } from '../components/MaterialPreview';
import { TalkFormModal } from '../components/TalkFormModal';
import { timestampDate } from '../dates';
import { formatBytes } from '../format';
import { BRAND_LINE, BRAND_PANEL } from '../theme';

/** Short badge for a file's kind, from its type or extension. Cosmetic — the preview decides for
 *  itself what it can actually render. */
function fileKind(material: Material): string {
  const type = material.content_type.toLowerCase();
  if (type === 'application/pdf') return 'PDF';
  if (type.startsWith('image/')) return 'IMG';
  if (type.includes('word') || /\.docx?$/i.test(material.name)) return 'DOC';
  if (type.includes('zip') || /\.zip$/i.test(material.name)) return 'ZIP';
  if (type.startsWith('audio/')) return 'AUD';
  if (type.startsWith('video/')) return 'VID';
  if (type.startsWith('text/') || /\.(md|markdown|txt)$/i.test(material.name)) return 'TXT';
  return 'FILE';
}

/** One talk in the menu, with its edit/remove actions. */
function TalkCard({
  talk,
  onEdit,
  onRemove,
}: {
  talk: TalkSummary;
  onEdit: () => void;
  onRemove: () => void;
}) {
  return (
    <Card withBorder radius="md" padding="md" h="100%" style={{ borderColor: BRAND_LINE }}>
      <Group justify="space-between" wrap="nowrap" align="flex-start">
        <Text fw={650} c="navy.9" lineClamp={2}>
          {talk.title}
        </Text>
        <Menu position="bottom-end" withinPortal>
          <Menu.Target>
            <ActionIcon variant="subtle" color="gray" aria-label={`Actions for ${talk.title}`}>
              <IconDotsVertical size={16} />
            </ActionIcon>
          </Menu.Target>
          <Menu.Dropdown>
            <Menu.Item leftSection={<IconPencil size={14} />} onClick={onEdit}>
              Edit
            </Menu.Item>
            <Menu.Item color="red" leftSection={<IconTrash size={14} />} onClick={onRemove}>
              Remove
            </Menu.Item>
          </Menu.Dropdown>
        </Menu>
      </Group>

      {talk.duration && (
        <Badge variant="light" color="gray" size="sm" mt={6}>
          {talk.duration}
        </Badge>
      )}

      {talk.one_liner && (
        <Text size="sm" c="dimmed" mt="sm">
          {talk.one_liner}
        </Text>
      )}
    </Card>
  );
}

export function TalksMaterials() {
  const talks = useTalks();
  const materials = useMaterials();
  const createTalk = useCreateTalk();
  const deleteTalk = useDeleteTalk();
  const [editingTalk, setEditingTalk] = useState<TalkSummary | null>(null);
  const [talkOpen, talkHandlers] = useDisclosure(false);
  const updateTalk = useUpdateTalk(editingTalk?.id ?? 0);

  const upload = useUploadMaterial();
  const replaceFile = useReplaceMaterialFile();
  const removeMaterial = useDeleteMaterial();
  const materialUrl = useMaterialUrl();
  const [previewing, setPreviewing] = useState<Material | null>(null);
  const [previewOpen, previewHandlers] = useDisclosure(false);
  const [fileError, setFileError] = useState<string | null>(null);
  // Which material a replacement picker is aimed at — FileButton has no per-row identity of its
  // own, so the row records its target before opening the chooser.
  const replaceTarget = useRef<Material | null>(null);
  const replaceReset = useRef<(() => void) | null>(null);

  function openCreateTalk() {
    setEditingTalk(null);
    talkHandlers.open();
  }

  function openEditTalk(talk: TalkSummary) {
    setEditingTalk(talk);
    talkHandlers.open();
  }

  async function handleTalkSubmit(values: TalkInput) {
    if (editingTalk) {
      await updateTalk.mutateAsync(values);
    } else {
      await createTalk.mutateAsync(values);
    }
  }

  async function handleRemoveTalk(talk: TalkSummary) {
    // A removed talk keeps its title on gigs that already reference it, so this is not as
    // destructive as it reads — but it does disappear from the opportunity picker.
    if (!window.confirm(`Remove “${talk.title}”? Gigs already using it keep its name.`)) return;
    await deleteTalk.mutateAsync(talk.id);
  }

  async function handleUpload(file: File | null) {
    if (!file) return;
    setFileError(null);
    try {
      await upload.mutateAsync({ file });
    } catch (err) {
      setFileError(err instanceof Error ? err.message : 'Upload failed.');
    }
  }

  async function handleReplace(file: File | null) {
    const target = replaceTarget.current;
    replaceReset.current?.();
    if (!file || !target) return;
    setFileError(null);
    // Confirmed because overwriting cannot be undone from here. It is safe for anything already
    // sent — an attached material is copied into the message — but the previous file is gone.
    if (
      !window.confirm(
        `Replace “${target.name}” with “${file.name}”?\n\n` +
          'Emails already sent keep the version they went out with. The current file is replaced.',
      )
    ) {
      return;
    }
    try {
      await replaceFile.mutateAsync({ id: target.id, file });
    } catch (err) {
      setFileError(err instanceof Error ? err.message : 'Replacement failed.');
    }
  }

  async function handleDownload(material: Material) {
    setFileError(null);
    try {
      const { url } = await materialUrl.mutateAsync({ id: material.id, download: true });
      // The signed URL carries Content-Disposition: attachment, so navigating to it saves the file
      // rather than replacing the app.
      window.location.href = url;
    } catch {
      setFileError('Could not prepare that download.');
    }
  }

  async function handleRemoveMaterial(material: Material) {
    if (!window.confirm(`Remove “${material.name}” from your materials?`)) return;
    setFileError(null);
    try {
      await removeMaterial.mutateAsync(material.id);
    } catch {
      setFileError('Could not remove that material.');
    }
  }

  const talkList = talks.data ?? [];
  const materialList = materials.data ?? [];
  const busy = upload.isPending || replaceFile.isPending;

  return (
    <Stack>
      <Group justify="space-between" align="flex-start">
        <div>
          <Title order={2} c="navy.9">
            Talks &amp; Materials
          </Title>
          <Text c="dimmed" size="sm">
            The Guest Workshop menu — attach these to any outreach
          </Text>
        </div>
        <Button leftSection={<IconPlus size={16} />} onClick={openCreateTalk}>
          Add talk
        </Button>
      </Group>

      {talks.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={18} />}>
          {talks.error.message}
        </Alert>
      )}
      {talks.isLoading && (
        <Group>
          <Loader size="sm" />
          <Text>Loading talks…</Text>
        </Group>
      )}

      {talks.data && talkList.length === 0 && (
        <Text c="dimmed">No talks yet. Add the offers you pitch, and they appear on every gig.</Text>
      )}

      {talkList.length > 0 && (
        <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }}>
          {talkList.map((talk) => (
            <TalkCard
              key={talk.id}
              talk={talk}
              onEdit={() => openEditTalk(talk)}
              onRemove={() => void handleRemoveTalk(talk)}
            />
          ))}
        </SimpleGrid>
      )}

      {/* Materials */}
      <Card withBorder radius="md" padding={0} mt="md" style={{ borderColor: BRAND_LINE }}>
        <Group
          justify="space-between"
          px="md"
          py="sm"
          style={{ borderBottom: `1px solid ${BRAND_LINE}` }}
        >
          <Text fw={650} fz={13}>
            Materials
          </Text>
          <FileButton onChange={(file) => void handleUpload(file)}>
            {(props) => (
              <Button
                {...props}
                size="xs"
                variant="default"
                loading={upload.isPending}
                leftSection={<IconUpload size={14} />}
              >
                Upload
              </Button>
            )}
          </FileButton>
        </Group>

        <Box p="md">
          {fileError && (
            <Alert color="red" variant="light" mb="sm" icon={<IconAlertTriangle size={18} />}>
              {fileError}
            </Alert>
          )}
          {materials.isError && (
            <Alert color="red" icon={<IconAlertTriangle size={18} />}>
              {materials.error.message}
            </Alert>
          )}

          {materials.isLoading && (
            <Group>
              <Loader size="sm" />
              <Text size="sm">Loading materials…</Text>
            </Group>
          )}

          {materials.data && materialList.length === 0 && (
            <Text c="dimmed" size="sm">
              Nothing here yet — upload a one-sheet or a speaker menu and it becomes attachable from
              the composer.
            </Text>
          )}

          <Stack gap={0}>
            {materialList.map((material, index) => (
              <Group
                key={material.id}
                justify="space-between"
                wrap="nowrap"
                py="sm"
                style={index > 0 ? { borderTop: `1px solid ${BRAND_LINE}` } : undefined}
              >
                <Group gap="sm" wrap="nowrap" style={{ minWidth: 0 }}>
                  <Badge
                    variant="light"
                    color="gray"
                    radius="sm"
                    style={{ backgroundColor: BRAND_PANEL, flexShrink: 0, width: 46 }}
                  >
                    {fileKind(material)}
                  </Badge>
                  <div style={{ minWidth: 0 }}>
                    <Text
                      fw={600}
                      size="sm"
                      lineClamp={1}
                      style={{ cursor: 'pointer' }}
                      onClick={() => {
                        setPreviewing(material);
                        previewHandlers.open();
                      }}
                    >
                      {material.name}
                    </Text>
                    <Text size="xs" c="dimmed">
                      {formatBytes(material.size_bytes)} · updated{' '}
                      {timestampDate(material.updated_at)}
                    </Text>
                  </div>
                </Group>

                <Group gap={4} wrap="nowrap">
                  <Button
                    size="xs"
                    variant="subtle"
                    leftSection={<IconDownload size={14} />}
                    onClick={() => void handleDownload(material)}
                  >
                    Download
                  </Button>
                  <Menu position="bottom-end" withinPortal>
                    <Menu.Target>
                      <ActionIcon
                        variant="subtle"
                        color="gray"
                        aria-label={`Actions for ${material.name}`}
                      >
                        <IconDotsVertical size={16} />
                      </ActionIcon>
                    </Menu.Target>
                    <Menu.Dropdown>
                      <FileButton
                        onChange={(file) => void handleReplace(file)}
                        resetRef={replaceReset}
                      >
                        {(props) => (
                          <Menu.Item
                            leftSection={<IconUpload size={14} />}
                            closeMenuOnClick={false}
                            onClick={() => {
                              // Record which row the chooser is for before opening it — FileButton
                              // has no per-row identity, so the handler would not know otherwise.
                              replaceTarget.current = material;
                              props.onClick();
                            }}
                          >
                            Replace file…
                          </Menu.Item>
                        )}
                      </FileButton>
                      <Menu.Item
                        color="red"
                        leftSection={<IconTrash size={14} />}
                        onClick={() => void handleRemoveMaterial(material)}
                      >
                        Remove
                      </Menu.Item>
                    </Menu.Dropdown>
                  </Menu>
                </Group>
              </Group>
            ))}
          </Stack>

          {busy && (
            <Group mt="sm">
              <Loader size="xs" />
              <Text size="xs" c="dimmed">
                Uploading…
              </Text>
            </Group>
          )}
        </Box>
      </Card>

      <TalkFormModal
        opened={talkOpen}
        onClose={talkHandlers.close}
        title={editingTalk ? 'Edit talk' : 'Add talk'}
        submitLabel={editingTalk ? 'Save talk' : 'Add talk'}
        initialValues={editingTalk ?? undefined}
        onSubmit={handleTalkSubmit}
      />
      <MaterialPreview
        material={previewing}
        opened={previewOpen}
        onClose={previewHandlers.close}
      />
    </Stack>
  );
}