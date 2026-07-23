import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import { useApi } from './client';

// Mirrors backend models/talks.py. Timestamps are ISO strings over the wire.

export interface TalkInput {
  title: string;
  length_minutes?: number | null;
  one_liner?: string | null;
  sort_order?: number;
}

export interface TalkSummary {
  id: number;
  title: string;
  length_minutes: number | null;
  one_liner: string | null;
  sort_order: number;
  created_at: string;
  updated_at: string;
}

export interface Talk extends TalkInput {
  id: number;
  created_at: string;
  updated_at: string;
}

const talkKeys = {
  all: ['talks'] as const,
  detail: (id: number) => ['talks', id] as const,
};

/** List the caller's talks (the reusable offers), ordered for the picker. */
export function useTalks(): UseQueryResult<TalkSummary[]> {
  const api = useApi();
  return useQuery({
    queryKey: talkKeys.all,
    queryFn: async () => (await api<{ talks: TalkSummary[] }>('/talks')).talks,
  });
}

/** Load one talk's detail. */
export function useTalk(id: number): UseQueryResult<Talk> {
  const api = useApi();
  return useQuery({
    queryKey: talkKeys.detail(id),
    queryFn: () => api<Talk>(`/talks/${id}`),
  });
}

export function useCreateTalk() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: TalkInput) =>
      api<Talk>('/talks', { method: 'POST', body: JSON.stringify(data) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: talkKeys.all }),
  });
}

export function useUpdateTalk(id: number) {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: TalkInput) =>
      api<Talk>(`/talks/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    onSuccess: (updated) => {
      queryClient.setQueryData(talkKeys.detail(id), updated);
      queryClient.invalidateQueries({ queryKey: talkKeys.all });
    },
  });
}

export function useDeleteTalk() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api<{ deleted: boolean }>(`/talks/${id}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: talkKeys.all }),
  });
}