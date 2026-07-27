import { ColorInput, FileButton, Popover, Select } from '@mantine/core';
import { Link, RichTextEditor as MantineRichTextEditor } from '@mantine/tiptap';
import Image from '@tiptap/extension-image';
import { IconPaint, IconPhoto } from '@tabler/icons-react';
import Subscript from '@tiptap/extension-subscript';
import Superscript from '@tiptap/extension-superscript';
import TextAlign from '@tiptap/extension-text-align';
import { BackgroundColor, Color, FontSize, TextStyle } from '@tiptap/extension-text-style';
import Underline from '@tiptap/extension-underline';
import { useEditor, type Editor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import { useEffect, useState } from 'react';
import { BRAND_GOLD, BRAND_MUTED, BRAND_NAVY, BRAND_TERRACOTTA } from '../theme';

/** Text colours offered in the toolbar — the brand palette, in the roles the brand guide assigns:
 *  navy for headline text, terracotta for bold inline emphasis, gold for highlight/accent, and
 *  warm grey for body copy (softer than pure black). Black is included because a signature
 *  sometimes wants it and the guide's warm grey is a deliberate default, not a prohibition. */
const SWATCHES = [BRAND_NAVY, BRAND_TERRACOTTA, BRAND_GOLD, BRAND_MUTED, '#000000'];

/** Longest edge the stored **bitmap** is scaled down to, in CSS pixels.
 *
 *  This is resolution, not display size — the two are deliberately different. Keeping the bitmap
 *  larger than it is shown is what makes it crisp on high-DPI screens; 800 leaves roughly 3x
 *  headroom over a typical signature logo while keeping the base64 payload — which rides in
 *  `signatures.body_html` and in every message carrying the signature — to a sane size. */
const MAX_IMAGE_WIDTH_PX = 800;

/** Width an image is *displayed* at when first inserted.
 *
 *  Separate from the bitmap width above: inserting at full bitmap size is how a logo ends up
 *  800px wide in the signature. Donna's current signature shows its logo at roughly 265px, so
 *  this lands close to right immediately, and the drag handle adjusts from there. */
const DEFAULT_IMAGE_DISPLAY_WIDTH_PX = 280;

/** Smallest width a resize drag can produce, so an image cannot be shrunk to an unclickable speck. */
const MIN_IMAGE_WIDTH_PX = 40;

/** Refuse an image whose encoded data URI exceeds this. A signature repeats on every send, so a
 *  heavy logo is a per-email cost, not a one-off. */
const MAX_IMAGE_BYTES = 200 * 1024;

/** Sizes offered in the toolbar. px is what email clients handle reliably. */
const FONT_SIZES = ['12px', '14px', '16px', '18px', '24px', '32px'];

/**
 * Read an image file, downscale the bitmap, and return it with the size to *display* it at.
 *
 * Returns two different widths on purpose: the data URI holds a bitmap of up to
 * {@link MAX_IMAGE_WIDTH_PX}, while `width`/`height` are the smaller
 * {@link DEFAULT_IMAGE_DISPLAY_WIDTH_PX} the reader sees. Inserting at bitmap size is what made a
 * logo arrive 800px wide.
 */
async function fileToScaledDataUri(
  file: File,
): Promise<{ src: string; width: number; height: number }> {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, MAX_IMAGE_WIDTH_PX / bitmap.width);
  const bitmapWidth = Math.round(bitmap.width * scale);
  const bitmapHeight = Math.round(bitmap.height * scale);

  const canvas = document.createElement('canvas');
  canvas.width = bitmapWidth;
  canvas.height = bitmapHeight;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('could not read the image');
  ctx.drawImage(bitmap, 0, 0, bitmapWidth, bitmapHeight);

  // PNG keeps logo edges crisp and preserves transparency; JPEG would halo a logo against white.
  const src = canvas.toDataURL('image/png');
  if (src.length > MAX_IMAGE_BYTES) {
    throw new Error(
      `Image is too large once encoded (${Math.round(src.length / 1024)} KB, max ${
        MAX_IMAGE_BYTES / 1024
      } KB).`,
    );
  }

  // Never upscale a small image just to hit the default width.
  const width = Math.min(DEFAULT_IMAGE_DISPLAY_WIDTH_PX, bitmapWidth);
  const height = Math.round((bitmapHeight * width) / bitmapWidth);
  return { src, width, height };
}

function ImageControl({ editor }: { editor: Editor | null }) {
  const [error, setError] = useState<string | null>(null);

  async function handleFile(file: File | null) {
    if (!file || !editor) return;
    setError(null);
    try {
      const { src, width, height } = await fileToScaledDataUri(file);
      editor
        .chain()
        .focus()
        .setImage({ src, width, height } as never)
        .run();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not insert that image.');
    }
  }

  return (
    <Popover opened={Boolean(error)} onClose={() => setError(null)} position="bottom" withArrow>
      <Popover.Target>
        <FileButton onChange={handleFile} accept="image/png,image/jpeg,image/gif,image/webp">
          {(props) => (
            <MantineRichTextEditor.Control {...props} aria-label="Insert image" title="Insert image">
              <IconPhoto size={16} />
            </MantineRichTextEditor.Control>
          )}
        </FileButton>
      </Popover.Target>
      <Popover.Dropdown>{error}</Popover.Dropdown>
    </Popover>
  );
}

function FontSizeControl({ editor }: { editor: Editor | null }) {
  const current: string | null = editor?.getAttributes('textStyle').fontSize ?? null;
  return (
    <Select
      size="xs"
      w={92}
      placeholder="Size"
      data={FONT_SIZES}
      value={current}
      onChange={(value) => {
        if (!editor) return;
        if (value) {
          editor.chain().focus().setFontSize(value).run();
        } else {
          editor.chain().focus().unsetFontSize().run();
        }
      }}
      clearable
      comboboxProps={{ withinPortal: true }}
    />
  );
}

function BackgroundColorControl({ editor }: { editor: Editor | null }) {
  const current: string = editor?.getAttributes('textStyle').backgroundColor ?? '';
  return (
    <Popover position="bottom" withArrow>
      <Popover.Target>
        <MantineRichTextEditor.Control aria-label="Background colour" title="Background colour">
          <IconPaint size={16} />
        </MantineRichTextEditor.Control>
      </Popover.Target>
      <Popover.Dropdown>
        <ColorInput
          size="xs"
          w={200}
          format="hex"
          value={current}
          onChangeEnd={(value) => editor?.chain().focus().setBackgroundColor(value).run()}
        />
      </Popover.Dropdown>
    </Popover>
  );
}

interface RichTextFieldProps {
  /** Current value as an HTML string. */
  value: string;
  /** Called with the editor's HTML on every change. */
  onChange: (html: string) => void;
  /** Optional placeholder shown when empty (rendered by the caller; kept for parity). */
  placeholder?: string;
}

/**
 * A styled rich-text editor (Mantine + Tiptap) that reads and writes an HTML string. Shared by the
 * signature editor and the email composer, so the toolbar and extensions live in one place.
 *
 * Everything here is chosen for **email**, not web rendering:
 *
 * - colour and background emit inline `style` attributes. `BackgroundColor` is used rather than
 *   the Highlight extension, which emits `<mark>` — a tag many email clients leave unstyled;
 * - images carry explicit `width`/`height` attributes for Outlook's Word engine;
 * - inserted images are `data:` URIs, which `common/mail.py` converts to `cid:` inline parts at
 *   send time. Gmail strips `data:` images and Outlook will not render them, so they must never
 *   reach the wire — but they are exactly what a browser renders natively here and in the thread
 *   view, with no resolution plumbing and no environment-specific URL baked into stored HTML.
 */
export function RichTextField({ value, onChange }: RichTextFieldProps) {
  const editor = useEditor({
    extensions: [
      StarterKit,
      Underline,
      Link,
      TextStyle,
      Color,
      FontSize,
      BackgroundColor,
      Subscript,
      Superscript,
      TextAlign.configure({ types: ['heading', 'paragraph'] }),
      // `allowBase64` is load-bearing, not a nicety: it defaults to FALSE, which makes the parse
      // rule `img[src]:not([src^="data:"])`, so a stored data: image is **silently dropped** when
      // HTML is loaded back in. Inserting still looks fine — setImage builds the node directly and
      // never goes through the parser — so the loss only appears on reload, with no error. Every
      // image here is a data: URI, so turning this off would discard them.
      //
      // `resize` gives the drag handles; v3's Image already carries width/height attributes and a
      // resizable node view, so neither needs hand-rolling.
      Image.configure({
        inline: false,
        allowBase64: true,
        resize: { enabled: true, minWidth: MIN_IMAGE_WIDTH_PX, alwaysPreserveAspectRatio: true },
      }),
    ],
    content: value,
    onUpdate: ({ editor }) => onChange(editor.getHTML()),
  });

  // Sync an externally-changed value into the editor (e.g. once the stored signature loads), but
  // never while the user is typing — replacing content mid-edit would clobber the cursor.
  useEffect(() => {
    if (editor && !editor.isFocused && value !== editor.getHTML()) {
      editor.commands.setContent(value, { emitUpdate: false });
    }
  }, [value, editor]);

  return (
    <MantineRichTextEditor editor={editor}>
      <MantineRichTextEditor.Toolbar sticky>
        <MantineRichTextEditor.ControlsGroup>
          <MantineRichTextEditor.Bold />
          <MantineRichTextEditor.Italic />
          <MantineRichTextEditor.Underline />
          <MantineRichTextEditor.Strikethrough />
          <MantineRichTextEditor.ClearFormatting />
        </MantineRichTextEditor.ControlsGroup>

        <MantineRichTextEditor.ControlsGroup>
          <MantineRichTextEditor.H2 />
          <MantineRichTextEditor.H3 />
          <MantineRichTextEditor.Blockquote />
        </MantineRichTextEditor.ControlsGroup>

        <MantineRichTextEditor.ControlsGroup>
          <FontSizeControl editor={editor} />
        </MantineRichTextEditor.ControlsGroup>

        <MantineRichTextEditor.ControlsGroup>
          <MantineRichTextEditor.ColorPicker colors={SWATCHES} />
          <MantineRichTextEditor.UnsetColor />
          <BackgroundColorControl editor={editor} />
        </MantineRichTextEditor.ControlsGroup>

        <MantineRichTextEditor.ControlsGroup>
          <MantineRichTextEditor.BulletList />
          <MantineRichTextEditor.OrderedList />
        </MantineRichTextEditor.ControlsGroup>

        <MantineRichTextEditor.ControlsGroup>
          <MantineRichTextEditor.AlignLeft />
          <MantineRichTextEditor.AlignCenter />
          <MantineRichTextEditor.AlignRight />
        </MantineRichTextEditor.ControlsGroup>

        <MantineRichTextEditor.ControlsGroup>
          <MantineRichTextEditor.Link />
          <MantineRichTextEditor.Unlink />
          <ImageControl editor={editor} />
          <MantineRichTextEditor.Hr />
        </MantineRichTextEditor.ControlsGroup>

        <MantineRichTextEditor.ControlsGroup>
          <MantineRichTextEditor.Undo />
          <MantineRichTextEditor.Redo />
        </MantineRichTextEditor.ControlsGroup>
      </MantineRichTextEditor.Toolbar>
      <MantineRichTextEditor.Content />
    </MantineRichTextEditor>
  );
}