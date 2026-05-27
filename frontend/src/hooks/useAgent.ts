import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import type { AgentDetail } from '../types/api';

export function useAgent(name: string) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['agent', name],
    queryFn: () => api.get<AgentDetail>(`/api/agents/${name}`),
    staleTime: 10_000,
    refetchInterval: 10_000,
    refetchIntervalInBackground: true,
    enabled: !!name,
  });
  return { data, isLoading, isError };
}
