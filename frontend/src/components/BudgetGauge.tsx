/**
 * BudgetGauge — displays a token budget consumption gauge.
 * STORY-508 (AC-12): Receives percent_used (from QuotaInfo) and renders
 * a visual progress bar with colour-coded levels.
 */

interface BudgetGaugeProps {
  /** Percentage of budget consumed, 0–100. From QuotaInfo.percent_used. */
  percentUsed?: number;
  /** Display label (default: "Daily Budget") */
  label?: string;
  /** Nominal daily budget in USD (for display only) */
  budgetUsd?: number;
}

function gaugeColor(percent: number): string {
  if (percent >= 90) return 'bg-red-500';
  if (percent >= 70) return 'bg-yellow-500';
  return 'bg-green-500';
}

function gaugeTextColor(percent: number): string {
  if (percent >= 90) return 'text-red-400';
  if (percent >= 70) return 'text-yellow-400';
  return 'text-green-400';
}

export function BudgetGauge({
  percentUsed,
  label = 'Daily Budget',
  budgetUsd,
}: BudgetGaugeProps) {
  const pct = percentUsed ?? 0;
  const clamped = Math.min(100, Math.max(0, pct));

  return (
    <div className="bg-gray-800 border border-gray-700 rounded-lg mx-4 mb-4 px-4 py-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-gray-100 font-semibold text-sm">{label}</span>
        <span className={`text-sm font-mono font-medium ${gaugeTextColor(clamped)}`}>
          {percentUsed != null ? `${clamped.toFixed(1)}%` : '--'}
          {budgetUsd != null && (
            <span className="text-gray-500 text-xs ml-1">(${budgetUsd}/day)</span>
          )}
        </span>
      </div>

      {/* Progress bar */}
      <div className="w-full bg-gray-700 rounded-full h-2 overflow-hidden">
        <div
          className={`h-2 rounded-full transition-all duration-500 ${gaugeColor(clamped)}`}
          style={{ width: `${clamped}%` }}
        />
      </div>

      {percentUsed == null && (
        <p className="text-gray-500 text-xs mt-1">Budget data unavailable</p>
      )}
    </div>
  );
}
