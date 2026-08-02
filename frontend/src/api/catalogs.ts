import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { useApi } from './client';

// Mirrors backend models/catalogs.py — the reference vocabularies the SPA loads once after
// sign-in. Callers resolve entries by `short_name`; ids/audit columns are not exposed.

export interface CatalogItem {
  short_name: string;
  description: string;
  sort_order: number;
}
export interface OpportunityStatus extends CatalogItem {
  is_terminal: boolean;
}
export interface PaymentStatus extends CatalogItem {
  is_settled: boolean;
}
export interface OutreachKind extends CatalogItem {
  counts_toward_target: boolean;
}

export interface Catalogs {
  organization_types: CatalogItem[];
  warmth_tiers: CatalogItem[];
  contact_roles: CatalogItem[];
  opportunity_formats: CatalogItem[];
  opportunity_statuses: OpportunityStatus[];
  comp_types: CatalogItem[];
  payment_statuses: PaymentStatus[];
  outreach_kinds: OutreachKind[];
  outreach_channels: CatalogItem[];
  message_template_kinds: CatalogItem[];
  target_types: CatalogItem[];
}

/** Any catalog, narrowed to what a label lookup needs. Widened from `CatalogItem[]` so the lists
 *  carrying extra flags (statuses, payment statuses, kinds) pass without a cast. */
export type CatalogList = { short_name: string; description: string }[] | undefined;

/**
 * Resolve a catalog `short_name` to its human label.
 *
 * Falls back to the short_name itself rather than blanking: an unknown code means the SPA is
 * running against a catalog it has not loaded, and showing `outreach_sent` is more use than
 * showing nothing. A null short_name is an unset optional field, so that renders empty.
 *
 * Lived as six near-identical private copies across the pages before this — `label`, `warmthLabel`,
 * `paymentLabel`, `orgTypeLabel`, `formatLabel` — which is why it sits beside the catalog types.
 */
export function catalogLabel(list: CatalogList, shortName: string | null): string {
  if (!shortName) return '';
  return list?.find((item) => item.short_name === shortName)?.description ?? shortName;
}

/** Load the catalog vocabularies. Reference data is stable for a session, so it never refetches. */
export function useCatalogs(): UseQueryResult<Catalogs> {
  const api = useApi();
  return useQuery({
    queryKey: ['catalogs'],
    queryFn: () => api<Catalogs>('/catalogs'),
    staleTime: Infinity,
  });
}