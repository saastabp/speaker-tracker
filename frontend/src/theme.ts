import { createTheme, type MantineColorsTuple } from '@mantine/core';

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

/** Cream page background (Mantine has no natural slot for it; applied on AppShell.Main). */
export const BRAND_CREAM = '#FBF8F2';

const SANS =
  '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';

export const theme = createTheme({
  primaryColor: 'terracotta',
  primaryShade: 6,
  colors: { navy, terracotta, gold },
  fontFamily: SANS,
  headings: { fontFamily: SANS, fontWeight: '600' },
});