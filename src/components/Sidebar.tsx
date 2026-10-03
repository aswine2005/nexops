import type { LucideIcon } from 'lucide-react'
import {
  Activity, BarChart3, Bot, Boxes, CircleHelp, Flag, LayoutDashboard, Logs, Rocket,
  Server, Settings2, ShieldCheck, Siren, X,
} from 'lucide-react'
import { NavLink } from 'react-router-dom'
import { initials, title } from '../lib/format'
import { useAuth } from '../state/AuthContext'
import { useLive } from '../state/LiveContext'

const primaryLinks = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/applications', label: 'Applications', icon: Boxes },
  { to: '/deployments', label: 'Deployments', icon: Rocket },
  { to: '/incidents', label: 'Incidents', icon: Siren },
  { to: '/agents', label: 'AI Agents', icon: Bot },
]

const operationsLinks = [
  { to: '/metrics', label: 'Metrics', icon: Activity },
  { to: '/logs', label: 'Logs', icon: Logs },
  { to: '/infrastructure', label: 'Infrastructure', icon: Server },
]

const deliveryLinks = [
  { to: '/flags', label: 'Feature Flags', icon: Flag },
  { to: '/dora', label: 'DORA Metrics', icon: BarChart3 },
]

const managementLinks = [
  { to: '/policies', label: 'Policies', icon: ShieldCheck },
  { to: '/settings', label: 'Settings', icon: Settings2 },
]

interface SidebarProps { mobileOpen: boolean; onNavigate: () => void }

export function Sidebar({ mobileOpen, onNavigate }: SidebarProps) {
  const { user } = useAuth()
  const { activeIncidents, settings } = useLive()
  const count = activeIncidents.length
  const links = primaryLinks.map((link) => link.to === '/incidents' ? { ...link, badge: count ? String(count) : undefined } : link)
  const llm = settings?.llm_provider === 'groq'
  return (
    <aside className={`sidebar${mobileOpen ? ' sidebar-open' : ''}`}>
      <div className="brand-row">
        <div className="brand-mark" aria-hidden="true"><span /><span /><span /><span /></div>
        <div className="brand-copy"><span className="brand-name">nex<span>ops</span></span><span className="brand-tagline">RELIABILITY PLATFORM</span></div>
        <button className="icon-button sidebar-close" aria-label="Close menu" onClick={onNavigate}><X size={17} /></button>
      </div>

      <div className="workspace-switch workspace-static">
        <span className="workspace-avatar">{initials(user?.display_name)}</span>
        <span className="workspace-name"><strong>{user?.display_name ?? 'NexOps'}</strong><small>{title(user?.role)} · {user?.email}</small></span>
      </div>

      <nav className="side-nav" aria-label="Primary navigation">
        <NavGroup label="WORKSPACE" links={links} onNavigate={onNavigate} />
        <NavGroup label="OBSERVABILITY" links={operationsLinks} onNavigate={onNavigate} />
        <NavGroup label="DELIVERY" links={deliveryLinks} onNavigate={onNavigate} />
        <NavGroup label="GOVERNANCE" links={managementLinks} onNavigate={onNavigate} />
      </nav>

      <div className="sidebar-bottom">
        <div className="sidebar-ai-card">
          <div className="ai-card-top"><span className="ai-orb"><Bot size={15} /></span><span className="ai-card-title">NexOps AI</span><span className={llm ? 'live-dot' : 'demo-dot'} /></div>
          <p>{llm ? <>Groq investigation<br />{settings?.llm_model}</> : <>Deterministic evidence rules.<br />Add GROQ_API_KEY for LLM analysis.</>}</p>
          <span className="ai-mode-label">{llm ? 'LLM ENABLED' : 'RULES MODE'} <span>·</span> v{settings?.version ?? '1.0.0'}</span>
        </div>
        <a className="help-link" href="/docs" target="_blank" rel="noreferrer"><CircleHelp size={16} /> API documentation <span>↗</span></a>
        <div className="sidebar-footer"><span className="footer-status"><i className={count ? 'footer-status-alert' : ''} /> {count ? `${count} active incident${count > 1 ? 's' : ''}` : 'No active incidents'}</span><span>v{settings?.version ?? '1.0.0'}</span></div>
      </div>
    </aside>
  )
}

interface NavLinkData { to: string; label: string; icon: LucideIcon; end?: boolean; badge?: string }
function NavGroup({ label, links, onNavigate }: { label: string; links: NavLinkData[]; onNavigate: () => void }) {
  return (
    <div className="nav-group">
      <span className="nav-group-label">{label}</span>
      {links.map(({ to, label: linkLabel, icon: Icon, end, badge }) => (
        <NavLink key={to} to={to} end={end} onClick={onNavigate} className={({ isActive }) => `nav-link${isActive ? ' nav-link-active' : ''}`}>
          <Icon size={17} strokeWidth={1.8} /><span>{linkLabel}</span>{badge && <span className="nav-count">{badge}</span>}
        </NavLink>
      ))}
    </div>
  )
}
