import { Outlet, useNavigate, Link, useLocation } from 'react-router-dom';
import { clearApiKey } from '../api/client';
import { AlertBanner } from './AlertBanner';
import { FleetOverviewBar } from './FleetOverviewBar';
import { AlertStatusPanel } from './AlertStatusPanel';
import { BudgetGauge } from './BudgetGauge';
import { FoundryCostPanel } from './FoundryCostPanel';

function NavLink({ to, label }: { to: string; label: string }) {
  const location = useLocation();
  const isActive = location.pathname === to;
  return (
    <Link
      to={to}
      className={`px-3 py-1 rounded text-sm transition-colors ${
        isActive ? 'bg-gray-600 text-white' : 'text-gray-400 hover:text-gray-200'
      }`}
    >
      {label}
    </Link>
  );
}

export function DashboardLayout() {
  const navigate = useNavigate();

  const handleLogout = () => {
    clearApiKey();
    navigate('/login');
  };

  return (
    <div className="min-h-screen bg-gray-900 text-gray-100">
      <header className="bg-gray-800 border-b border-gray-700">
        <div className="flex items-center justify-between px-4 py-3">
          <div className="flex items-center gap-6">
            <h1 className="text-xl font-bold">Ops Console</h1>
            <nav className="flex gap-2">
              <NavLink to="/" label="Fleet" />
              <NavLink to="/work-history" label="Work History" />
              <NavLink to="/alerts" label="Alerts" />
            </nav>
          </div>
          <button
            onClick={handleLogout}
            className="bg-gray-700 hover:bg-gray-600 text-gray-300 px-3 py-1 rounded text-sm transition-colors"
          >
            Logout
          </button>
        </div>
        <AlertBanner />
      </header>
      <FleetOverviewBar />
      <div className="flex gap-4 px-4 pt-4">
        <div className="flex-1 min-w-0">
          <AlertStatusPanel />
        </div>
        <div className="w-64 flex-shrink-0">
          <BudgetGauge />
        </div>
      </div>
      <FoundryCostPanel />
      <main>
        <Outlet />
      </main>
    </div>
  );
}
