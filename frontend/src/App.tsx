import { lazy } from 'react';
import { QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { queryClient } from './api/queryClient';
import ErrorBoundary from './components/ErrorBoundary';
import Layout from './components/layout/Layout';
import HubLayout from './components/layout/HubLayout';
import ProtectedRoute from './components/auth/ProtectedRoute';
import RequireRole from './components/auth/RequireRole';
import Login from './features/auth/Login';
import AcceptInvitation from './features/auth/AcceptInvitation';
import SettingsHub from './features/settings/SettingsHub';

// Route-level code splitting: heavy pages (charts, React Flow, Scalar) load on first visit.
const Dashboard = lazy(() => import('./features/cases/Dashboard'));
const AuditLogPage = lazy(() => import('./features/audit/AuditLogPage'));
const MyQueue = lazy(() => import('./features/queue/MyQueue'));
const CasesList = lazy(() => import('./features/cases/CasesList'));
const CaseDetail = lazy(() => import('./features/cases/CaseDetail'));
const AlertsList = lazy(() => import('./features/alerts/AlertsList'));
const ArtifactsList = lazy(() => import('./features/artifacts/ArtifactsList'));
const IOCList = lazy(() => import('./features/iocs/IOCList'));
const ArtifactMindMap = lazy(() => import('./features/artifacts/ArtifactMindMap'));
const Playbooks = lazy(() => import('./features/playbooks/Playbooks'));
const AutomationsList = lazy(() => import('./features/automations/AutomationsList'));
const WorkflowEditor = lazy(() => import('./features/automations/WorkflowEditor'));
const RunDetail = lazy(() => import('./features/automations/RunDetail'));
const Settings = lazy(() => import('./features/settings/Settings'));
const ProfilePage = lazy(() => import('./features/profile/ProfilePage'));
const Integrations = lazy(() => import('./features/integrations/Integrations'));
const UserManagement = lazy(() => import('./features/admin/UserManagement'));
const TenantManagement = lazy(() => import('./features/superadmin/TenantManagement'));
const TenantDetail = lazy(() => import('./features/superadmin/TenantDetail'));
const ApiDocs = lazy(() => import('./features/docs/ApiDocs'));

/** Old URL → new URL, keeping the rest of the path, query and hash (bookmarks, shared links). */
function Moved({ from, to }: { from: string; to: string }) {
  const { pathname, search, hash } = useLocation();
  return <Navigate replace to={to + pathname.slice(from.length) + search + hash} />;
}

const MOVED: [string, string][] = [
  ['/automations', '/workflows'],
  ['/mindmap', '/graph'],
  ['/artifacts', '/observables'],
  ['/iocs', '/observables/iocs'],
  ['/notifications', '/queue'],
  ['/integrations', '/settings/integrations'],
  ['/profile', '/settings/profile'],
  ['/admin/users', '/settings/users'],
  ['/superadmin/tenants', '/settings/tenants'],
];

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
                <Route path="queue" element={<MyQueue />} />
                <Route path="cases" element={<CasesList />} />
                <Route path="cases/:id" element={<CaseDetail />} />
                <Route path="alerts" element={<AlertsList />} />
                <Route path="observables" element={<HubLayout title="observables" tabs={[
                  { label: 'Artifacts', to: '/observables', end: true },
                  { label: 'IOCs', to: '/observables/iocs' },
                ]} />}>
                  <Route index element={<ArtifactsList />} />
                  <Route path="iocs" element={<IOCList />} />
                </Route>
                <Route path="graph" element={<ArtifactMindMap />} />
                <Route path="playbooks" element={<Playbooks />} />
                <Route path="workflows" element={<AutomationsList />} />
                <Route path="workflows/runs/:runId" element={<RunDetail />} />
                <Route path="workflows/:id" element={<WorkflowEditor />} />
                <Route path="docs" element={<ApiDocs />} />
                <Route path="settings" element={<SettingsHub />}>
                  <Route index element={<Settings />} />
                  <Route path="profile" element={<ProfilePage />} />
                  <Route path="integrations" element={<Integrations />} />
                  <Route element={<RequireRole roles={['admin', 'super_admin']} />}>
                    <Route path="users" element={<UserManagement />} />
                    <Route path="audit" element={<AuditLogPage />} />
                  </Route>
                  <Route element={<RequireRole roles={['super_admin']} />}>
                    <Route path="tenants" element={<TenantManagement />} />
                    <Route path="tenants/:id" element={<TenantDetail />} />
                  </Route>
                </Route>
                {MOVED.map(([from, to]) => (
                  <Route key={from} path={`${from.slice(1)}/*`} element={<Moved from={from} to={to} />} />
                ))}
              </Route>
            </Route>
          </Routes>
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  );
}

export default App;
