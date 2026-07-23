import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import { useApi } from './client';

// Mirrors backend models/organizations.py. Timestamps are ISO strings over the wire.

export interface OrganizationInput {
  organization_type: string; // organization_types catalog short_name
  name: string;
  location?: string | null;
  website_url?: string | null;
  email_domain?: string | null;
  what_it_is?: string | null;
  why_it_fits?: string | null;
  how_to_approach?: string | null;
  notes?: string | null;
}

export interface AffiliatedContact {
  contact_id: number;
  name: string;
  title: string | null;
  is_primary: boolean;
  is_power_partner: boolean;
}

export interface OrganizationSummary {
  id: number;
  organization_type: string;
  name: string;
  location: string | null;
  why_it_fits: string | null;
  contact_count: number;
  research_ready: boolean;
  created_at: string;
  updated_at: string;
}

export interface Organization extends OrganizationInput {
  id: number;
  contact_count: number;
  research_ready: boolean;
  created_at: string;
  updated_at: string;
  contacts: AffiliatedContact[];
}

const organizationKeys = {
  all: ['organizations'] as const,
  detail: (id: number) => ['organizations', id] as const,
};

/** List the caller's organizations (venues) as summaries. */
export function useOrganizations(): UseQueryResult<OrganizationSummary[]> {
  const api = useApi();
  return useQuery({
    queryKey: organizationKeys.all,
    queryFn: async () =>
      (await api<{ organizations: OrganizationSummary[] }>('/organizations')).organizations,
  });
}

/** Load one organization's full detail (Kindling fields, readiness, affiliated contacts). */
export function useOrganization(id: number): UseQueryResult<Organization> {
  const api = useApi();
  return useQuery({
    queryKey: organizationKeys.detail(id),
    queryFn: () => api<Organization>(`/organizations/${id}`),
  });
}

export function useCreateOrganization() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: OrganizationInput) =>
      api<Organization>('/organizations', { method: 'POST', body: JSON.stringify(data) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: organizationKeys.all }),
  });
}

export function useUpdateOrganization(id: number) {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: OrganizationInput) =>
      api<Organization>(`/organizations/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    onSuccess: (updated) => {
      queryClient.setQueryData(organizationKeys.detail(id), updated);
      queryClient.invalidateQueries({ queryKey: organizationKeys.all });
    },
  });
}

export function useDeleteOrganization() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      api<{ deleted: boolean }>(`/organizations/${id}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: organizationKeys.all }),
  });
}