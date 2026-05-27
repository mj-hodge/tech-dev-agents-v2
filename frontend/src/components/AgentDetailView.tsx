import { useParams } from 'react-router-dom';
import { useAgent } from '../hooks/useAgent';
import { useAgentActions } from '../hooks/useAgentActions';
import { CostChart } from './CostChart';
import { ActivityTimeline } from './ActivityTimeline';
import { AgentContextPanel } from './AgentContextPanel';

function elapsedTime(isoString: string | null | undefined): string | null {
  if (!isoString) return null;
  const diffMs = Date.now() - new Date(isoString).getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return null;
  const hrs = Math.floor(diffMin / 60);
  const mins = diffMin % 60;
  if (hrs === 0) return `${diffMin}m`;
  if (mins === 0) return `${hrs}h`;
  return `${hrs}h ${mins}m`;
}

function extractPhaseNum(phase: string | null | undefined): number | null {
  if (!phase) return null;
  const m = phase.match(/Phase\s*(\d+)/i);
  return m ? parseInt(m[1], 10) : null;
}

export function AgentDetailView() {
  const { name } = useParams<{ name: string }>();
  const { data: agent, isLoading } = useAgent(name!);
  const { restart, pause } = useAgentActions(name!);

  const isPending = restart.isPending || pause.isPending;

  const handleRestart = () => {
    if (window.confirm('Restart agent?')) {
      restart.mutate(name!);
    }
  };

  const handlePause = () => {
    if (window.confirm('Pause agent?')) {
      pause.mutate(name!);
    }
  };

  if (isLoading) {
    return (
      <div className="p-4 space-y-4">
        <div className="animate-pulse bg-gray-700 rounded h-8 w-48" />
        <div className="animate-pulse bg-gray-700 rounded h-64" />
        <div className="animate-pulse bg-gray-700 rounded h-32" />
      </div>
    );
  }

  if (!agent) {
    return (
      <div className="p-4">
        <p className="text-red-400">Agent not found</p>
        <a href="/" className="text-blue-400 hover:underline text-sm mt-2 inline-block">
          ← Back to Dashboard
        </a>
      </div>
    );
  }

  // STORY-480: Phase tracking
  const phaseNum = extractPhaseNum((agent as any).current_phase);
  const phaseTotal = (agent as any).phase_total ?? null;
  const elapsed = elapsedTime((agent as any).phase_started_at);

  return (
    <div className="p-4 space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-2xl font-bold text-gray-100">{agent.name}</h1>
        <a href="/" className="text-blue-400 hover:underline text-sm">
          ← Back to Dashboard
        </a>
      </div>

      <div className="flex gap-3">
        <button
          onClick={handleRestart}
          disabled={isPending}
          className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white px-4 py-2 rounded text-sm transition-colors"
        >
          Restart
        </button>
        <button
          onClick={handlePause}
          disabled={isPending}
          className="bg-yellow-600 hover:bg-yellow-700 disabled:opacity-50 text-white px-4 py-2 rounded text-sm transition-colors"
        >
          Pause
        </button>
      </div>

      {agent.context && <AgentContextPanel context={agent.context} />}

      {/* STORY-480: Current Work card */}
      {agent.current_story && (
        <div className="bg-gray-700 rounded-lg p-4">
          <h2 className="text-lg font-semibold text-gray-200 mb-2">Current Work</h2>
          <p className="text-gray-300 text-sm mb-1">{agent.current_story}</p>
          {agent.current_phase && (
            <p className="text-gray-400 text-sm">{agent.current_phase}</p>
          )}
          {phaseNum !== null && phaseTotal !== null && (
            <p className="text-gray-400 text-sm mt-1">{phaseNum} of {phaseTotal}</p>
          )}
          {elapsed && (
            <p className="text-gray-500 text-xs mt-1">{elapsed}</p>
          )}
        </div>
      )}

      <div>
        <h2 className="text-lg font-semibold text-gray-200 mb-2">Cost History</h2>
        <CostChart data={agent.cost_history} />
      </div>

      <div>
        <h2 className="text-lg font-semibold text-gray-200 mb-2">Activity</h2>
        <ActivityTimeline entries={agent.activity_timeline} />
      </div>
    </div>
  );
}
