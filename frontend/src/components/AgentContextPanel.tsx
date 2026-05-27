import type { AgentContext } from '../types/api';

export function AgentContextPanel({ context }: { context: AgentContext }) {
  const { teams_link, blocker_status } = context;

  const isBlocked = blocker_status?.startsWith('Blocked:') ?? false;
  const isDecisionNeeded = blocker_status?.startsWith('Decision needed:') ?? false;

  return (
    <div className="space-y-3">
      {teams_link && (
        <a
          href={teams_link}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-blue-400 hover:text-blue-300 text-sm underline"
        >
          Open in Teams
        </a>
      )}

      {isBlocked && (
        <div className="bg-red-900/40 border border-red-700 text-red-300 rounded px-3 py-2 text-sm">
          {blocker_status}
        </div>
      )}

      {isDecisionNeeded && (
        <div className="bg-yellow-900/40 border border-yellow-700 text-yellow-300 rounded px-3 py-2 text-sm">
          {blocker_status}
        </div>
      )}
    </div>
  );
}
