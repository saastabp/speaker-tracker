import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import { useApi } from './client';

// Mirrors backend models/contacts.py. Affiliation writes change organization contact counts and
// research-readiness, so their mutations invalidate the organizations cache too.

export interface ContactInput {
  name: string;
  email?: string | null;
  phone?: string | null;
  warmth_tier?: string | null; // warmth_tiers catalog short_name
  source?: string | null;
  how_you_know?: string | null;
  notes?: string | null;
}

export interface OrganizationAffiliation {
  organization_id: number;
  organization_name: string;
  title: string | null;
  is_primary: boolean;
  is_power_partner: boolean; // scoped to this contact↔venue edge
}

export interface ContactSummary {
  id: number;
  name: string;
  email: string | null;
  warmth_tier: string | null;
  is_power_partner: boolean; // rollup: a power partner at ≥1 affiliated venue
  organization_count: number;
  created_at: string;
  updated_at: string;
}

export interface Contact extends ContactInput {
  id: number;
  created_at: string;
  updated_at: string;
  organizations: OrganizationAffiliation[];
}

export interface AffiliationInput {
  organization_id: number;
  title?: string | null;
  is_primary?: boolean;
  is_power_partner?: boolean;
}

export interface AffiliationUpdate {
  title?: string | null;
  is_primary?: boolean;
  is_power_partner?: boolean;
}

const contactKeys = {
  all: ['contacts'] as const,
  list: (query?: string) => ['contacts', 'list', query ?? ''] as const,
  detail: (id: number) => ['contacts', id] as const,
};

/** List the caller's contacts; ``query`` runs the dedupe search (name/email substring). Pass
 *  ``enabled: false`` to hold the query (e.g. until a debounced search term is long enough). */
export function useContacts(query?: string, enabled = true): UseQueryResult<ContactSummary[]> {
  const api = useApi();
  return useQuery({
    queryKey: contactKeys.list(query),
    queryFn: async () => {
      const path = query ? `/contacts?q=${encodeURIComponent(query)}` : '/contacts';
      return (await api<{ contacts: ContactSummary[] }>(path)).contacts;
    },
    enabled,
  });
}

/** Load one contact's full detail, including organization affiliations. */
export function useContact(id: number): UseQueryResult<Contact> {
  const api = useApi();
  return useQuery({
    queryKey: contactKeys.detail(id),
    queryFn: () => api<Contact>(`/contacts/${id}`),
  });
}

export function useCreateContact() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: ContactInput) =>
      api<Contact>('/contacts', { method: 'POST', body: JSON.stringify(data) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: contactKeys.all }),
  });
}

export function useUpdateContact(id: number) {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: ContactInput) =>
      api<Contact>(`/contacts/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    onSuccess: (updated) => {
      queryClient.setQueryData(contactKeys.detail(id), updated);
      queryClient.invalidateQueries({ queryKey: contactKeys.all });
    },
  });
}

export function useDeleteContact() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api<{ deleted: boolean }>(`/contacts/${id}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: contactKeys.all }),
  });
}

/** Cache updates shared by the affiliation mutations: refresh the contact and the orgs list. */
function useAffiliationMutation<TVariables>(
  contactId: number,
  request: (api: ReturnType<typeof useApi>, variables: TVariables) => Promise<Contact>,
) {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (variables: TVariables) => request(api, variables),
    onSuccess: (updated) => {
      queryClient.setQueryData(contactKeys.detail(contactId), updated);
      queryClient.invalidateQueries({ queryKey: contactKeys.all });
      queryClient.invalidateQueries({ queryKey: ['organizations'] });
    },
  });
}

export function useAddAffiliation(contactId: number) {
  return useAffiliationMutation<AffiliationInput>(contactId, (api, data) =>
    api<Contact>(`/contacts/${contactId}/organizations`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  );
}

// Edit and detach take both ids as mutation variables (not hook args), so a list page that fixes
// one side (the venue's contacts panel) can drive them per-row. Both endpoints return the updated
// contact, so the cache update is the same as the add path.
function affiliationCacheUpdate(
  queryClient: ReturnType<typeof useQueryClient>,
  contactId: number,
  updated: Contact,
) {
  queryClient.setQueryData(contactKeys.detail(contactId), updated);
  queryClient.invalidateQueries({ queryKey: contactKeys.all });
  queryClient.invalidateQueries({ queryKey: ['organizations'] });
}

export function useEditAffiliation() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      contactId,
      organizationId,
      data,
    }: {
      contactId: number;
      organizationId: number;
      data: AffiliationUpdate;
    }) =>
      api<Contact>(`/contacts/${contactId}/organizations/${organizationId}`, {
        method: 'PUT',
        body: JSON.stringify(data),
      }),
    onSuccess: (updated, { contactId }) => affiliationCacheUpdate(queryClient, contactId, updated),
  });
}

export function useDetachAffiliation() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ contactId, organizationId }: { contactId: number; organizationId: number }) =>
      api<Contact>(`/contacts/${contactId}/organizations/${organizationId}`, { method: 'DELETE' }),
    onSuccess: (updated, { contactId }) => affiliationCacheUpdate(queryClient, contactId, updated),
  });
}