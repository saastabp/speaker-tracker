import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import { useApi } from './client';

// Mirrors backend models/targets.py. `target_type` is a target_types short_name; a target is a
// goal_count per (target_type, cadence).

export type Cadence = 'weekly' | 'monthly' | 'quarterly';

export interface Target {
  target_type: string;
  cadence: Cadence;
  goal_count: number;
}

const targetKeys = {
  all: ['targets'] as const,
};

/** List the caller's set targets. */
export function useTargets(): UseQueryResult<Target[]> {
  const api = useApi();
  return useQuery({
    queryKey: targetKeys.all,
    queryFn: async () => (await api<{ targets: Target[] }>('/targets')).targets,
  });
}

/** Upsert a goal for a (target_type, cadence). Refreshes both the targets list and the dashboard. */
export function usePutTarget() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: Target) =>
      api<Target>('/targets', { method: 'PUT', body: JSON.stringify(data) }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: targetKeys.all });
      queryClient.invalidateQueries({ queryKey: ['dashboard'] });
    },
  });
}

/** Unset a target. */
export function useDeleteTarget() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ target_type, cadence }: { target_type: string; cadence: Cadence }) =>
      api<{ deleted: boolean }>(`/targets/${target_type}/${cadence}`, { method: 'DELETE' }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: targetKeys.all });
      queryClient.invalidateQueries({ queryKey: ['dashboard'] });
    },
  });
}