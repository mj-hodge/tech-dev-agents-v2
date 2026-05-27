import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import type { AgentsResponse } from '../types/api';

export function useAgents() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['agents'],
    queryFn: () => api.get<AgentsResponse>('/api/agents').then(r => r.agents),
    staleTime: 10_000,
    refetchInterval: 10_000,
    refetchIntervalInBackground: true,
  });
  return { data, isLoading, isError };
}
