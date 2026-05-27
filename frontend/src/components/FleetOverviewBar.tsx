import { useFleet } from '../hooks/useFleet';
import type { FleetOverview } from '../types/api';

function formatCurrency(value: number): string {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value);
}

function healthColor(score: number): string {
  if (score >= 0.8) return 'text-green-400';
  if (score >= 0.5) return 'text-yellow-400';
  return 'text-red-400';
}

function formatHealthPercent(score: number): string {
  return `${Math.round(score * 100)}%`;
}

function totalQueued(data: FleetOverview | undefined): number {
  if (!data) return 0;
  return data.agents.reduce((sum, a) => sum + (a.queued_stories?.length ?? 0), 0);
}

function budgetColor(pct: number): string {
  if (pct >= 90) return 'bg-red-500';
  if (pct >= 80) return 'bg-yellow-500';
  return 'bg-green-500';
}

export function FleetOverviewBar() {
  const { data, isLoading } = useFleet();

  if (isLoading) {
    return (
      <div className="flex gap-4 p-4 bg-gray-800 border-b border-gray-700">
        {[1, 2, 3, 4, 5, 6, 7, 8].map((i) => (
          <div key={i} className="flex-1 animate-pulse bg-gray-700 rounded h-16" />
        ))}
      </div>
    );
  }

  const budgetUsd = data?.daily_budget_usd ?? 0;
  const spendUsd = data?.total_daily_spend_usd ?? 0;
  const budgetPct = budgetUsd > 0 ? Math.floor((spendUsd / budgetUsd) * 100) : 0;
  const barColor = budgetColor(budgetPct);

  return (
    <div className="flex gap-4 p-4 bg-gray-800 border-b border-gray-700 flex-wrap">
      {/* STORY-496: Relabeled to "Azure Foundry Spend" */}
      {/* STORY-736: Banner when Cost Management is unreachable */}
      <div className="flex-1 min-w-[150px] bg-gray-700 rounded p-3">
        <p className="text-gray-400 text-xs uppercase tracking-wide">Daily Foundry Spend</p>
        <p className="text-gray-100 text-xl font-bold">
          {data && data.cost_mgmt_reachable === false
            ? '—'
            : data ? formatCurrency(data.daily_foundry_usd ?? data.total_daily_spend_usd) : '—'}
        </p>
        {data && data.cost_mgmt_reachable === false && (
          <p className="text-amber-400 text-xs mt-1" data-testid="cost-mgmt-warning">
            Cost data unavailable — check Cost Management config
          </p>
        )}
        {data && (data.daily_sdk_usd != null || data.daily_openai_usd != null) && (
          <div className="mt-1 text-xs text-gray-400 space-y-0.5">
            {(data.daily_sdk_usd ?? 0) > 0 && (
              <div><span className="text-gray-500">SDK </span>{formatCurrency(data.daily_sdk_usd ?? 0)}</div>
            )}
            {(data.daily_openai_usd ?? 0) > 0 && (
              <div><span className="text-gray-500">OpenAI </span>{formatCurrency(data.daily_openai_usd ?? 0)}</div>
            )}
          </div>
        )}
      </div>

      {data && budgetUsd > 0 && (
        <div className="flex-1 min-w-[150px] bg-gray-700 rounded p-3">
          <p className="text-gray-400 text-xs uppercase tracking-wide">Budget Used</p>
          <p className="text-gray-100 text-xl font-bold">{budgetPct}%</p>
          <div className="mt-2 w-full bg-gray-600 rounded-full h-2">
            <div
              data-testid="budget-progress"
              role="progressbar"
              aria-valuenow={budgetPct}
              aria-valuemin={0}
              aria-valuemax={100}
              className={`h-2 rounded-full ${barColor}`}
              style={{ width: `${Math.min(budgetPct, 100)}%` }}
            />
          </div>
        </div>
      )}

      {/* STORY-496: Relabeled to "Monthly Foundry" */}
      <div className="flex-1 min-w-[150px] bg-gray-700 rounded p-3">
        <p className="text-gray-400 text-xs uppercase tracking-wide">Monthly Foundry</p>
        <p className="text-gray-100 text-xl font-bold">
          {data ? formatCurrency(data.total_monthly_spend_usd) : '—'}
        </p>
      </div>
      <div className="flex-1 min-w-[150px] bg-gray-700 rounded p-3">
        <p className="text-gray-400 text-xs uppercase tracking-wide">Active Agents</p>
        <p className="text-gray-100 text-xl font-bold">
          {data ? `${data.active_agents} / ${data.total_agents}` : '—'}
        </p>
      </div>
      <div className="flex-1 min-w-[150px] bg-gray-700 rounded p-3">
        <p className="text-gray-400 text-xs uppercase tracking-wide">Busy Agents</p>
        <p className="text-gray-100 text-xl font-bold">
          {data ? data.busy_agents ?? 0 : '—'}
        </p>
      </div>
      <div className="flex-1 min-w-[150px] bg-gray-700 rounded p-3">
        <p className="text-gray-400 text-xs uppercase tracking-wide">Active Stories</p>
        <p className="text-gray-100 text-xl font-bold">
          {data ? data.stories_in_progress : '—'}
        </p>
      </div>
      <div className="flex-1 min-w-[150px] bg-gray-700 rounded p-3">
        <p className="text-gray-400 text-xs uppercase tracking-wide">Queued Stories</p>
        <p className="text-gray-100 text-xl font-bold">
          {data ? totalQueued(data) : '—'}
        </p>
      </div>
      <div className="flex-1 min-w-[150px] bg-gray-700 rounded p-3">
        <p className="text-gray-400 text-xs uppercase tracking-wide">Health Score</p>
        <p className={`text-xl font-bold ${data ? healthColor(data.fleet_health_score) : 'text-gray-100'}`}>
          {data ? formatHealthPercent(data.fleet_health_score) : '—'}
        </p>
      </div>
    </div>
  );
}
