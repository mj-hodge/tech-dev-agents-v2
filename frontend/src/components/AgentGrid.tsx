import { useAgents } from '../hooks/useAgents';
import { AgentCard } from './AgentCard';
import { DispatchQueue } from './DispatchQueue';

export function AgentGrid() {
  const { data, isLoading, isError } = useAgents();

  if (isLoading) {
    return (
      <div>
        <div className="pt-4">
          <DispatchQueue />
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 p-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="animate-pulse bg-gray-800 rounded-lg h-36 border border-gray-700" />
          ))}
        </div>
      </div>
    );
  }

  if (isError) {
    return (
      <div>
        <div className="pt-4">
          <DispatchQueue />
        </div>
        <div className="p-4 text-center">
          <p className="text-red-400 mb-3">Failed to load agents</p>
          <button
            onClick={() => window.location.reload()}
            className="bg-gray-700 hover:bg-gray-600 text-gray-100 px-4 py-2 rounded transition-colors"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!data || data.length === 0) {
    return (
      <div>
        <div className="pt-4">
          <DispatchQueue />
        </div>
        <div className="p-8 text-center text-gray-500">
          No agents registered
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="pt-4">
        <DispatchQueue />
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 p-4">
        {data.map((agent) => (
          <AgentCard key={agent.name} agent={agent} />
        ))}
      </div>
    </div>
  );
}
