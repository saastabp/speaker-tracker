import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { useApi } from './client';

// Mirrors backend models/funnel.py. The board columns come from the server (acceptance #9); the
// SPA hardcodes no stage name.

export interface FunnelStage {
  short_name: string;
  label: string;
  sort_order: number;
  is_terminal: boolean;
}

/** The server-owned board columns, in display order. Rarely changes, so it is cached indefinitely
 *  for the session; a full reload picks up any catalog change. */
export function useFunnel(): UseQueryResult<FunnelStage[]> {
  const api = useApi();
  return useQuery({
    queryKey: ['funnel'],
    queryFn: async () => (await api<{ stages: FunnelStage[] }>('/funnel')).stages,
    staleTime: Infinity,
  });
}