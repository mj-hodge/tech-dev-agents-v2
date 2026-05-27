import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import type { WorkHistoryResponse } from '../types/api';

export function useWorkHistory(agent?: string, since?: string) {
  const params = new URLSearchParams();
  if (agent) params.set('agent', agent);
  if (since) params.set('since', since);
  const qs = params.toString();
  const url = qs ? `/api/work-history?${qs}` : '/api/work-history';

  return useQuery<WorkHistoryResponse>({
    queryKey: ['work-history', agent ?? null, since ?? null],
    queryFn: () => api.get<WorkHistoryResponse>(url),
    staleTime: 60_000,
    refetchInterval: 60_000,
  });
}
