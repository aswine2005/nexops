import { useEffect, useMemo, useState } from 'react'
import { Bell, ChevronDown, Command, LogOut, Menu, Search, Siren as SirenIcon } from 'lucide-react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { Application, Deployment, Incident } from '../api/types'
import { incidentLabel, initials, relative, title } from '../lib/format'
import { useAuth } from '../state/AuthContext'
import { useEnvironment } from '../state/EnvironmentContext'
import type { EnvironmentFilter } from '../state/EnvironmentContext'
import { useLive } from '../state/LiveContext'

const pageNames: Record<string, string> = {
  '/': 'Overview', '/applications': 'Applications', '/deployments': 'Deployments', '/incidents': 'Incidents',
  '/agents': 'AI Agents', '/metrics': 'Metrics', '/logs': 'Logs', '/infrastructure': 'Infrastructure',
  '/policies': 'Policies', '/settings': 'Settings', '/dora': 'DORA Metrics', '/flags': 'Feature flags',
}
const environments: Array<{ value: EnvironmentFilter; label: string }> = [
  { value: 'production', label: 'Production' }, { value: 'staging', label: 'Staging' }, { value: 'development', label: 'Development' }, { value: 'all', label: 'All environments' },
]

interface SearchHit { label: string; detail: string; to: string }

export function Topbar({ onMenuClick }: { onMenuClick: () => void }) {
  const { environment, setEnvironment } = useEnvironment()
  const { user, logout } = useAuth()
  const { activeIncidents } = useLive()
  const navigate = useNavigate()
  const [envOpen, setEnvOpen] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const [notificationsOpen, setNotificationsOpen] = useState(false)
  const [userOpen, setUserOpen] = useState(false)
  const [term, setTerm] = useState('')
  const [index, setIndex] = useState<SearchHit[]>([])
  const { pathname: path } = useLocation()
  const pageName = path.startsWith('/applications/') ? 'Application detail'
    : path.startsWith('/deployments/') ? 'Deployment detail'
      : path.startsWith('/incidents/') ? 'Incident investigation'
        : pageNames[path] ?? 'Overview'
  const current = environments.find((item) => item.value === environment) ?? environments[0]

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setSearchOpen(true) }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  useEffect(() => {
    if (!searchOpen) return
    Promise.all([
      api<Application[]>('/api/applications'), api<Incident[]>('/api/incidents?limit=100'), api<Deployment[]>('/api/deployments?limit=100'),
    ]).then(([apps, incidents, deployments]) => setIndex([
      ...apps.map((app) => ({ label: app.name, detail: `Application · ${app.id} · ${app.current_version ?? 'no version'}`, to: `/applications/${app.id}` })),
      ...incidents.map((incident) => ({ label: `${incident.id} · ${incident.title}`, detail: `Incident · ${incidentLabel(incident.status)} · ${relative(incident.detected_at)}`, to: `/incidents/${incident.id}` })),
      ...deployments.map((deployment) => ({ label: `${deployment.id} · ${deployment.application_name} ${deployment.version}`, detail: `Deployment · ${deployment.status} · ${relative(deployment.started_at)}`, to: `/deployments/${deployment.id}` })),
    ])).catch(() => setIndex([]))
  }, [searchOpen])

  const hits = useMemo(() => {
    const needle = term.trim().toLowerCase()
    if (!needle) return index.slice(0, 8)
    return index.filter((hit) => `${hit.label} ${hit.detail}`.toLowerCase().includes(needle)).slice(0, 10)
  }, [term, index])

  function go(to: string) { setSearchOpen(false); setTerm(''); navigate(to) }

  return (
    <header className="topbar">
      <div className="topbar-left"><button className="icon-button mobile-menu" aria-label="Open navigation" onClick={onMenuClick}><Menu size={19} /></button><div className="breadcrumb"><span>Workspace</span><span className="breadcrumb-slash">/</span><strong>{pageName}</strong></div></div>
      <div className="topbar-right">
        <div className="env-wrap">
          <button className="env-selector" onClick={() => setEnvOpen(!envOpen)} aria-expanded={envOpen}><span className={`env-indicator ${environment === 'all' ? 'staging' : environment}`} />{current.label}<ChevronDown size={14} /></button>
          {envOpen && <div className="popover env-popover"><span className="popover-label">SELECT ENVIRONMENT</span>{environments.map((item) => <button key={item.value} className={`popover-option${environment === item.value ? ' option-selected' : ''}`} onClick={() => { setEnvironment(item.value); setEnvOpen(false) }}><span className={`env-indicator ${item.value === 'all' ? 'staging' : item.value}`} />{item.label}{environment === item.value && <span className="option-check">✓</span>}</button>)}</div>}
        </div>
        <span className="topbar-divider" />
        <button className={`search-trigger${searchOpen ? ' search-open' : ''}`} onClick={() => setSearchOpen(!searchOpen)} aria-label="Search"><Search size={16} /><span>Search anything...</span><kbd><Command size={10} /> K</kbd></button>
        <div className="notification-wrap">
          <button className="icon-button notification-button" aria-label="Notifications" onClick={() => setNotificationsOpen(!notificationsOpen)}><Bell size={17} />{activeIncidents.length > 0 && <i />}</button>
          {notificationsOpen && <div className="popover notification-popover">
            <div className="notification-head"><span>Active incidents</span><span className="unread-label">{activeIncidents.length} OPEN</span></div>
            {activeIncidents.length === 0 && <div className="notification-item"><span><strong>All clear</strong><small>No active incidents in this environment</small></span></div>}
            {activeIncidents.slice(0, 5).map((incident) => <Link key={incident.id} className="notification-item" to={`/incidents/${incident.id}`} onClick={() => setNotificationsOpen(false)}><span className="notification-icon"><SirenIcon size={15} /></span><span><strong>{incident.title}</strong><small>{incident.id} · {incidentLabel(incident.status)} · {relative(incident.detected_at)}</small></span></Link>)}
            <button className="view-all-notifications" onClick={() => { setNotificationsOpen(false); navigate('/incidents') }}>View all incidents <span>→</span></button>
          </div>}
        </div>
        <span className="topbar-divider user-divider" />
        <div className="notification-wrap">
          <button className="user-menu" aria-label="User menu" onClick={() => setUserOpen(!userOpen)}><span className="user-avatar">{initials(user?.display_name)}</span><span className="user-name">{user?.display_name}</span><ChevronDown size={13} /></button>
          {userOpen && <div className="popover user-popover"><span className="popover-label">{user?.email}</span><div className="popover-option">Role · {title(user?.role)}</div><button className="popover-option" onClick={() => { setUserOpen(false); logout() }}><LogOut size={13} /> Sign out</button></div>}
        </div>
        {searchOpen && <div className="search-overlay" onClick={(event) => { if (event.target === event.currentTarget) setSearchOpen(false) }}>
          <div className="search-modal"><Search size={18} /><input autoFocus value={term} onChange={(event) => setTerm(event.target.value)} placeholder="Search services, incidents, deployments..." onKeyDown={(event) => { if (event.key === 'Escape') setSearchOpen(false); if (event.key === 'Enter' && hits[0]) go(hits[0].to) }} /><kbd>ESC</kbd><button className="icon-button" onClick={() => setSearchOpen(false)} aria-label="Close search">×</button></div>
          <div className="search-results">{hits.length ? hits.map((hit) => <button key={hit.to} className="search-result" onClick={() => go(hit.to)}><strong>{hit.label}</strong><small>{hit.detail}</small></button>) : <div className="search-hint">No matches</div>}</div>
        </div>}
      </div>
    </header>
  )
}
