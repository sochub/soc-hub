import { QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { queryClient } from './api/queryClient';
import ProfilePage from './features/profile/ProfilePage';
import ErrorBoundary from './components/ErrorBoundary';
import Layout from './components/layout/Layout';
import ProtectedRoute from './components/auth/ProtectedRoute';
import RequireRole from './components/auth/RequireRole';
import Login from './features/auth/Login';
import AcceptInvitation from './features/auth/AcceptInvitation';
import Dashboard from './features/cases/Dashboard';
import CasesList from './features/cases/CasesList';
import CaseDetail from './features/cases/CaseDetail';
import NotificationsPage from './features/notifications/NotificationsPage';
import ArtifactsList from './features/artifacts/ArtifactsList';
import ArtifactMindMap from './features/artifacts/ArtifactMindMap';
import Integrations from './features/integrations/Integrations';
import Settings from './features/settings/Settings';
import UserManagement from './features/admin/UserManagement';
import TenantManagement from './features/superadmin/TenantManagement';
import TenantDetail from './features/superadmin/TenantDetail';
import IOCList from './features/iocs/IOCList';
import Playbooks from './features/playbooks/Playbooks';
import AutomationsList from './features/automations/AutomationsList';
import WorkflowEditor from './features/automations/WorkflowEditor';
import RunDetail from './features/automations/RunDetail';
import AlertsList from './features/alerts/AlertsList';
import ApiDocs from './features/docs/ApiDocs';

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
                <Route path="notifications" element={<NotificationsPage />} />
                <Route path="alerts" element={<AlertsList />} />
                <Route path="artifacts" element={<ArtifactsList />} />
                <Route path="iocs" element={<IOCList />} />
                <Route path="playbooks" element={<Playbooks />} />
                <Route path="automations" element={<AutomationsList />} />
                <Route path="automations/runs/:runId" element={<RunDetail />} />
                <Route path="automations/:id" element={<WorkflowEditor />} />
                <Route path="mindmap" element={<ArtifactMindMap />} />
                <Route path="integrations" element={<Integrations />} />
                <Route path="profile" element={<ProfilePage />} />
                <Route path="settings" element={<Settings />} />
                <Route path="docs" element={<ApiDocs />} />
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
