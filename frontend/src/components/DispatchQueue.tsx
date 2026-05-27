/**
 * Dispatch Queue + History panels.
 * STORY-026: Queue display (original)
 * STORY-028: History tab, new status badges, duration column
 * STORY-858: Split failed/terminal stories (attention_queue) into a separate
 *            History panel below the Queue panel. Queue shows only actionable lanes.
 */

import { useState } from 'react';
import { useDispatchQueue, useCancelDispatch, useDispatchHistory } from '../hooks/useDispatchQueue';
import type { DispatchItem } from '../types/api';
import { NeedsInfoAnswerModal } from './NeedsInfoAnswerModal';

function timeAgo(isoDate: string): string {
  if (!isoDate) return '--';
  const ts = new Date(isoDate).getTime();
  if (!Number.isFinite(ts)) return '--';
  const seconds = Math.floor((Date.now() - ts) / 1000);
  if (!Number.isFinite(seconds) || seconds < 0) return '--';
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function formatDuration(startIso: string | null, endIso: string | null): string {
  if (!startIso || !endIso) return '--';
  const ms = new Date(endIso).getTime() - new Date(startIso).getTime();
  if (ms < 0) return '--';
  const totalSeconds = Math.floor(ms / 1000);
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const remainMins = minutes % 60;
  return `${hours}h ${remainMins}m`;
}

function scopeColor(scope: string): string {
  switch (scope) {
    case 'large': return 'bg-red-600/20 text-red-400';
    case 'medium': return 'bg-yellow-600/20 text-yellow-400';
    default: return 'bg-green-600/20 text-green-400';
  }
}

function statusBadge(status: string): { className: string; label: string } {
  // STORY-515: was missing in_review, in_progress, failed, rate_limited —
  // those items fell to the default gray "other" bucket or showed the raw
  // status string. Customer couldn't see what stage a story was actually in.
  switch (status) {
    case 'pending':
      return { className: 'bg-yellow-600/20 text-yellow-400', label: 'waiting' };
    case 'claimed':
      return { className: 'bg-blue-600/20 text-blue-400', label: 'claimed' };
    case 'in_progress':
      return { className: 'bg-blue-600/20 text-blue-400', label: 'in progress' };
    case 'in_review':
      return { className: 'bg-cyan-600/20 text-cyan-400', label: 'needs review' };
    case 'completed':
      return { className: 'bg-green-600/20 text-green-400', label: 'completed' };
    case 'cancelled':
      return { className: 'bg-red-600/20 text-red-400', label: 'cancelled' };
    case 'failed':
      return { className: 'bg-red-600/20 text-red-400', label: 'failed' };
    case 'paused':
      return { className: 'bg-purple-600/20 text-purple-400', label: 'paused' };
    case 'needs_info':
      return { className: 'bg-violet-600/20 text-violet-400', label: 'needs info' };
    case 'rate_limited':
      return { className: 'bg-amber-600/20 text-amber-400', label: 'rate limited' };
    default:
      return { className: 'bg-gray-600/20 text-gray-400', label: status };
  }
}

function storyLabel(item: DispatchItem): string {
  if (item.title) return `${item.story_id} — ${item.title}`;
  return item.story_id;
}

function ItemRow({ item, onCancel, onAnswer }: { item: DispatchItem; onCancel?: (id: string) => void; onAnswer?: (item: DispatchItem) => void }) {
  const badge = statusBadge(item.status);
  return (
    <tr className="border-t border-gray-700 hover:bg-gray-750">
      <td className="px-3 py-2 text-gray-100 font-mono text-sm">{storyLabel(item)}</td>
      <td className="px-3 py-2 text-gray-300 text-sm">{item.repo}</td>
      <td className="px-3 py-2">
        <span className={`px-2 py-0.5 rounded text-xs font-medium ${scopeColor(item.scope)}`}>
          {item.scope}
        </span>
      </td>
      <td className="px-3 py-2 text-gray-400 text-sm">
        {item.claimed_by ? (
          <span className="text-blue-400">{item.claimed_by}</span>
        ) : (
          <span className="text-gray-500">--</span>
        )}
      </td>
      <td className="px-3 py-2 text-gray-400 text-sm">
        {item.claimed_at ? timeAgo(item.claimed_at) : timeAgo(item.enqueued_at)}
      </td>
      <td className="px-3 py-2">
        <span className={`px-2 py-0.5 rounded text-xs font-medium ${badge.className}`}>
          {badge.label}
        </span>
      </td>
      <td className="px-3 py-2">
        {item.status === 'pending' && onCancel && (
          <button
            onClick={() => onCancel(item.story_id)}
            className="text-xs text-red-400 hover:text-red-300 underline"
          >
            cancel
          </button>
        )}
        {item.status === 'needs_info' && onAnswer && (
          <button
            onClick={() => onAnswer(item)}
            className="px-2 py-0.5 text-xs font-medium rounded border border-violet-500 text-violet-300 hover:bg-violet-600/30 transition-colors"
            aria-label={`Answer question for ${item.story_id}`}
          >
            Answer
          </button>
        )}
      </td>
    </tr>
  );
}

function HistoryRow({ item }: { item: DispatchItem }) {
  const badge = statusBadge(item.status);
  const duration = item.status === 'completed'
    ? formatDuration(item.claimed_at, item.completed_at)
    : formatDuration(item.enqueued_at, item.cancelled_at);

  return (
    <tr className="border-t border-gray-700 hover:bg-gray-750">
      <td className="px-3 py-2 text-gray-100 font-mono text-sm">{storyLabel(item)}</td>
      <td className="px-3 py-2 text-gray-300 text-sm">{item.repo}</td>
      <td className="px-3 py-2">
        <span className={`px-2 py-0.5 rounded text-xs font-medium ${scopeColor(item.scope)}`}>
          {item.scope}
        </span>
      </td>
      <td className="px-3 py-2 text-gray-400 text-sm">
        {item.claimed_by ? (
          <span className="text-blue-400">{item.claimed_by}</span>
        ) : (
          <span className="text-gray-500">--</span>
        )}
      </td>
      <td className="px-3 py-2 text-gray-400 text-sm">{timeAgo(item.enqueued_at)}</td>
      <td className="px-3 py-2 text-gray-400 text-sm">
        {item.completed_at ? timeAgo(item.completed_at) : item.cancelled_at ? timeAgo(item.cancelled_at) : '--'}
      </td>
      <td className="px-3 py-2 text-gray-300 text-sm font-mono">{duration}</td>
      <td className="px-3 py-2">
        <span className={`px-2 py-0.5 rounded text-xs font-medium ${badge.className}`}>
          {badge.label}
        </span>
      </td>
      <td className="px-3 py-2 text-gray-400 text-xs max-w-md truncate" title={item.title ?? item.prompt ?? ''}>
        {item.title ?? (item.prompt ? item.prompt.slice(0, 120) : '--')}
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// History Panel — terminal + failed lanes (attention_queue + API history)
// STORY-858: Replaces the former in-tab HistoryTab component. Now a standalone
// collapsible panel rendered below the Queue panel.
// ---------------------------------------------------------------------------

type DaysFilter = 7 | 30 | null;

function HistoryPanel({ attentionItems }: { attentionItems: DispatchItem[] }) {
  const [collapsed, setCollapsed] = useState(false);
  const [page, setPage] = useState(0);
  const [daysFilter, setDaysFilter] = useState<DaysFilter>(7);
  const limit = 20;
  const { data, isLoading, isError } = useDispatchHistory(page, limit);

  // Compute cutoff date for client-side filtering
  const cutoff: Date | null = daysFilter
    ? new Date(Date.now() - daysFilter * 86_400_000)
    : null;

  // Filter attention_queue items by age
  const filteredAttention: DispatchItem[] = cutoff
    ? attentionItems.filter((item) => new Date(item.enqueued_at) >= cutoff!)
    : attentionItems;

  // Filter history API items by age
  const historyItems: DispatchItem[] = data?.items ?? [];
  const filteredHistory: DispatchItem[] = cutoff
    ? historyItems.filter((item) => new Date(item.enqueued_at) >= cutoff!)
    : historyItems;

  const combinedItems = [...filteredAttention, ...filteredHistory];

  // Pagination only available in "All" mode (no date filter)
  const totalPages = !daysFilter && data ? Math.ceil(data.total / limit) : 0;

  const handleFilterChange = (d: DaysFilter) => {
    setPage(0);
    setDaysFilter(d);
  };

  return (
    <div
      data-testid="history-panel"
      data-section="history-panel"
      className="bg-gray-800 border border-gray-700 rounded-lg mx-4 mb-4"
    >
      <button
        onClick={() => setCollapsed(!collapsed)}
        className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-gray-750 rounded-t-lg"
      >
        <div className="flex items-center gap-2">
          <span className="text-gray-100 font-semibold text-sm">History</span>
          {filteredAttention.length > 0 && (
            <span
              data-testid="history-attention-badge"
              className="px-2 py-0.5 rounded-full text-xs font-medium bg-red-600/20 text-red-400"
            >
              {filteredAttention.length} in attention
            </span>
          )}
        </div>
        {/* Day filter buttons — stop propagation so clicks don't toggle collapse */}
        <div
          className="flex items-center gap-1"
          onClick={(e) => e.stopPropagation()}
        >
          {([7, 30, null] as const).map((d) => (
            <button
              key={d ?? 'all'}
              onClick={() => handleFilterChange(d)}
              className={`px-2 py-0.5 rounded text-xs transition-colors ${
                daysFilter === d
                  ? 'bg-gray-600 text-gray-100'
                  : 'text-gray-400 hover:text-gray-300'
              }`}
            >
              {d !== null ? `${d}d` : 'All'}
            </button>
          ))}
          <span className="text-gray-500 text-xs ml-1">{collapsed ? '+' : '−'}</span>
        </div>
      </button>

      {!collapsed && (
        <div className="px-4 pb-3">
          {isLoading && <div className="animate-pulse bg-gray-700 rounded h-12" />}
          {isError && <p className="text-red-400 text-sm py-2">Failed to load dispatch history</p>}

          {!isLoading && combinedItems.length === 0 && (
            <p className="text-gray-500 text-sm py-2">
              {daysFilter
                ? `No history in the last ${daysFilter} days`
                : 'No dispatch history yet'}
            </p>
          )}

          {combinedItems.length > 0 && (
            <>
              <table className="w-full text-left">
                <thead>
                  <tr className="text-gray-400 text-xs uppercase tracking-wide">
                    <th className="px-3 py-2">Story</th>
                    <th className="px-3 py-2">Repo</th>
                    <th className="px-3 py-2">Scope</th>
                    <th className="px-3 py-2">Agent</th>
                    <th className="px-3 py-2">Enqueued</th>
                    <th className="px-3 py-2">Finished</th>
                    <th className="px-3 py-2">Duration</th>
                    <th className="px-3 py-2">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {combinedItems.map((item) => (
                    <HistoryRow
                      key={`${item.story_id}-${item.completed_at ?? item.cancelled_at ?? item.enqueued_at}`}
                      item={item}
                    />
                  ))}
                </tbody>
              </table>

              {totalPages > 1 && (
                <div className="flex items-center justify-between px-3 py-2 text-sm text-gray-400">
                  <button
                    onClick={() => setPage(Math.max(0, page - 1))}
                    disabled={page === 0}
                    className="px-2 py-1 rounded bg-gray-700 hover:bg-gray-600 disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    Prev
                  </button>
                  <span>Page {page + 1} of {totalPages}</span>
                  <button
                    onClick={() => setPage(Math.min(totalPages - 1, page + 1))}
                    disabled={page >= totalPages - 1}
                    className="px-2 py-1 rounded bg-gray-700 hover:bg-gray-600 disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    Next
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Queue Panel — actionable lanes only
// STORY-858: attention_queue (failed) items are NO LONGER rendered here.
//            They are passed down to HistoryPanel.
// ---------------------------------------------------------------------------

export function DispatchQueue() {
  const { data, isLoading, isError, refetch } = useDispatchQueue();
  const cancelMutation = useCancelDispatch();
  const [collapsed, setCollapsed] = useState(false);
  const [answerModal, setAnswerModal] = useState<{ storyId: string; repo: string; isOpen: boolean } | null>(null);

  const needsInfoCount = data?.needs_info?.length ?? 0;
  const inReviewCount = data?.in_review?.length ?? 0;
  const pausedCount = data?.paused?.length ?? 0;
  const attentionItems = data?.attention_queue ?? [];
  const attentionCount = attentionItems.length;

  // Queue is non-empty if ANY actionable bucket has items — including needs_info.
  // Previously (pre-2026-04-23), needs_info-only states caused the table to
  // render "No stories in dispatch queue" even when stories were blocked on
  // operator input. STORY-528/505 fix: ensure those rows are visible.
  const totalItems =
    (data?.total_pending ?? 0) +
    (data?.total_claimed ?? 0) +
    needsInfoCount +
    inReviewCount +
    pausedCount;
  // NOTE: attentionCount is intentionally excluded — failed items are not actionable.

  // STORY-528/505 fix (2026-04-23): surface needs_info count prominently so
  // agent questions are visible at a glance. Clicking scrolls to the
  // needs_info section. Pulsing violet badge when > 0.
  const scrollToNeedsInfo = () => {
    setCollapsed(false);
    requestAnimationFrame(() => {
      document
        .querySelector('[data-section="needs-info"]')
        ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  };

  // STORY-858: scroll to History panel when operator clicks attention callout.
  const scrollToHistory = () => {
    requestAnimationFrame(() => {
      document
        .querySelector('[data-testid="history-panel"]')
        ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  };

  return (
    <>
      {/* ------------------------------------------------------------------ */}
      {/* Queue Panel — actionable lanes: pending, in_progress, in_review,   */}
      {/* paused, needs_info. No attention_queue (failed) rows here.          */}
      {/* ------------------------------------------------------------------ */}
      <div data-testid="queue-panel" className="bg-gray-800 border border-gray-700 rounded-lg mx-4 mb-4">
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-gray-750 rounded-t-lg"
        >
          <div className="flex items-center gap-2">
            <span className="text-gray-100 font-semibold text-sm">Dispatch Queue</span>
            {totalItems > 0 && (
              <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-yellow-600/20 text-yellow-400">
                {data?.total_pending ?? 0} pending
              </span>
            )}
            {needsInfoCount > 0 && (
              <span
                role="button"
                data-testid="needs-info-badge"
                aria-label={`${needsInfoCount} stories need your input`}
                onClick={(e) => {
                  e.stopPropagation();
                  scrollToNeedsInfo();
                }}
                className="px-2 py-0.5 rounded-full text-xs font-medium bg-violet-600/25 text-violet-300 ring-1 ring-violet-500/60 animate-pulse cursor-pointer hover:bg-violet-600/40"
              >
                {needsInfoCount} needs info
              </span>
            )}
            {/* STORY-858: attention callout — links to History panel */}
            {attentionCount > 0 && (
              <span
                role="button"
                data-testid="attention-callout"
                aria-label={`${attentionCount} stories in attention queue — view history`}
                onClick={(e) => {
                  e.stopPropagation();
                  scrollToHistory();
                }}
                className="px-2 py-0.5 rounded-full text-xs font-medium bg-red-600/20 text-red-400 ring-1 ring-red-500/40 cursor-pointer hover:bg-red-600/30"
              >
                {attentionCount} in attention — view history
              </span>
            )}
          </div>
          <span className="text-gray-500 text-xs">{collapsed ? '+' : '−'}</span>
        </button>

        {!collapsed && (
          <div className="px-4 pb-3">
            {isLoading && (
              <div className="animate-pulse bg-gray-700 rounded h-12" />
            )}

            {isError && (
              <p className="text-red-400 text-sm py-2">Failed to load dispatch queue</p>
            )}

            {data && totalItems === 0 && (
              <p className="text-gray-500 text-sm py-2">No stories in dispatch queue</p>
            )}

            {data && totalItems > 0 && (
              <table className="w-full text-left">
                <thead>
                  <tr className="text-gray-400 text-xs uppercase tracking-wide">
                    <th className="px-3 py-2">Story</th>
                    <th className="px-3 py-2">Repo</th>
                    <th className="px-3 py-2">Scope</th>
                    <th className="px-3 py-2">Agent</th>
                    <th className="px-3 py-2">Time</th>
                    <th className="px-3 py-2">Status</th>
                    <th className="px-3 py-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {data.pending.map((item) => (
                    <ItemRow
                      key={item.story_id}
                      item={item}
                      onCancel={(id) => cancelMutation.mutate(id)}
                    />
                  ))}
                  {(data.in_progress ?? data.claimed).map((item) => (
                    <ItemRow key={item.story_id} item={item} />
                  ))}
                  {/* STORY-515: render in_review and paused rows too.
                      Previously these states were invisible on the dashboard
                      even though the server was publishing them. */}
                  {data.in_review?.map((item) => (
                    <ItemRow key={`ir-${item.story_id}`} item={item} />
                  ))}
                  {data.paused?.map((item) => (
                    <ItemRow key={`p-${item.story_id}`} item={item} />
                  ))}
                  {needsInfoCount > 0 && (
                    <tr data-section="needs-info" aria-hidden="true">
                      <td colSpan={6} className="p-0 border-0" />
                    </tr>
                  )}
                  {data.needs_info?.map((item) => (
                    <ItemRow
                      key={`ni-${item.story_id}`}
                      item={item}
                      onAnswer={(it) => setAnswerModal({ storyId: it.story_id, repo: it.repo, isOpen: true })}
                    />
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* History Panel — terminal + failed lanes.                            */}
      {/* Receives attention_queue items from the live queue response;         */}
      {/* merges with completed/cancelled from /api/dispatch/history.         */}
      {/* ------------------------------------------------------------------ */}
      <HistoryPanel attentionItems={attentionItems} />

      {answerModal?.isOpen && (
        <NeedsInfoAnswerModal
          storyId={answerModal.storyId}
          repo={answerModal.repo}
          isOpen={answerModal.isOpen}
          onClose={() => setAnswerModal(null)}
          onAnswered={() => {
            refetch();
            setAnswerModal(null);
          }}
        />
      )}
    </>
  );
}
