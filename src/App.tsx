import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { Loading } from './components/Feedback'
import { LoginPage } from './pages/LoginPage'
import { AuthProvider, useAuth } from './state/AuthContext'
import { EnvironmentProvider } from './state/EnvironmentContext'
import { LiveProvider } from './state/LiveContext'

const page = <T extends string>(loader: () => Promise<Record<T, React.ComponentType>>, name: T) => lazy(() => loader().then((module) => ({ default: module[name] })))

const DashboardPage = page(() => import('./pages/DashboardPage'), 'DashboardPage')
const ApplicationsPage = page(() => import('./pages/ApplicationsPage'), 'ApplicationsPage')
const ApplicationDetailPage = page(() => import('./pages/ApplicationDetailPage'), 'ApplicationDetailPage')
const ApplicationFormPage = page(() => import('./pages/ApplicationFormPage'), 'ApplicationFormPage')
const DeploymentsPage = page(() => import('./pages/DeploymentsPage'), 'DeploymentsPage')
const DeploymentDetailPage = page(() => import('./pages/DeploymentDetailPage'), 'DeploymentDetailPage')
const IncidentsPage = page(() => import('./pages/IncidentsPage'), 'IncidentsPage')
const IncidentDetailPage = page(() => import('./pages/IncidentDetailPage'), 'IncidentDetailPage')
const MetricsPage = page(() => import('./pages/MetricsPage'), 'MetricsPage')
const LogsPage = page(() => import('./pages/LogsPage'), 'LogsPage')
const DoraPage = page(() => import('./pages/DoraPage'), 'DoraPage')
const AgentsPage = page(() => import('./pages/AgentsPage'), 'AgentsPage')
const InfrastructurePage = page(() => import('./pages/InfrastructurePage'), 'InfrastructurePage')
const PoliciesPage = page(() => import('./pages/PoliciesPage'), 'PoliciesPage')
const SettingsPage = page(() => import('./pages/SettingsPage'), 'SettingsPage')
const FlagsPage = page(() => import('./pages/FlagsPage'), 'FlagsPage')
const NotFoundPage = page(() => import('./pages/NotFoundPage'), 'NotFoundPage')

function Workspace() {
  const { user, ready } = useAuth()
  if (!ready) return <div className="login-page"><Loading label="Connecting to NexOps…" /></div>
  if (!user) return <LoginPage />
  return (
    <EnvironmentProvider>
      <LiveProvider>
        <AppShell>
          <Suspense fallback={<div className="work-page"><Loading label="Loading workspace…" /></div>}>
            <Routes>
              <Route path="/" element={<DashboardPage />} />
              <Route path="/dashboard" element={<Navigate to="/" replace />} />
              <Route path="/applications" element={<ApplicationsPage />} />
              <Route path="/applications/new" element={<ApplicationFormPage />} />
              <Route path="/applications/:slug/edit" element={<ApplicationFormPage />} />
              <Route path="/applications/:slug" element={<ApplicationDetailPage />} />
              <Route path="/deployments" element={<DeploymentsPage />} />
              <Route path="/deployments/:deploymentId" element={<DeploymentDetailPage />} />
              <Route path="/incidents" element={<IncidentsPage />} />
              <Route path="/incidents/:incidentId" element={<IncidentDetailPage />} />
              <Route path="/metrics" element={<MetricsPage />} />
              <Route path="/logs" element={<LogsPage />} />
              <Route path="/dora" element={<DoraPage />} />
              <Route path="/flags" element={<FlagsPage />} />
              <Route path="/agents" element={<AgentsPage />} />
              <Route path="/infrastructure" element={<InfrastructurePage />} />
              <Route path="/policies" element={<PoliciesPage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="*" element={<NotFoundPage />} />
            </Routes>
          </Suspense>
        </AppShell>
      </LiveProvider>
    </EnvironmentProvider>
  )
}

export default function App() {
  return <AuthProvider><Workspace /></AuthProvider>
}
