import { Alert, Anchor, Box, Code, Group, Loader, Modal, Text } from '@mantine/core';
import { IconAlertTriangle, IconDownload } from '@tabler/icons-react';
import { useEffect, useState } from 'react';
import { type Material, useMaterialUrl } from '../api/materials';
import { formatBytes } from '../format';
import { BRAND_LINE } from '../theme';

/**
 * What a material can be shown as, from its content type.
 *
 * `none` is a real answer, not a failure. A .docx has no browser-native rendering — showing it
 * would mean shipping a converter — and media and archives are not viewable in any useful sense.
 * Saying so plainly beats an empty frame that looks broken.
 */
type PreviewKind = 'image' | 'pdf' | 'text' | 'none';

/** Markdown arrives under several types depending on what set it, and often as octet-stream. */
function previewKind(material: Material): PreviewKind {
  const type = material.content_type.toLowerCase();
  if (type.startsWith('image/')) return 'image';
  if (type === 'application/pdf') return 'pdf';
  if (type.startsWith('text/')) return 'text';
  // Browsers frequently report no useful type for .md; fall back to the extension rather than
  // refusing to preview a file that is plainly text.
  if (/\.(md|markdown|txt)$/i.test(material.name)) return 'text';
  return 'none';
}

/**
 * Preview one material in place.
 *
 * **Everything here renders from the presigned S3 URL, never from bytes inlined into our DOM.**
 * `<img>` and `<iframe>` point straight at S3, which is a different origin and so cannot reach the
 * ID token this app holds in memory. The text branch does fetch the body — that is why the bucket
 * allows cross-origin GET — but it is rendered as *text* inside `<Code>`, never as markup, so a
 * file containing `<script>` shows those characters instead of running them.
 */
export function MaterialPreview({
  material,
  opened,
  onClose,
}: {
  material: Material | null;
  opened: boolean;
  onClose: () => void;
}) {
  const materialUrl = useMaterialUrl();
  const [url, setUrl] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const kind = material ? previewKind(material) : 'none';

  useEffect(() => {
    if (!opened || !material) {
      setUrl(null);
      setText(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    // Asked for at the moment of use: the URL expires in minutes, so caching it with the listing
    // would hand out links that have already gone stale.
    materialUrl
      .mutateAsync({ id: material.id })
      .then(async ({ url: signed }) => {
        if (cancelled) return;
        setUrl(signed);
        if (previewKind(material) === 'text') {
          const response = await fetch(signed);
          if (!response.ok) throw new Error(`Could not read the file (${response.status}).`);
          const body = await response.text();
          if (!cancelled) setText(body);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load a preview.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [opened, material?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Modal opened={opened} onClose={onClose} title={material?.name ?? 'Preview'} size="xl">
      {material && (
        <Text size="xs" c="dimmed" mb="sm">
          {material.content_type} · {formatBytes(material.size_bytes)}
        </Text>
      )}

      {loading && (
        <Group>
          <Loader size="sm" />
          <Text size="sm">Loading preview…</Text>
        </Group>
      )}

      {error && (
        <Alert color="red" variant="light" icon={<IconAlertTriangle size={18} />}>
          {error}
        </Alert>
      )}

      {!loading && !error && kind === 'none' && (
        <Alert color="gray" variant="light">
          <Text size="sm">
            No preview for this kind of file — download it to view.
          </Text>
          {url && (
            <Anchor href={url} target="_blank" rel="noreferrer" size="sm" mt="xs" display="block">
              <Group gap={4}>
                <IconDownload size={14} />
                Open {material?.name}
              </Group>
            </Anchor>
          )}
        </Alert>
      )}

      {!loading && !error && url && kind === 'image' && (
        <Box style={{ textAlign: 'center' }}>
          <img
            src={url}
            alt={material?.name ?? ''}
            style={{ maxWidth: '100%', maxHeight: '70vh', borderRadius: 8 }}
          />
        </Box>
      )}

      {!loading && !error && url && kind === 'pdf' && (
        <Box
          component="iframe"
          src={url}
          title={material?.name ?? 'PDF preview'}
          style={{ width: '100%', height: '70vh', border: `1px solid ${BRAND_LINE}`, borderRadius: 8 }}
        />
      )}

      {!loading && !error && text !== null && kind === 'text' && (
        // Rendered as text, deliberately. Turning markdown into HTML here would put a file's
        // contents into our own DOM, which is the one thing the presigned-URL approach avoids.
        <Code
          block
          style={{ maxHeight: '70vh', overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 13 }}
        >
          {text}
        </Code>
      )}
    </Modal>
  );
}