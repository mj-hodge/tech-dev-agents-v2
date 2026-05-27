import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useWorkHistory } from '../hooks/useWorkHistory';
import type { CompletedStory } from '../types/api';

function formatCurrency(value: number): string {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value);
}

function PrBadge({ state }: { state: string | null }) {
  if (!state) return <span className="text-gray-500 text-xs">no PR</span>;
  const colors: Record<string, string> = {
    open: 'bg-green-600 text-green-100',
    merged: 'bg-purple-600 text-purple-100',
    closed: 'bg-red-600 text-red-100',
  };
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${colors[state] || 'bg-gray-600 text-gray-100'}`}>
      {state}
    </span>
  );
}

function StoryRow({ story }: { story: CompletedStory & { total_cost_usd?: number | null } }) {
  const timeAgo = story.completed_at
    ? new Date(story.completed_at).toLocaleDateString()
    : '—';

  return (
    <tr className="border-b border-gray-700 hover:bg-gray-750">
      <td className="px-3 py-2 text-sm font-medium text-gray-200">{story.story_id}</td>
      <td className="px-3 py-2 text-sm text-gray-400">{story.repo}</td>
      <td className="px-3 py-2 text-sm text-gray-400">{story.agent}</td>
      <td className="px-3 py-2 text-sm text-gray-300 max-w-xs truncate">{story.summary}</td>
      <td className="px-3 py-2">
        {story.pr_url ? (
          <a href={story.pr_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1">
            <PrBadge state={story.pr_state} />
            <span className="text-xs text-gray-500">#{story.pr_number}</span>
          </a>
        ) : (
          <span className="text-gray-600 text-xs">no PR</span>
        )}
      </td>
      <td className="px-3 py-2 text-sm text-gray-400">
        {story.total_cost_usd != null ? formatCurrency(story.total_cost_usd) : '—'}
      </td>
      <td className="px-3 py-2 text-sm text-gray-500">{timeAgo}</td>
    </tr>
  );
}

const DATE_PRESETS = [
  { label: 'Today', days: 1 },
  { label: '7d', days: 7 },
  { label: '30d', days: 30 },
  { label: 'All', days: 0 },
] as const;

export function WorkHistoryPanel() {
  const [searchParams, setSearchParams] = useSearchParams();
  const agentFilter = searchParams.get('agent') ?? '';
  const [activeDays, setActiveDays] = useState<number>(0);
  const [agentSelect, setAgentSelect] = useState<string>(agentFilter);

  const sinceDate = activeDays > 0
    ? new Date(Date.now() - activeDays * 24 * 60 * 60 * 1000).toISOString().slice(0, 10)
    : undefined;

  const { data, isLoading, isError } = useWorkHistory(
    agentSelect || undefined,
    sinceDate,
  );

  const handleAgentChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const val = e.target.value;
    setAgentSelect(val);
    const next = new URLSearchParams(searchParams);
    if (val) {
      next.set('agent', val);
    } else {
      next.delete('agent');
    }
    setSearchParams(next);
  };

  if (isLoading) {
    return (
      <div className="bg-gray-800 rounded-lg p-6">
        <h2 className="text-lg font-semibold text-gray-200 mb-4">Work History</h2>
        <div className="animate-pulse space-y-2">
          {[1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="h-8 bg-gray-700 rounded" />
          ))}
        </div>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="bg-gray-800 rounded-lg p-6">
        <h2 className="text-lg font-semibold text-gray-200 mb-4">Work History</h2>
        <p className="text-red-400 text-sm">Failed to load work history</p>
      </div>
    );
  }

  const stories = data?.stories || [];
  const openPRs = stories.filter((s) => s.pr_state === 'open');

  // Unique agents for the filter dropdown
  const uniqueAgents = Array.from(new Set(stories.map((s) => s.agent))).sort();

  return (
    <div className="bg-gray-800 rounded-lg p-6">
      <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
        <h2 className="text-lg font-semibold text-gray-200">Work History</h2>
        {openPRs.length > 0 && (
          <span className="bg-green-600 text-green-100 text-xs px-2 py-1 rounded-full font-medium">
            {openPRs.length} PR{openPRs.length > 1 ? 's' : ''} to review
          </span>
        )}
      </div>

      {/* STORY-480: Filter controls */}
      <div className="flex items-center gap-3 mb-4 flex-wrap">
        <select
          name="agent-filter"
          value={agentSelect}
          onChange={handleAgentChange}
          className="bg-gray-700 text-gray-200 text-sm rounded px-2 py-1 border border-gray-600"
        >
          <option value="">All Agents</option>
          {uniqueAgents.map((a) => (
            <option key={a} value={a}>{a}</option>
          ))}
        </select>

        <div className="flex gap-1">
          {DATE_PRESETS.map(({ label, days }) => (
            <button
              key={label}
              onClick={() => setActiveDays(days)}
              className={`text-xs px-2 py-1 rounded ${
                activeDays === days
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {stories.length === 0 ? (
        <p className="text-gray-500 text-sm">No agent PRs found</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-gray-600">
                <th className="px-3 py-2 text-xs font-medium text-gray-400 uppercase">Story</th>
                <th className="px-3 py-2 text-xs font-medium text-gray-400 uppercase">Repo</th>
                <th className="px-3 py-2 text-xs font-medium text-gray-400 uppercase">Agent</th>
                <th className="px-3 py-2 text-xs font-medium text-gray-400 uppercase">Title</th>
                <th className="px-3 py-2 text-xs font-medium text-gray-400 uppercase">PR</th>
                <th className="px-3 py-2 text-xs font-medium text-gray-400 uppercase">Cost</th>
                <th className="px-3 py-2 text-xs font-medium text-gray-400 uppercase">Date</th>
              </tr>
            </thead>
            <tbody>
              {stories.map((story, i) => (
                <StoryRow key={`${story.repo}-${story.pr_number}-${i}`} story={story as any} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
