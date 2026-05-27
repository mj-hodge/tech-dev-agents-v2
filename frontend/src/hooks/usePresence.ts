import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { PresenceResponse } from "../types/api";

export function usePresence() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["presence"],
    queryFn: () => api.get<PresenceResponse>("/api/agents/presence"),
    staleTime: 30_000,
    refetchInterval: 30_000,
    refetchIntervalInBackground: true,
  });

  return { data, isLoading, isError };
}
