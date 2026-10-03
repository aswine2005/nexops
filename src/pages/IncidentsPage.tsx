import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { ArrowRight, Clock3, Search, ShieldAlert, Sparkles } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api, query } from '../api/client'
import type { Incident } from '../api/types'
import { ErrorNote, Loading } from '../components/Feedback'
import { PageHeading } from '../components/PageHeading'
import { StatusBadge } from '../components/StatusBadge'
import { useResource } from '../hooks/useResource'
import { duration, incidentLabel, incidentTone, minutes, relative, severityTone, title } from '../lib/format'
import { useEnvironment } from '../state/EnvironmentContext'

export function IncidentsPage() {
  const { apiEnvironment } = useEnvironment()
  const { data, error, loading } = useResource(() => api<Incident[]>(`/api/incidents${query({ environment: apiEnvironment, limit: 300 })}`), [apiEnvironment], 10000)
  const [term, setTerm] = useState('')
  const [status, setStatus] = useState('all')
  const [severity, setSeverity] = useState('all')
  const incidents = data ?? []
  const visible = useMemo(() => incidents.filter((incident) => {
    const matches = `${incident.id} ${incident.title} ${incident.application_name} ${incident.summary} ${incident.root_cause ?? ''}`.toLowerCase().includes(term.toLowerCase())
    const statusMatch = status === 'all' || (status === 'active' ? incident.status !== 'resolved' : incident.status === status)
    return matches && statusMatch && (severity === 'all' || incident.severity === severity)
  }), [incidents, term, status, severity])
  const active = incidents.filter((incident) => incident.status !== 'resolved')
  const resolved = incidents.filter((incident) => incident.duration_seconds !== null)
  const sortedDurations = resolved.map((incident) => incident.duration_seconds as number).sort((a, b) => a - b)
  const median = sortedDurations.length ? sortedDurations[Math.floor(sortedDurations.length / 2)] / 60 : null
  const correlated = incidents.filter((incident) => incident.deployment_correlated).length

  return (
    <div className="work-page">
      <PageHeading eyebrow="PRODUCTION RELIABILITY" title="Incidents" description="Incidents opened by deterministic detection, with their evidence, likely root cause and verified recovery." action={<span className="snapshot-tag"><i /> Live</span>} />
      <div className="summary-strip three-summary">
        <IncidentStat icon={<ShieldAlert size={16} />} label="Active incidents" value={String(active.length).padStart(2, '0')} note={active.length ? `${active.filter((incident) => incident.severity === 'critical' || incident.severity === 'high').length} high or critical` : 'No open incidents'} tone={active.length ? 'red' : 'green'} />
        <IncidentStat icon={<Clock3 size={16} />} label="Median time to resolve" value={minutes(median)} note={`${resolved.length} resolved incident(s)`} tone="blue" />
        <IncidentStat icon={<Sparkles size={16} />} label="Deployment-related" value={String(correlated).padStart(2, '0')} note={incidents.length ? `${Math.round((correlated / incidents.length) * 100)}% correlated with a release` : 'No incidents yet'} tone="violet" />
      </div>
      {error && <ErrorNote message={error} />}
      <section className="panel catalog-panel">
        <div className="data-toolbar"><div className="toolbar-title"><div><h2>Incident history</h2><p>{visible.length} of {incidents.length} incidents</p></div></div><div className="toolbar-controls"><label className="search-field"><Search size={15} /><input value={term} onChange={(event) => setTerm(event.target.value)} placeholder="Search incidents" /></label><select className="filter-select" value={status} onChange={(event) => setStatus(event.target.value)} aria-label="Filter by status"><option value="all">All incidents</option><option value="active">Active</option><option value="investigating">Investigating</option><option value="awaiting_approval">Awaiting approval</option><option value="verifying">Verifying recovery</option><option value="resolved">Resolved</option></select><select className="filter-select" value={severity} onChange={(event) => setSeverity(event.target.value)} aria-label="Filter severity"><option value="all">All severities</option><option value="critical">Critical</option><option value="high">High</option><option value="medium">Medium</option></select></div></div>
        {loading && !data ? <Loading /> : <div className="wide-table-wrap"><table className="data-table incidents-table"><thead><tr><th>INCIDENT</th><th>SERVICE</th><th>SEVERITY</th><th>STATUS</th><th>DEPLOYMENT</th><th>DETECTED</th><th>DURATION</th><th /></tr></thead><tbody>
          {visible.map((incident) => <tr key={incident.id}><td><Link className="incident-table-link" to={`/incidents/${incident.id}`}><span className={`incident-list-icon ${incident.status === 'resolved' ? 'resolved' : 'active'}`}><ShieldAlert size={15} /></span><span><strong>{incident.id} · {incident.title}</strong><small>{incident.root_cause ?? incident.summary}</small></span></Link></td><td>{incident.application_name}</td><td><StatusBadge tone={severityTone(incident.severity)} compact>{title(incident.severity)}</StatusBadge></td><td><StatusBadge tone={incidentTone(incident.status)} dot compact>{incidentLabel(incident.status)}</StatusBadge></td><td>{incident.deployment ? <Link className="inline-record-link" to={`/deployments/${incident.deployment.id}`}>{incident.deployment.id} · {incident.deployment.version}</Link> : <span className="cell-subtext">Not correlated</span>}</td><td>{relative(incident.detected_at)}</td><td>{duration(incident.duration_seconds)}</td><td><Link to={`/incidents/${incident.id}`} className="row-open-link" aria-label={`Open ${incident.id}`}><ArrowRight size={15} /></Link></td></tr>)}
          {visible.length === 0 && <tr><td colSpan={8}><div className="empty-state"><span>{incidents.length ? 'No incidents match those filters' : 'No incidents recorded'}</span><small>{incidents.length ? 'Try another search or filter.' : 'Incidents open automatically when production signals breach thresholds.'}</small></div></td></tr>}
        </tbody></table></div>}
        <div className="table-footer"><span>Detection is deterministic; AI investigates the evidence afterwards.</span><span>{visible.length} shown <i /> Live</span></div>
      </section>
    </div>
  )
}

function IncidentStat({ icon, label, value, note, tone }: { icon: ReactNode; label: string; value: string; note: string; tone: string }) {
  return <div className={`summary-stat summary-${tone}`}><span className="summary-icon">{icon}</span><div className="summary-copy"><span>{label}</span><strong>{value}</strong><small>{note}</small></div></div>
}
