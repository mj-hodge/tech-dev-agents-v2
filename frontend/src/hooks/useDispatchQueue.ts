import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, isAuthenticated } from '../api/client';
import { adaptV2QueueResponse, type V2QueueBuckets } from '../api/dispatch';
import type { DispatchHistoryResponse, DispatchMetrics } from '../types/api';

// Epic-Queue-v2 Q5 — dashboard now reads from the v2 endpoint.
// The v2 response is a bucketed shape (with an extra `attention` lane and no
// total_pending/total_claimed/fetched_at fields), so adaptV2QueueResponse
// normalises it to the DispatchQueueResponse shape the rest of the UI expects.
export function useDispatchQueue() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['dispatch-queue'],
    queryFn: async () => {
      if (!isAuthenticated()) {
        throw new Error('Not authenticated to dispatch API');
      }
      const raw = await api.get<V2QueueBuckets>('/api/dispatch/v2/queue');
      const v2 = adaptV2QueueResponse(raw);

      const v2Total =
        (v2.pending?.length ?? 0) +
        (v2.in_progress?.length ?? 0) +
        (v2.in_review?.length ?? 0) +
        (v2.paused?.length ?? 0) +
        (v2.needs_info?.length ?? 0);

      // Guardrail: if v2 projection briefly reports empty, cross-check v1.
      // If v1 has active rows, prefer v1 to avoid false "all zero" dashboards.
      if (v2Total === 0) {
        const v1 = await api.get<V2QueueBuckets>('/api/dispatch/queue');
        const v1Total =
          (v1.pending?.length ?? 0) +
          (v1.in_progress?.length ?? 0) +
          (v1.in_review?.length ?? 0) +
          (v1.paused?.length ?? 0) +
          (v1.needs_info?.length ?? 0);
        if (v1Total > 0) {
          return adaptV2QueueResponse(v1);
        }
      }

      return v2;
    },
    staleTime: 5_000,
    refetchInterval: 5_000,
    refetchIntervalInBackground: true,
  });
  return { data, isLoading, isError, refetch };
}

export function useCancelDispatch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (storyId: string) =>
      api.delete<{ cancelled: boolean }>(`/api/dispatch/queue/${storyId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dispatch-queue'] });
      queryClient.invalidateQueries({ queryKey: ['dispatch-history'] });
    },
  });
}

export function useDispatchHistory(page: number = 0, limit: number = 20) {
  return useQuery({
    queryKey: ['dispatch-history', page, limit],
    queryFn: async () => {
      const [terminal, attention] = await Promise.all([
        api.get<{ items: Array<Record<string, unknown>> }>(
          `/api/dispatch/v2/queue?lane=terminal&limit=300`
        ),
        api.get<{ items: Array<Record<string, unknown>> }>(
          `/api/dispatch/v2/queue?lane=attention_queue&limit=300`
        ),
      ]);

      const toItem = (row: Record<string, unknown>) => {
        const state = String(row.state ?? '');
        const updatedAt = String(row.updated_at ?? row.created_at ?? new Date().toISOString());
        const status =
          state === 'completed' ? 'completed' :
          state === 'failed' ? 'failed' :
          'cancelled';

        return {
          story_id: String(row.story_id ?? ''),
          repo: String(row.repo ?? ''),
          scope: String(row.scope ?? 'small'),
          prompt: String(row.prompt ?? ''),
          enqueued_at: String(row.created_at ?? updatedAt),
          enqueued_by: String(row.enqueued_by ?? 'system'),
          title: (row.title as string | null) ?? null,
          status,
          claimed_by: null,
          claimed_at: null,
          completed_at: status === 'completed' ? updatedAt : null,
          cancelled_at: status === 'cancelled' ? updatedAt : null,
          failed_at: status === 'failed' ? updatedAt : null,
          commit_sha: (row.commit_sha as string | null) ?? null,
          pr_number: (row.pr_number as number | null) ?? null,
          paused_at: null,
          current_phase: null,
          phase_started_at: null,
          needs_info_path: null,
          rework_of: (row.rework_of as string | null) ?? null,
          question_text: null,
          answer_text: null,
          failure_reason: (row.failure_reason as string | null)
            ?? (row.failure_class as string | null)
            ?? null,
          dependencies_unmet: [],
        };
      };

      const all = [
        ...(terminal.items ?? []).map(toItem),
        ...(attention.items ?? []).map(toItem),
      ].sort((a, b) => {
        const rank = (s: string) => (
          s === 'completed' ? 0 :
          s === 'cancelled' ? 1 :
          2 // failed
        );
        const sr = rank(a.status) - rank(b.status);
        if (sr !== 0) return sr;
        const at = Date.parse(
          a.completed_at || a.cancelled_at || a.failed_at || a.enqueued_at
        );
        const bt = Date.parse(
          b.completed_at || b.cancelled_at || b.failed_at || b.enqueued_at
        );
        return bt - at;
      });

      const start = page * limit;
      const end = start + limit;
      const items = all.slice(start, end);
      return {
        items,
        total: all.length,
        limit,
        offset: start,
        fetched_at: new Date().toISOString(),
      } as DispatchHistoryResponse;
    },
    staleTime: 30_000,
  });
}

export function useDispatchMetrics() {
  return useQuery({
    queryKey: ['dispatch-metrics'],
    queryFn: () => api.get<DispatchMetrics>('/api/dispatch/metrics'),
    staleTime: 10_000,
    refetchInterval: 15_000,
    refetchIntervalInBackground: true,
  });
}
