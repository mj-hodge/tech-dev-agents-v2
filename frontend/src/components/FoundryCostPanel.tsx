/**
 * STORY-576: Fleet-wide Foundry cost panel — 7-day daily cost stacked by
 * model deployment (Opus / Sonnet / Haiku).
 *
 * Mounted in DashboardLayout below BudgetGauge.
 * Warning banner fires when today's total exceeds $200.
 */

import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from 'recharts';
import { useFoundryCost } from '../hooks/useFoundryCost';

const CHART_WIDTH = 700;
const CHART_HEIGHT = 300;
const WARNING_THRESHOLD_USD = 200;

function formatUsd(value: number): string {
  return value.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  });
}

function formatCacheAge(seconds: number): string {
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m ago`;
}

export function FoundryCostPanel() {
  const { data, isLoading, isError } = useFoundryCost(7);

  // Loading state
  if (isLoading) {
    return (
      <div data-testid="foundry-cost-panel" className="bg-gray-800 border border-gray-700 rounded-lg mx-4 mb-4 px-4 py-4">
        <div className="animate-pulse space-y-3">
          <div className="h-4 bg-gray-700 rounded w-48" />
          <div className="h-[300px] bg-gray-700 rounded" />
        </div>
      </div>
    );
  }

  // Error state
  if (isError) {
    return (
      <div data-testid="foundry-cost-panel" className="bg-gray-800 border border-gray-700 rounded-lg mx-4 mb-4 px-4 py-4">
        <p className="text-red-400 text-sm">Failed to load Foundry cost data</p>
      </div>
    );
  }

  // Empty data
  if (!data || data.daily.length === 0) {
    return (
      <div data-testid="foundry-cost-panel" className="bg-gray-800 border border-gray-700 rounded-lg mx-4 mb-4 px-4 py-4">
        <h3 className="text-gray-100 font-semibold text-sm mb-2">Foundry Cost (7 Day)</h3>
        <p className="text-gray-500 text-sm">No cost data available</p>
      </div>
    );
  }

  // Compute totals
  const totalSpend = data.daily.reduce((sum, d) => sum + d.total_usd, 0);
  const todayEntry = data.daily[data.daily.length - 1];
  const todayTotal = todayEntry?.total_usd ?? 0;
  const showWarning = todayTotal > WARNING_THRESHOLD_USD;

  // STORY-736: When all daily entries are zero, show unavailable message instead of all-zero chart
  if (totalSpend === 0) {
    return (
      <div data-testid="foundry-cost-panel" className="bg-gray-800 border border-gray-700 rounded-lg mx-4 mb-4 px-4 py-4">
        <h3 className="text-gray-100 font-semibold text-sm mb-2">Foundry Cost (7 Day)</h3>
        <p className="text-gray-500 text-sm">Cost data unavailable — Azure Cost Management may be unreachable or unconfigured</p>
      </div>
    );
  }

  return (
    <div data-testid="foundry-cost-panel" className="bg-gray-800 border border-gray-700 rounded-lg mx-4 mb-4 px-4 py-4">
      {/* Warning banner */}
      {showWarning && (
        <div
          data-testid="foundry-cost-warning"
          className="bg-red-900/50 border border-red-700 rounded-md px-3 py-2 mb-3 flex items-center gap-2 text-sm text-red-200"
        >
          <span>&#9888;</span>
          <span>Today&apos;s Foundry spend exceeds $200 ({formatUsd(todayTotal)})</span>
        </div>
      )}

      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-gray-100 font-semibold text-sm">Foundry Cost (7 Day)</h3>
        <div className="flex items-center gap-3 text-xs text-gray-400">
          <span data-testid="foundry-cost-total">
            Total: <span className="text-gray-100 font-mono font-medium">{formatUsd(totalSpend)}</span>
          </span>
          {data.fetched_at && (
            <span>Updated {formatCacheAge(data.cache_age_seconds)}</span>
          )}
        </div>
      </div>

      {/* Stacked area chart */}
      <div data-testid="foundry-cost-chart" className="w-full overflow-x-auto">
        <AreaChart width={CHART_WIDTH} height={CHART_HEIGHT} data={data.daily} stackOffset="none">
          <defs>
            <linearGradient id="opusGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#ef4444" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#ef4444" stopOpacity={0} />
            </linearGradient>
            <linearGradient id="sonnetGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#eab308" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#eab308" stopOpacity={0} />
            </linearGradient>
            <linearGradient id="haikuGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#22c55e" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#22c55e" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis dataKey="date" stroke="#9ca3af" tick={{ fontSize: 12 }} />
          <YAxis stroke="#9ca3af" tickFormatter={(v) => `$${v}`} tick={{ fontSize: 12 }} />
          <Tooltip
            contentStyle={{ backgroundColor: '#1f2937', border: '1px solid #374151', borderRadius: '6px' }}
            labelStyle={{ color: '#e5e7eb' }}
            formatter={(value: number, name: string) => [`$${value.toFixed(2)}`, name]}
          />
          <Legend />
          <Area
            type="monotone"
            dataKey="opus_usd"
            name="Opus"
            stroke="#ef4444"
            fill="url(#opusGradient)"
            stackId="cost"
          />
          <Area
            type="monotone"
            dataKey="sonnet_usd"
            name="Sonnet"
            stroke="#eab308"
            fill="url(#sonnetGradient)"
            stackId="cost"
          />
          <Area
            type="monotone"
            dataKey="haiku_usd"
            name="Haiku"
            stroke="#22c55e"
            fill="url(#haikuGradient)"
            stackId="cost"
          />
        </AreaChart>
      </div>
    </div>
  );
}
