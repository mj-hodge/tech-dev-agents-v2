import { usePresence } from "../hooks/usePresence";
import type { AgentPresenceItem } from "../types/api";

const STATE_CONFIG: Record<string, { dot: string; text: string; label: string }> = {
  working:      { dot: "bg-green-400",  text: "text-green-400",  label: "Working" },
  idle:         { dot: "bg-gray-400",   text: "text-gray-400",   label: "Idle" },
  rate_limited: { dot: "bg-yellow-400", text: "text-yellow-400", label: "Rate Limited" },
  offline:      { dot: "bg-red-400",    text: "text-red-400",    label: "Offline" },
};

export function PresencePanel() {
  const { data, isLoading, isError } = usePresence();

  if (isLoading) return <PresenceSkeleton />;
  if (isError || !data) return <PresenceError />;

  return (
    <div className="bg-gray-800 rounded-lg p-4 mb-6">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-medium text-gray-300">Agent Presence</h3>
        {data.cached && (
          <span className="text-xs text-gray-500">cached</span>
        )}
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        {data.agents.map((agent) => (
          <PresenceBubble key={agent.name} agent={agent} />
        ))}
      </div>
    </div>
  );
}

function PresenceBubble({ agent }: { agent: AgentPresenceItem }) {
  const config = STATE_CONFIG[agent.state] ?? STATE_CONFIG.offline;
  return (
    <div className="flex items-center gap-2 p-2 rounded bg-gray-700/50" title={agent.detail ?? ""}>
      <span className={`h-3 w-3 rounded-full ${config.dot}`} />
      <div>
        <div className="text-sm text-gray-100">{agent.name}</div>
        <div className={`text-xs ${config.text}`}>{config.label}</div>
      </div>
    </div>
  );
}

function PresenceSkeleton() {
  return (
    <div className="bg-gray-800 rounded-lg p-4 mb-6">
      <div className="h-4 w-32 bg-gray-700 rounded animate-pulse mb-3" />
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="h-12 bg-gray-700 rounded animate-pulse" />
        ))}
      </div>
    </div>
  );
}

function PresenceError() {
  return (
    <div className="bg-gray-800 rounded-lg p-4 mb-6">
      <p className="text-sm text-red-400">Failed to load presence data</p>
    </div>
  );
}
