import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import type { FleetOverview } from '../types/api';

export function useFleet() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['fleet'],
    queryFn: () => api.get<FleetOverview>('/api/fleet'),
    staleTime: 10_000,
    refetchInterval: 10_000,
    refetchIntervalInBackground: true,
  });
  return { data, isLoading, isError };
}
