import { Group, Text } from '@mantine/core';
import type { ReactNode } from 'react';
import { BRAND_FAINT, BRAND_LINE } from '../theme';

/** Card header: sentence-case heading + optional right-side action/hint, over a hairline. */
export function CardTitle({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <Group
      justify="space-between"
      align="center"
      pb={8}
      mb="sm"
      style={{ borderBottom: `1px solid ${BRAND_LINE}` }}
    >
      <Text fw={600} c="navy.9">
        {children}
      </Text>
      {action}
    </Group>
  );
}

/** One row of a two-column key-value detail grid (the parent sets the grid). */
export function KV({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <Text size="sm" c={BRAND_FAINT}>
        {label}
      </Text>
      <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
        {children}
      </Text>
    </>
  );
}

/** First+second word initials, e.g. "Kauai Beach Resort & Spa" → "KB", "Iris Kealoha" → "IK". */
export function initials(name: string): string {
  const words = name
    .trim()
    .split(/\s+/)
    .filter((w) => /[a-z0-9]/i.test(w));
  if (words.length === 0) return '?';
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}