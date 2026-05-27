/**
 * AlertStatusPanel — displays currently firing Grafana alerts.
 * STORY-508 (AC-12): Fetches from GET /api/alerts/active and renders
 * a list of active alerts with name, severity, and start time.
 * Shows "No active alerts" when the fleet is healthy.
 */

import { useEffect, useState } from 'react';
import { getApiKey } from '../api/client';

interface ActiveAlert {
  name: string;
  severity: string;
  started_at: string;
  labels: Record<string, string>;
  annotations: Record<string, string>;
}

interface ActiveAlertsResponse {
  alerts: ActiveAlert[];
  count: number;
  fetched_at: string;
  error?: string;
}

function severityColor(severity: string): string {
  switch (severity.toLowerCase()) {
    case 'critical':
      return 'text-red-400 bg-red-600/20';
    case 'warning':
      return 'text-yellow-400 bg-yellow-600/20';
    case 'info':
      return 'text-blue-400 bg-blue-600/20';
    default:
      return 'text-gray-400 bg-gray-600/20';
  }
}

function timeAgo(isoDate: string): string {
  const seconds = Math.floor((Date.now() - new Date(isoDate).getTime()) / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ago`;
}

export function AlertStatusPanel() {
  const [data, setData] = useState<ActiveAlertsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function fetchAlerts() {
      const apiKey = getApiKey();
      const headers: Record<string, string> = apiKey ? { 'X-API-Key': apiKey } : {};
      try {
        // Primary: Grafana-sourced active alerts
        const resp = await fetch('/api/alerts/active', { headers });
        if (resp.ok) {
          const json: ActiveAlertsResponse = await resp.json();
          if (!cancelled) {
            setData(json);
            setError(null);
            setLoading(false);
          }
          return;
        }
        // 404 = Grafana webhook disabled — fall back to Loki-sourced alerts
        if (resp.status === 404) {
          const fbResp = await fetch('/api/alerts', { headers });
          if (!fbResp.ok) {
            throw new Error(`HTTP ${fbResp.status}`);
          }
          const fbJson: { alerts: Array<{ type: string; severity: string; triggered_at: string; agent_name: string; active: boolean }>; fetched_at?: string } = await fbResp.json();
          const active = (fbJson.alerts || []).filter((a) => a.active);
          // Adapt Loki shape → panel's expected shape
          const adapted: ActiveAlertsResponse = {
            alerts: active.map((a) => ({
              name: a.type,
              severity: a.severity,
              // triggered_at is a 19-digit ns string from Loki; slice off the last
              // 6 chars to get ms (Number() would lose precision past 2^53).
              started_at: new Date(Number(String(a.triggered_at).slice(0, -6))).toISOString(),
              labels: { agent: a.agent_name },
              annotations: {},
            })),
            count: active.length,
            fetched_at: fbJson.fetched_at ?? new Date().toISOString(),
          };
          if (!cancelled) {
            setData(adapted);
            setError(null);
            setLoading(false);
          }
          return;
        }
        throw new Error(`HTTP ${resp.status}`);
      } catch (err) {
        if (!cancelled) {
          setError('Failed to load active alerts');
          setLoading(false);
        }
      }
    }

    fetchAlerts();
    // Refresh every 30 seconds
    const interval = setInterval(fetchAlerts, 30_000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return (
    <div className="bg-gray-800 border border-gray-700 rounded-lg mx-4 mb-4">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700">
        <span className="text-gray-100 font-semibold text-sm">Active Alerts</span>
        {data && data.count > 0 && (
          <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-red-600/20 text-red-400">
            {data.count} firing
          </span>
        )}
      </div>

      <div className="px-4 py-3">
        {loading && (
          <div className="animate-pulse bg-gray-700 rounded h-8" />
        )}

        {!loading && error && (
          <p className="text-red-400 text-sm py-1">{error}</p>
        )}

        {!loading && data && data.error === 'grafana_unreachable' && (
          <p className="text-yellow-400 text-sm py-1">
            ⚠️ Grafana unreachable — alert status unavailable
          </p>
        )}

        {!loading && !error && data && data.count === 0 && !data.error && (
          <p className="text-green-400 text-sm py-1">✓ No active alerts</p>
        )}

        {!loading && !error && data && data.alerts.length > 0 && (
          <ul className="space-y-2">
            {data.alerts.map((alert, i) => (
              <li
                key={`${alert.name}-${i}`}
                className="flex items-center justify-between text-sm"
              >
                <div className="flex items-center gap-2 min-w-0">
                  <span
                    className={`px-2 py-0.5 rounded text-xs font-medium flex-shrink-0 ${severityColor(alert.severity)}`}
                  >
                    {alert.severity}
                  </span>
                  <span className="text-gray-200 font-mono truncate">{alert.name}</span>
                  {alert.labels?.agent && (
                    <span className="text-gray-400 text-xs flex-shrink-0">
                      [{alert.labels.agent}]
                    </span>
                  )}
                </div>
                <span className="text-gray-500 text-xs flex-shrink-0 ml-2">
                  {timeAgo(alert.started_at)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
