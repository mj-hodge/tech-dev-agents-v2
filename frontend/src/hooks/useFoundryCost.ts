/**
 * STORY-576: Hook for fetching fleet-wide Foundry cost by model deployment.
 */

import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import type { FoundryCostResponse } from '../types/api';

export type { FoundryCostResponse as FoundryCostData };
export type { DailyFoundryCost } from '../types/api';

export function useFoundryCost(days: number = 7) {
  return useQuery({
    queryKey: ['foundry-cost', days],
    queryFn: () => api.get<FoundryCostResponse>(`/api/fleet/foundry-cost?days=${days}`),
    staleTime: 300_000,       // 5 minutes — server data only updates every 2h
    refetchInterval: 300_000, // re-check every 5 minutes
  });
}
