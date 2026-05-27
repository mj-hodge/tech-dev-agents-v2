import { Link } from 'react-router-dom';
import { useAlerts } from '../hooks/useAlerts';

export function AlertBanner() {
  const { data, isLoading } = useAlerts();

  if (isLoading || !data || data.active_count === 0) {
    return null;
  }

  const label = data.active_count === 1 ? 'active alert' : 'active alerts';

  return (
    <div className="bg-yellow-600 text-white px-4 py-2 text-sm" data-testid="alert-banner" role="alert">
      <Link to="/alerts" className="flex items-center gap-2 hover:underline">
        <span>⚠</span>
        <span>{data.active_count} {label}</span>
      </Link>
    </div>
  );
}
