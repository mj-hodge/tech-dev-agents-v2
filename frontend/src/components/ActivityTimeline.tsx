import type { ActivityEntry } from '../types/api';

const typeIcons: Record<ActivityEntry['type'], string> = {
  commit: '⑂',
  phase: '→',
  message: '💬',
  error: '✗',
  restart: '↺',
};

function relativeTime(isoString: string): string {
  const now = Date.now();
  const then = new Date(isoString).getTime();
  const diffMs = now - then;
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHr = Math.floor(diffMin / 60);

  if (diffSec < 60) return 'just now';
  if (diffMin < 60) return `${diffMin}m ago`;
  if (diffHr < 24) return `${diffHr}h ago`;
  return `${Math.floor(diffHr / 24)}d ago`;
}

export function ActivityTimeline({ entries }: { entries: ActivityEntry[] }) {
  return (
    <div className="space-y-3">
      {entries.map((entry, index) => (
        <div key={index} className="flex gap-3 relative pl-4 border-l-2 border-gray-700">
          <span className="text-gray-500 text-sm mt-0.5">{typeIcons[entry.type]}</span>
          <div className="flex-1 min-w-0">
            <p className="text-gray-300 text-sm">{entry.description}</p>
            <p
              className="text-gray-500 text-xs mt-0.5"
              title={new Date(entry.timestamp).toLocaleString()}
            >
              {relativeTime(entry.timestamp)}
            </p>
          </div>
        </div>
      ))}
    </div>
  );
}
