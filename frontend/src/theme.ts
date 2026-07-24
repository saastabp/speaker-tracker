import { Badge, Input, Modal, createTheme, type MantineColorsTuple } from '@mantine/core';

// Brand palette (DESIGN §7 / ARCHITECTURE §7): navy nav rail + headings, terracotta primary
// actions, gold accents, cream page background. Sans-serif — the brand guide's Playfair/Lato
// pairing is for the public site, not this internal tool; colour carries the brand.
//
// Mantine wants a 10-shade ramp per colour (index 0 lightest → 9 darkest). These are first-pass
// ramps anchored on the brand hex; refine against Donna's eye later.
const navy: MantineColorsTuple = [
  '#eef2f5', '#d6e0e7', '#adc0cd', '#809fb2', '#5e849b',
  '#47738d', '#396580', '#2d4f66', '#24435a', '#1f3b4d',
];
const terracotta: MantineColorsTuple = [
  '#fdecea', '#f7cfc9', '#eda79d', '#e37f70', '#db5f4c',
  '#d64b36', '#c2483a', '#a83b2f', '#8f3025', '#75251c',
];
const gold: MantineColorsTuple = [
  '#fbf4e2', '#f3e2b8', '#ebcd85', '#e3b954', '#dda832',
  '#d99f26', '#d9a02c', '#bd8a1e', '#977015', '#6f5310',
];
// Approved semantic colours (mockup --good / --warn), anchored at shade 6; used for positive
// (Received / goal-met) and caution (Outstanding / awaiting) states across the app.
const good: MantineColorsTuple = [
  '#eef5f0', '#e2eee4', '#bfdac8', '#97c3a6', '#71ac86',
  '#549b6c', '#3f7a52', '#356a46', '#2b5539', '#20402b',
];
const warn: MantineColorsTuple = [
  '#faf3e1', '#f5e9cd', '#ecd49b', '#e0be66', '#d5aa3e',
  '#c69a29', '#b07d1e', '#956819', '#785314', '#5a3e0f',
];

/** Cream page background (Mantine has no natural slot for it; applied on AppShell.Main). */
export const BRAND_CREAM = '#FBF8F2';

/** Hairline that separates card titles from content and list rows (mockup `--line`). */
export const BRAND_LINE = '#E7DCC9';

/** Warm panel fill behind grouped surfaces — pipeline columns, insets (mockup `--surface-2`). */
export const BRAND_PANEL = '#F2EADE';

/** Uppercase field-label grey (mockup `--muted`). */
export const BRAND_MUTED = '#555555';

/** Lighter helper / placeholder grey (mockup `--faint`). */
export const BRAND_FAINT = '#948B7D';

const SANS =
  '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';

export const theme = createTheme({
  primaryColor: 'terracotta',
  primaryShade: 6,
  colors: { navy, terracotta, gold, good, warn },
  fontFamily: SANS,
  headings: { fontFamily: SANS, fontWeight: '600' },
  // Radius scale from the approved mockup: inputs/segmented 9px (sm, the default), cards 12px (md),
  // modal 14px (lg), pills/avatars fully round (xl). Mantine's defaults were ~4px (near-square).
  defaultRadius: 'sm',
  radius: {
    xs: '6px',
    sm: '9px',
    md: '12px',
    lg: '14px',
    xl: '9999px',
  },
  components: {
    // The approved mockup uses sentence-case, full-pill chips; Mantine's Badge defaults to uppercase.
    Badge: Badge.extend({
      styles: { label: { textTransform: 'none' } },
      defaultProps: { radius: 'xl' },
    }),
    // The modal frame is a 14px (lg) radius.
    Modal: Modal.extend({ defaultProps: { radius: 'lg' } }),
    // Warm brand hairline on input borders (mockup `--line`) instead of Mantine's generic grey.
    Input: Input.extend({ styles: { input: { borderColor: BRAND_LINE } } }),
  },
});