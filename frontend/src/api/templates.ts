import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import { useApi } from './client';

// Mirrors backend models/message_templates.py. `kind` and `channel` are catalog short_names
// (message_template_kinds / outreach_channels); `body` holds merge fields resolved client-side.

export interface MessageTemplateInput {
  kind: string;
  channel: string;
  name: string;
  subject?: string | null; // null for DM templates
  body: string;
}

export interface MessageTemplate extends MessageTemplateInput {
  id: number;
  is_shared: boolean; // true = shared reference row (user_id NULL)
  created_at: string;
  updated_at: string;
}

const templateKeys = {
  all: ['templates'] as const,
  detail: (id: number) => ['templates', id] as const,
};

/** List every template visible to the caller (own + shared), shared first. */
export function useTemplates(): UseQueryResult<MessageTemplate[]> {
  const api = useApi();
  return useQuery({
    queryKey: templateKeys.all,
    queryFn: async () => (await api<{ templates: MessageTemplate[] }>('/templates')).templates,
  });
}

/** Load one visible template. */
export function useTemplate(id: number): UseQueryResult<MessageTemplate> {
  const api = useApi();
  return useQuery({
    queryKey: templateKeys.detail(id),
    queryFn: () => api<MessageTemplate>(`/templates/${id}`),
  });
}

export function useCreateTemplate() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: MessageTemplateInput) =>
      api<MessageTemplate>('/templates', { method: 'POST', body: JSON.stringify(data) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: templateKeys.all }),
  });
}

/** Full-replace a template. Editing a shared row edits it in place (does not fork it). */
export function useUpdateTemplate(id: number) {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: MessageTemplateInput) =>
      api<MessageTemplate>(`/templates/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    onSuccess: (updated) => {
      queryClient.setQueryData(templateKeys.detail(id), updated);
      queryClient.invalidateQueries({ queryKey: templateKeys.all });
    },
  });
}

/** Fork a template into a personal copy; the new copy is returned and the list refreshes. */
export function useDuplicateTemplate() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      api<MessageTemplate>(`/templates/${id}/duplicate`, { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: templateKeys.all }),
  });
}

/** Soft-delete one of the caller's own templates (shared rows are protected server-side). */
export function useDeleteTemplate() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      api<{ deleted: boolean }>(`/templates/${id}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: templateKeys.all }),
  });
}