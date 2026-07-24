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

/** Load the catalog vocabularies. Reference data is stable for a session, so it never refetches. */
export function useCatalogs(): UseQueryResult<Catalogs> {
  const api = useApi();
  return useQuery({
    queryKey: ['catalogs'],
    queryFn: () => api<Catalogs>('/catalogs'),
    staleTime: Infinity,
  });
}