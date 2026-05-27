import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import type { AlertsResponse } from '../types/api';

export function useAlerts() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['alerts'],
    queryFn: () => api.get<AlertsResponse>('/api/alerts'),
    staleTime: 30_000,
  });
  return { data, isLoading, isError };
}
