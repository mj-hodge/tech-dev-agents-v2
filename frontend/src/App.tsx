import { Routes, Route, Navigate } from 'react-router-dom';
import { useIsAuthenticated } from '@azure/msal-react';
import { LoginPage } from './components/LoginPage';
import { DashboardLayout } from './components/DashboardLayout';
import { AgentGrid } from './components/AgentGrid';
import { AgentDetailView } from './components/AgentDetailView';
import { AlertHistoryPanel } from './components/AlertHistoryPanel';
import { WorkHistoryPanel } from './components/WorkHistoryPanel';

const SKIP_AUTH = import.meta.env.VITE_SKIP_AUTH === 'true';

function AuthGuard({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useIsAuthenticated();
  if (SKIP_AUTH || isAuthenticated) {
    return <>{children}</>;
  }
  return <Navigate to="/login" replace />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/"
        element={
          <AuthGuard>
            <DashboardLayout />
          </AuthGuard>
        }
      >
        <Route index element={<AgentGrid />} />
        <Route path="agents/:name" element={<AgentDetailView />} />
        <Route path="alerts" element={<AlertHistoryPanel />} />
        <Route path="work-history" element={<WorkHistoryPanel />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
