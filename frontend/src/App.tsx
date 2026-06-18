import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import ErrorBoundary from './components/ErrorBoundary';
import Layout from './components/layout/Layout';
import ProtectedRoute from './components/auth/ProtectedRoute';
import RequireRole from './components/auth/RequireRole';
import Login from './features/auth/Login';
import AcceptInvitation from './features/auth/AcceptInvitation';
import Dashboard from './features/cases/Dashboard';
import CasesList from './features/cases/CasesList';
import CaseDetail from './features/cases/CaseDetail';
import ArtifactsList from './features/artifacts/ArtifactsList';
import ArtifactMindMap from './features/artifacts/ArtifactMindMap';
import Integrations from './features/integrations/Integrations';
import Settings from './features/settings/Settings';
import UserManagement from './features/admin/UserManagement';
import TenantManagement from './features/superadmin/TenantManagement';
import TenantDetail from './features/superadmin/TenantDetail';
import IOCList from './features/iocs/IOCList';
import Playbooks from './features/playbooks/Playbooks';
import AlertsList from './features/alerts/AlertsList';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: (failureCount, error: any) => {
        if (error?.response?.status === 401 || error?.response?.status === 403) return false;
        return failureCount < 2;
      },
    },
  },
});

function App() {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/invite/:token" element={<AcceptInvitation />} />
            <Route element={<ProtectedRoute />}>
              <Route path="/" element={<Layout />}>
                <Route index element={<Dashboard />} />
                <Route path="cases" element={<CasesList />} />
                <Route path="cases/:id" element={<CaseDetail />} />
                <Route path="alerts" element={<AlertsList />} />
                <Route path="artifacts" element={<ArtifactsList />} />
                <Route path="iocs" element={<IOCList />} />
                <Route path="playbooks" element={<Playbooks />} />
                <Route path="mindmap" element={<ArtifactMindMap />} />
                <Route path="integrations" element={<Integrations />} />
                <Route path="settings" element={<Settings />} />
                <Route element={<RequireRole roles={['admin', 'super_admin']} />}>
                  <Route path="admin/users" element={<UserManagement />} />
                </Route>
                <Route element={<RequireRole roles={['super_admin']} />}>
                  <Route path="superadmin/tenants" element={<TenantManagement />} />
                  <Route path="superadmin/tenants/:id" element={<TenantDetail />} />
                </Route>
              </Route>
            </Route>
          </Routes>
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  );
}

export default App;
