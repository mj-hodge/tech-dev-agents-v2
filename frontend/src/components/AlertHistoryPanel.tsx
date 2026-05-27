import { useState } from 'react';
import { useAlerts } from '../hooks/useAlerts';

export function AlertHistoryPanel() {
  const { data, isLoading, isError } = useAlerts();
  const [filterAgent, setFilterAgent] = useState('');
  const [filterType, setFilterType] = useState('');

  if (isLoading) {
    return (
      <div className="p-4">
        <h2 className="text-xl font-bold text-gray-100 mb-4">Alert History</h2>
        <div className="animate-pulse space-y-2">
          {[1, 2, 3].map((i) => (
            <div key={i} className="bg-gray-700 rounded h-10" />
          ))}
        </div>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="p-4">
        <h2 className="text-xl font-bold text-gray-100 mb-4">Alert History</h2>
        <p className="text-red-400">Failed to load alerts</p>
      </div>
    );
  }

  const items = data?.alerts ?? [];
  const filtered = items.filter((item) => {
    if (filterAgent && !item.agent_name.includes(filterAgent)) return false;
    if (filterType && item.type !== filterType) return false;
    return true;
  });

  return (
    <div className="p-4">
      <h2 className="text-xl font-bold text-gray-100 mb-4">Alert History</h2>

      <div className="flex gap-3 mb-4 flex-wrap">
        <input
          type="text"
          placeholder="Filter by agent..."
          value={filterAgent}
          onChange={(e) => setFilterAgent(e.target.value)}
          className="bg-gray-700 text-gray-100 border border-gray-600 rounded px-3 py-1 text-sm focus:outline-none focus:border-blue-500"
        />
        <select
          value={filterType}
          onChange={(e) => setFilterType(e.target.value)}
          className="bg-gray-700 text-gray-100 border border-gray-600 rounded px-3 py-1 text-sm focus:outline-none focus:border-blue-500"
        >
          <option value="">All types</option>
          <option value="anomaly">Anomaly</option>
          <option value="threshold">Threshold</option>
          <option value="error">Error</option>
          <option value="offline">Offline</option>
        </select>
      </div>

      {filtered.length === 0 ? (
        <p className="text-gray-500 text-center py-8">No alerts found</p>
      ) : (
        <table className="w-full text-sm text-left">
          <thead className="text-gray-400 border-b border-gray-700">
            <tr>
              <th className="py-2 pr-4">Agent</th>
              <th className="py-2 pr-4">Type</th>
              <th className="py-2 pr-4">Message</th>
              <th className="py-2 pr-4">Time</th>
              <th className="py-2">Status</th>
            </tr>
          </thead>
          <tbody className="text-gray-300">
            {filtered.map((alert) => (
              <tr key={alert.id} className="border-b border-gray-800">
                <td className="py-2 pr-4">{alert.agent_name}</td>
                <td className="py-2 pr-4 capitalize">{alert.type}</td>
                <td className="py-2 pr-4">{alert.message}</td>
                <td className="py-2 pr-4 text-gray-500">
                  {new Date(alert.triggered_at).toLocaleString()}
                </td>
                <td className="py-2">
                  <span className={!alert.active ? 'text-green-400' : 'text-red-400'}>
                    {!alert.active ? 'Resolved' : 'Active'}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
