import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import type { ActionResponse } from '../types/api';

export function useAgentActions(name: string) {
  const queryClient = useQueryClient();

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['agents'] });
    queryClient.invalidateQueries({ queryKey: ['agent', name] });
  };

  const restartMutation = useMutation<ActionResponse, Error, string>({
    mutationFn: (agentName: string) =>
      api.post<ActionResponse>(`/api/agents/${agentName}/restart`),
    onSuccess: invalidate,
  });

  const pauseMutation = useMutation<ActionResponse, Error, string>({
    mutationFn: (agentName: string) =>
      api.post<ActionResponse>(`/api/agents/${agentName}/pause`),
    onSuccess: invalidate,
  });

  return {
    restart: {
      mutate: restartMutation.mutate,
      isPending: restartMutation.isPending,
    },
    pause: {
      mutate: pauseMutation.mutate,
      isPending: pauseMutation.isPending,
    },
  };
}
