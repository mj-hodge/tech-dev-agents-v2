import { Link } from 'react-router-dom';
import { StatusBadge } from './StatusBadge';
import type { AgentSummary } from '../types/api';

export type PresenceState = 'working' | 'idle' | 'rate_limited' | 'offline';

export function formatResetTime(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return '—';
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export function formatTokens(n: number | null): string {
  if (n === null) return '—';
  if (n === 0) return '0';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}K`;
  return String(n);
}

const presenceDotClass: Record<PresenceState, string> = {
  working: 'bg-green-400',
  idle: 'bg-gray-400',
  rate_limited: 'bg-yellow-400',
  offline: 'bg-red-400',
};

function formatCurrency(value: number): string {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value);
}

function relativeTime(isoString: string | null): string {
  if (!isoString) return 'never';
  const now = Date.now();
  const then = new Date(isoString).getTime();
  const diffMs = now - then;
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHr = Math.floor(diffMin / 60);
  const diffDay = Math.floor(diffHr / 24);

  if (diffSec < 60) return 'just now';
  if (diffMin < 60) return `${diffMin}m ago`;
  if (diffHr < 24) return `${diffHr}h ago`;
  return `${diffDay}d ago`;
}

function truncateStory(story: string): string {
  if (story.length <= 40) return story;
  return story.slice(0, 40) + '...';
}

const roleBadgeStyles: Record<string, string> = {
  manager: 'bg-purple-900/50 text-purple-300 border-purple-700',
  developer: 'bg-green-900/50 text-green-300 border-green-700',
};
const defaultRoleBadge = 'bg-gray-900/50 text-gray-300 border-gray-700';

export function AgentCard({ agent, presenceState }: { agent: AgentSummary; presenceState?: PresenceState }) {
  // STORY-736: Branch on foundry_cost_status for honest cost display
  const costStatus = agent.foundry_cost_status;
  const isUnavailable = costStatus === 'unavailable';
  const isStale = costStatus === 'stale';
  // STORY-038 + STORY-736: Only warn when CM is healthy (ok) but foundry is $0 with active SDK
  const foundryMissing = costStatus === 'ok' && agent.today_foundry_usd === 0 && agent.today_sdk_usd > 0;

  return (
    <Link
      to={`/agents/${agent.name}`}
      className="block bg-gray-800 rounded-lg p-4 hover:shadow-lg hover:shadow-black/30 transition-shadow border border-gray-700 hover:border-gray-600"
    >
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-2">
          <h3 className="text-gray-100 font-bold text-lg">{agent.name}</h3>
          {presenceState && (
            <span
              data-testid="presence-dot"
              className={`presence-dot w-2 h-2 rounded-full ${presenceDotClass[presenceState]}`}
            />
          )}
          {agent.role && (
            <span
              className={`text-xs px-1.5 py-0.5 rounded border ${roleBadgeStyles[agent.role] ?? defaultRoleBadge}`}
            >
              {agent.role}
            </span>
          )}
        </div>
        <StatusBadge status={agent.status} />
      </div>
      <p className={`text-xs mb-2 ${agent.busy ? 'text-amber-300' : 'text-gray-500'}`}>
        {agent.busy ? 'Busy (SDK active/waiting)' : 'Not busy'}
      </p>
      <p className="text-gray-400 text-sm truncate mb-1">
        {agent.current_story ? truncateStory(agent.current_story) : '\u2013'}
      </p>
      {agent.current_story && (
        <p className="text-gray-500 text-sm mb-2">{agent.current_phase ?? ''}</p>
      )}
      {(agent.queued_stories?.length ?? 0) > 0 && (
        <p className="text-xs text-amber-500 mb-2">
          {agent.queued_stories!.length} queued
        </p>
      )}
      {agent.quota && agent.quota.source !== 'no_data' && (
        <div className="text-xs text-gray-400 mb-2 space-y-0.5">
          <div className="flex items-center gap-1">
            <span>{formatTokens(agent.quota.current_block_tokens)}</span>
            <span className="text-gray-600">/</span>
            <span>
              {agent.quota.p90_limit !== null
                ? formatTokens(agent.quota.p90_limit)
                : '~200K'}
            </span>
            {agent.quota.p90_limit === null && (
              <span
                data-testid="quota-estimated-indicator"
                className="text-amber-400 cursor-help"
                title="Estimated 200K baseline — per-agent P90 not yet measured (STORY-543)"
              >
                *
              </span>
            )}
          </div>
          {agent.quota.sessions_in_block !== null && (
            <div>
              {agent.quota.sessions_in_block} {agent.quota.sessions_in_block === 1 ? 'session' : 'sessions'}
            </div>
          )}
          {agent.quota.reset_in_minutes !== null && agent.quota.reset_in_minutes !== undefined && (
            <div>
              <span className="text-gray-500">resets in </span>
              {formatResetTime(agent.quota.reset_in_minutes)}
            </div>
          )}
        </div>
      )}
      <div className="flex items-center justify-between text-sm">
        <span
          className="text-blue-400 relative group"
          title={isUnavailable
            ? 'Azure cost data unavailable — check Cost Management config'
            : `SDK ${formatCurrency(agent.today_sdk_usd)} | Foundry ${formatCurrency(agent.today_foundry_usd)} | OpenAI ${formatCurrency(agent.today_openai_usd)} | Total ${formatCurrency(agent.today_total_usd)}`}
        >
          <span className="text-gray-500 text-xs mr-1">Azure Spend</span>
          {isUnavailable ? '—' : formatCurrency(agent.today_foundry_usd)}
          {isStale && (
            <span
              className="ml-1 text-yellow-400"
              data-testid="foundry-stale"
              title="Cost data may be stale (Azure 24-48h reporting lag)"
            >
              🕐
            </span>
          )}
          {foundryMissing && (
            <span
              className="ml-1 text-amber-400"
              title="Azure cost data unavailable"
              data-testid="foundry-warning"
            >
              ⚠
            </span>
          )}
        </span>
        <span className="text-gray-500">{relativeTime(agent.checked_at)}</span>
      </div>
    </Link>
  );
}
