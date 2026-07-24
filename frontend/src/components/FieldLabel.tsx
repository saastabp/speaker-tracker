import { Text } from '@mantine/core';
import type { ReactNode } from 'react';
import { BRAND_FAINT, BRAND_MUTED } from '../theme';

interface FieldLabelProps {
  children: ReactNode;
  /** Optional sentence-case helper shown inline after the label (mockup `.opt`). */
  helper?: ReactNode;
}

/** Uppercase field label matching the approved mockup (`.frm > label`): 11.5px, tracked,
 *  muted, bold — with an optional lighter inline helper (`.opt`). Render it directly above
 *  an input, leaving the input's own `label` prop unset, so field spacing stays consistent
 *  across every modal. */
export function FieldLabel({ children, helper }: FieldLabelProps) {
  return (
    <Text
      component="label"
      display="block"
      mb={5}
      fw={700}
      fz="11.5px"
      c={BRAND_MUTED}
      style={{ letterSpacing: '0.05em', textTransform: 'uppercase' }}
    >
      {children}
      {helper && (
        <Text
          component="span"
          fw={500}
          c={BRAND_FAINT}
          ml={6}
          style={{ letterSpacing: 0, textTransform: 'none' }}
        >
          {helper}
        </Text>
      )}
    </Text>
  );
}