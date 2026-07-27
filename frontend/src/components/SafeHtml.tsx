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
        // Block `javascript:` and `data:` URLs in href/src; only real web and mail links survive.
        ALLOWED_URI_REGEXP: /^(?:https?|mailto|tel|cid):/i,
      }),
    [html],
  );

  // eslint-disable-next-line react/no-danger
  return <div className={className} dangerouslySetInnerHTML={{ __html: clean }} />;
}