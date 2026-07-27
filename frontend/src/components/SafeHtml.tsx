import DOMPurify from 'dompurify';
import { useMemo } from 'react';

/**
 * Render email HTML that may have been authored by someone else.
 *
 * **Inbound message bodies are third-party content.** They arrive from whoever emailed Donna, are
 * stored as raw MIME, and are handed back to the browser verbatim. Rendering them with
 * `dangerouslySetInnerHTML` unsanitised would let a venue's reply run script in her session —
 * where her ID token lives. Every email body therefore goes through DOMPurify first, outbound
 * included: our own HTML is trusted, but routing it down a second, unsanitised path just creates
 * a way to get that wrong later.
 *
 * The allowlist keeps what a rich-text email actually uses and drops the rest. Notably absent:
 * `<script>`, `<iframe>`, `<object>`, `<form>`, and every `on*` handler — DOMPurify strips those
 * by default, and `FORBID_TAGS`/`FORBID_ATTR` restate the ones worth being explicit about.
 * `<img>` is allowed but see the note on remote content below.
 */
const ALLOWED_TAGS = [
  'a',
  'b',
  'blockquote',
  'br',
  'code',
  'div',
  'em',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
  'hr',
  'i',
  'img',
  'li',
  'ol',
  'p',
  'pre',
  's',
  'span',
  'strong',
  'sub',
  'sup',
  'table',
  'tbody',
  'td',
  'tfoot',
  'th',
  'thead',
  'tr',
  'u',
  'ul',
];

const ALLOWED_ATTR = ['href', 'src', 'alt', 'title', 'width', 'height', 'style', 'colspan', 'rowspan'];

interface SafeHtmlProps {
  html: string;
  className?: string;
}

export function SafeHtml({ html, className }: SafeHtmlProps) {
  const clean = useMemo(
    () =>
      DOMPurify.sanitize(html, {
        ALLOWED_TAGS,
        ALLOWED_ATTR,
        FORBID_TAGS: ['script', 'style', 'iframe', 'object', 'embed', 'form', 'input'],
        FORBID_ATTR: ['onerror', 'onload', 'onclick', 'formaction'],
        // ⚠ This is DOMPurify's **default** regex with one alternative added — it is not a
        // hand-written URI allowlist, and must not be replaced by one.
        //
        // DOMPurify applies ALLOWED_URI_REGEXP to the value of *every* attribute whose name is
        // permitted and which is not in its small inert set (alt/class/id/style/title/…). So the
        // pattern has to accept ordinary non-URI values too. The default's second alternative,
        // `[^a-z]` ("starts with a non-letter"), is what lets `width="280"` and `colspan="2"`
        // through. Replacing the whole regex with a strict scheme list silently stripped every
        // width/height in the app — the email arrived correctly sized while our own thread view
        // rendered logos at full bitmap size.
        //
        // The one addition is `data:image/(png|jpeg|gif|webp);base64,` — how the composer stores
        // an image before sending, and therefore what this view renders. `data:` is otherwise
        // blocked by the default (it fails all three alternatives), and **`svg+xml` stays
        // blocked**: SVG can carry script, so an inline one would be an XSS vector wearing a
        // logo's clothes. `common/mail.py` refuses the same set, so both ends agree.
        // `javascript:` remains blocked, and `cid:` was already in the default scheme list.
        ALLOWED_URI_REGEXP:
          /^(?:(?:(?:f|ht)tps?|mailto|tel|callto|sms|cid|xmpp|matrix):|data:image\/(?:png|jpeg|gif|webp);base64,|[^a-z]|[a-z+.\-]+(?:[^a-z+.\-:]|$))/i,
      }),
    [html],
  );

  // eslint-disable-next-line react/no-danger
  return <div className={className} dangerouslySetInnerHTML={{ __html: clean }} />;
}