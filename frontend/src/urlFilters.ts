// The list pages keep their filter state in the URL rather than in `useState`.
//
// Slice 8 turns every Dashboard aggregate into a link into the list behind it, which only works if
// a link can *tell* a list what to show — local state would leave `/pipeline?reached=booked`
// quietly rendering the unfiltered board. It also makes a filtered view shareable and survive a
// reload, which local state never did.
//
// This hook exists because the same twelve lines had been written into two pages and were about to
// be written into four more.

import { useSearchParams } from 'react-router-dom';

export interface FilterParams {
  /** Read one filter, falling back to its default when absent from the URL. */
  get: (key: string, fallback?: string) => string;
  /** True when the key is present with this exact value. */
  has: (key: string, value: string) => boolean;
  /** Write one filter, dropping the key from the URL when it returns to its default. */
  set: (key: string, value: string, fallback?: string) => void;
  /** Write several filters in one update, dropping the keys whose value is empty. Use this — not
   *  repeated `set` calls — whenever a single control changes more than one key. */
  setMany: (values: Record<string, string>) => void;
  /** Toggle a filter between `value` and its default — the pill behaviour. */
  toggle: (key: string, value: string, fallback?: string) => void;
}

/**
 * Read and write a page's filter row through the query string.
 *
 * Two behaviours are deliberate and easy to lose when this is hand-rolled per page:
 *
 * - A filter at its default **deletes its key** rather than writing `?filter=all`, so a default
 *   view has a clean URL and two people describing "the contacts page" produce the same link.
 * - Writes `replace` rather than `push`. Filtering is not navigation; without this every
 *   keystroke in a search box becomes a separate Back-button stop, and leaving the page means
 *   pressing Back once per character typed.
 */
export function useFilterParams(): FilterParams {
  const [searchParams, setSearchParams] = useSearchParams();

  const get = (key: string, fallback = '') => searchParams.get(key) ?? fallback;

  const set = (key: string, value: string, fallback = '') => {
    const next = new URLSearchParams(searchParams);
    if (!value || value === fallback) {
      next.delete(key);
    } else {
      next.set(key, value);
    }
    setSearchParams(next, { replace: true });
  };

  /** Write several filters at once, deleting the keys whose value is empty.
   *
   * Necessary whenever one control changes more than one key. Sequential `set` calls each rebuild
   * from the *same* render's `searchParams`, so they clobber one another and only the last
   * survives — which is how Pipeline's "entered" pill came to clear one of its three keys and
   * appear to do nothing at all.
   */
  const setMany = (values: Record<string, string>) => {
    const next = new URLSearchParams(searchParams);
    for (const [key, value] of Object.entries(values)) {
      if (!value) {
        next.delete(key);
      } else {
        next.set(key, value);
      }
    }
    setSearchParams(next, { replace: true });
  };

  return {
    get,
    has: (key, value) => searchParams.get(key) === value,
    set,
    setMany,
    toggle: (key, value, fallback = '') =>
      set(key, get(key, fallback) === value ? fallback : value, fallback),
  };
}