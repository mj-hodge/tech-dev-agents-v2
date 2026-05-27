// STORY-496: Extended status support (rate_limited, working)
type Status = 'active' | 'online' | 'idle' | 'error' | 'offline' | 'rate_limited' | 'working' | 'stuck' | 'unreachable' | string;

const statusConfig: Record<string, { dot: string; text: string; label?: string }> = {
  active: { dot: 'bg-green-500', text: 'text-green-400' },
  online: { dot: 'bg-green-500', text: 'text-green-400' },
  idle: { dot: 'bg-yellow-500', text: 'text-yellow-400' },
  error: { dot: 'bg-red-500', text: 'text-red-400' },
  offline: { dot: 'bg-gray-500', text: 'text-gray-400' },
  stuck: { dot: 'bg-orange-500', text: 'text-orange-400' },
  unreachable: { dot: 'bg-gray-600', text: 'text-gray-500' },
  // STORY-496: new statuses
  rate_limited: { dot: 'bg-amber-500', text: 'text-amber-400', label: 'Rate Limited' },
  working: { dot: 'bg-green-500', text: 'text-green-400', label: 'Working' },
};

const fallback = { dot: 'bg-gray-500', text: 'text-gray-400' };

export function StatusBadge({
  status,
  detail,
}: {
  status: string;
  detail?: string;
}) {
  const config = statusConfig[status] || fallback;
  const label = config.label ?? status;
  return (
    <span className="inline-flex items-center gap-1.5" data-testid="status-badge">
      <span className={`inline-block w-2 h-2 rounded-full ${config.dot}`} />
      <span className={`text-sm ${config.text}`}>
        {label}
        {detail && (
          <span className="ml-1 text-xs text-gray-400">— {detail}</span>
        )}
      </span>
    </span>
  );
}
