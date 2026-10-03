import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Activity, ArrowRight, Plus, Search, Server, ShieldCheck } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api, query } from '../api/client'
import type { Application } from '../api/types'
import { EmptyState, ErrorNote, Loading } from '../components/Feedback'
import { PageHeading } from '../components/PageHeading'
import { Sparkline } from '../components/Sparkline'
import { StatusBadge } from '../components/StatusBadge'
import { useResource } from '../hooks/useResource'
import { appTone, ms, pct, relative, title } from '../lib/format'
import { useAuth } from '../state/AuthContext'
import { useEnvironment } from '../state/EnvironmentContext'

type HealthFilter = 'all' | 'healthy' | 'degraded' | 'critical' | 'unknown'

export function ApplicationsPage() {
  const { apiEnvironment } = useEnvironment()
  const { can } = useAuth()
  const { data, error, loading } = useResource(() => api<Application[]>(`/api/applications${query({ environment: apiEnvironment })}`), [apiEnvironment], 20000)
  const [term, setTerm] = useState('')
  const [health, setHealth] = useState<HealthFilter>('all')
  const [sort, setSort] = useState('name')
  const apps = data ?? []
  const filtered = useMemo(() => apps.filter((app) => {
    const matches = `${app.name} ${app.id} ${app.current_version ?? ''} ${app.owner ?? ''}`.toLowerCase().includes(term.toLowerCase())
    return matches && (health === 'all' || app.status === health)
  }).sort((a, b) => sort === 'error' ? (b.latest?.error_rate_percent ?? -1) - (a.latest?.error_rate_percent ?? -1) : a.name.localeCompare(b.name)), [apps, term, health, sort])
  const attention = apps.filter((app) => app.status === 'degraded' || app.status === 'critical')

  return (
    <div className="work-page">
      <PageHeading eyebrow="SERVICE CATALOG" title="Applications" description="Monitored services, their telemetry sources, and current production health." action={can('operator') ? <Link className="button button-primary" to="/applications/new"><Plus size={14} /> Register application</Link> : undefined} />
      <div className="summary-strip three-summary">
        <SummaryStat icon={<Server size={16} />} label="Monitored services" value={String(apps.length).padStart(2, '0')} note={`${new Set(apps.map((app) => app.environment)).size || 0} environment(s)`} tone="blue" />
        <SummaryStat icon={<ShieldCheck size={16} />} label="Healthy" value={String(apps.filter((app) => app.status === 'healthy').length).padStart(2, '0')} note="Within detection thresholds" tone="green" />
        <SummaryStat icon={<Activity size={16} />} label="Needs attention" value={String(attention.length).padStart(2, '0')} note={attention.length ? attention.map((app) => app.name).join(', ') : 'No services affected'} tone={attention.length ? 'red' : 'blue'} />
      </div>
      {error && <ErrorNote message={error} />}
      <section className="panel catalog-panel">
        <div className="data-toolbar">
          <div className="toolbar-title"><div><h2>Services</h2><p>{filtered.length} of {apps.length} services</p></div></div>
          <div className="toolbar-controls">
            <label className="search-field"><Search size={15} /><input value={term} onChange={(event) => setTerm(event.target.value)} placeholder="Search services" /></label>
            <select className="filter-select" value={health} onChange={(event) => setHealth(event.target.value as HealthFilter)} aria-label="Filter by health"><option value="all">All health states</option><option value="healthy">Healthy</option><option value="degraded">Degraded</option><option value="critical">Critical</option><option value="unknown">No data</option></select>
            <select className="filter-select sort-select" value={sort} onChange={(event) => setSort(event.target.value)} aria-label="Sort services"><option value="name">Name</option><option value="error">Error rate</option></select>
          </div>
        </div>
        {loading && !data ? <Loading /> : <div className="wide-table-wrap"><table className="data-table application-table"><thead><tr><th>APPLICATION</th><th>HEALTH</th><th>VERSION</th><th>ERROR RATE</th><th>P99 LATENCY</th><th>CPU / MEMORY</th><th>REPLICAS</th><th>LAST DEPLOYED</th><th>TREND</th><th /></tr></thead><tbody>
          {filtered.map((app) => {
            const tone = appTone(app.status)
            const bad = tone === 'critical' || tone === 'warning'
            return <tr key={app.id}>
              <td><Link className="table-primary-link" to={`/applications/${app.id}`}><span className={`table-service-icon ${tone}`}>{app.name[0]}</span><span><strong>{app.name}</strong><small>{app.id} · {title(app.environment)}</small></span></Link></td>
              <td><StatusBadge tone={tone} dot compact>{app.status === 'unknown' ? 'No data' : title(app.status)}</StatusBadge></td>
              <td><code>{app.current_version ?? '—'}</code></td>
              <td className={bad ? 'critical-text' : ''}>{pct(app.latest?.error_rate_percent)}</td>
              <td className={bad ? 'critical-text' : ''}>{ms(app.latest?.p99_latency_ms)}</td>
              <td>{pct(app.latest?.cpu_percent, 0)} / {pct(app.latest?.memory_percent, 0)}</td>
              <td>{app.latest?.total_replicas ? `${app.latest.healthy_replicas}/${app.latest.total_replicas}` : '—'}</td>
              <td>{relative(app.last_deployment?.finished_at ?? app.last_deployment?.started_at)}</td>
              <td><Sparkline values={app.sparkline} tone={bad ? 'critical' : app.sparkline.length ? 'healthy' : 'neutral'} /></td>
              <td><Link to={`/applications/${app.id}`} className="row-open-link" aria-label={`Open ${app.name}`}><ArrowRight size={15} /></Link></td>
            </tr>
          })}
          {filtered.length === 0 && <tr><td colSpan={10}><EmptyState title={apps.length ? 'No matching services' : 'No applications registered'} detail={apps.length ? 'Try another search term or health filter.' : 'Register a service with its Prometheus, log, and runtime configuration.'} /></td></tr>}
        </tbody></table></div>}
        <div className="table-footer"><span>Health is evaluated from the latest telemetry sample against each service's thresholds.</span><span>{apps.length} services <i /> Live</span></div>
      </section>
    </div>
  )
}

function SummaryStat({ icon, label, value, note, tone }: { icon: ReactNode; label: string; value: string; note: string; tone: string }) {
  return <div className={`summary-stat summary-${tone}`}><span className="summary-icon">{icon}</span><div className="summary-copy"><span>{label}</span><strong>{value}</strong><small>{note}</small></div></div>
}
