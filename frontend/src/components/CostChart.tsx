import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from 'recharts';
import type { CostHistoryEntry } from '../types/api';

type MultiSeriesEntry = {
  date: string;
  foundry_cost_usd: number;
  sdk_cost_usd: number;
  openai_cost_usd: number;
};

function isMultiSeries(data: CostHistoryEntry[]): data is (CostHistoryEntry & MultiSeriesEntry)[] {
  return data.length > 0 && 'foundry_cost_usd' in data[0];
}

// Use a fixed width so recharts renders SVG in JSDOM (ResponsiveContainer doesn't
// render children in test environments without real layout dimensions).
const CHART_WIDTH = 700;
const CHART_HEIGHT = 300;

export function CostChart({ data }: { data: CostHistoryEntry[] | undefined | null }) {
  if (!data || data.length === 0) {
    return (
      <div data-testid="cost-chart" className="w-full h-[300px] flex items-center justify-center text-gray-500">
        No cost data available
      </div>
    );
  }

  if (isMultiSeries(data)) {
    return (
      <div data-testid="cost-chart" className="w-full overflow-x-auto">
        <AreaChart width={CHART_WIDTH} height={CHART_HEIGHT} data={data} stackOffset="none">
          <defs>
            <linearGradient id="foundryGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
            </linearGradient>
            <linearGradient id="sdkGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#10b981" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
            </linearGradient>
            <linearGradient id="openaiGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#f59e0b" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis dataKey="date" stroke="#9ca3af" />
          <YAxis stroke="#9ca3af" tickFormatter={(v) => `$${v}`} />
          <Tooltip />
          <Legend />
          <Area
            type="monotone"
            dataKey="foundry_cost_usd"
            name="Foundry"
            stroke="#3b82f6"
            fill="url(#foundryGradient)"
            stackId="cost"
          />
          <Area
            type="monotone"
            dataKey="sdk_cost_usd"
            name="SDK"
            stroke="#10b981"
            fill="url(#sdkGradient)"
            stackId="cost"
          />
          <Area
            type="monotone"
            dataKey="openai_cost_usd"
            name="OpenAI"
            stroke="#f59e0b"
            fill="url(#openaiGradient)"
            stackId="cost"
          />
        </AreaChart>
      </div>
    );
  }

  // Legacy single-series format
  return (
    <div data-testid="cost-chart" className="w-full overflow-x-auto">
      <AreaChart width={CHART_WIDTH} height={CHART_HEIGHT} data={data}>
        <defs>
          <linearGradient id="costGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
            <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
        <XAxis dataKey="date" stroke="#9ca3af" />
        <YAxis stroke="#9ca3af" tickFormatter={(v) => `$${v}`} />
        <Tooltip formatter={(v) => [`$${v}`, 'Cost']} />
        <Area
          type="monotone"
          dataKey="cost"
          stroke="#3b82f6"
          fill="url(#costGradient)"
        />
      </AreaChart>
    </div>
  );
}
