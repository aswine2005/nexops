import { Activity, ArrowRight, Check, ChevronRight, CircleAlert, CircleCheck, Clock3, GitCommitHorizontal, HeartPulse, Plus, Rocket, ShieldAlert, Sparkles, Terminal } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api, query } from '../api/client'
import type { Dashboard, Deployment, Incident } from '../api/types'
import { ErrorRateChart } from '../components/ErrorRateChart'
import { ErrorNote, Loading } from '../components/Feedback'
import { MetricCard } from '../components/MetricCard'
import { Sparkline } from '../components/Sparkline'
import { StatusBadge } from '../components/StatusBadge'
import { useResource } from '../hooks/useResource'
import {
  appTone, clock, deploymentLabel, deploymentTone, greeting, incidentLabel, initials, ms, pct, rate, relative, shortSha, title,
} from '../lib/format'
import type { HealthTone } from '../lib/format'
import { useAuth } from '../state/AuthContext'
import { useEnvironment } from '../state/EnvironmentContext'

function change(current: number | null | undefined, previous: number | null | undefined, format: (value: number) => string): { text: string; direction: 'up' | 'down' | 'flat' } {
  if (current === null || current === undefined || previous === null || previous === undefined) return { text: 'No prior hour', direction: 'flat' }
  const diff = current - previous
  if (Math.abs(diff) < Math.max(Math.abs(previous) * 0.02, 0.001)) return { text: 'Stable', direction: 'flat' }
  return { text: `${diff > 0 ? '+' : '−'}${format(Math.abs(diff))}`, direction: diff > 0 ? 'up' : 'down' }
}

export function DashboardPage() {
  const { user } = useAuth()
  const { apiEnvironment, environment } = useEnvironment()
  const { data, error, loading, updatedAt } = useResource(() => api<Dashboard>(`/api/dashboard${query({ environment: apiEnvironment })}`), [apiEnvironment], 15000)

  if (loading && !data) return <div className="dashboard-page"><Loading label="Loading production health…" /></div>
  if (!data) return <div className="dashboard-page"><ErrorNote message={error ?? 'Dashboard unavailable'} /></div>

  const signals = data.golden_signals
  const previous = data.previous_hour
  const incidents = data.active_incidents
  const hasApps = data.summary.applications > 0
  const healthy = data.status === 'healthy'
  const focus = data.focus
  const latestDeployment = data.recent_deployments[0]
  const errorChange = change(signals.error_rate_percent, previous.error_rate_percent, (value) => pct(value))
  const latencyChange = change(signals.p99_latency_ms, previous.p99_latency_ms, (value) => ms(value))
  const trafficChange = change(signals.request_rate, previous.request_rate, (value) => rate(value))
  const cpuChange = change(signals.cpu_percent, previous.cpu_percent, (value) => pct(value, 0))
  const memoryChange = change(signals.memory_percent, previous.memory_percent, (value) => pct(value, 0))
  const errorTone: HealthTone = signals.error_rate_percent === null || signals.error_rate_percent === undefined ? 'neutral' : signals.error_rate_percent >= 5 ? 'critical' : signals.error_rate_percent >= 1 ? 'warning' : 'healthy'
  const latencyTone: HealthTone = signals.p99_latency_ms === null || signals.p99_latency_ms === undefined ? 'neutral' : signals.p99_latency_ms >= 1000 ? 'critical' : signals.p99_latency_ms >= 500 ? 'warning' : 'healthy'
  const cpuTone: HealthTone = (signals.cpu_percent ?? 0) >= 85 ? 'warning' : 'neutral'
  const environmentLabel = environment === 'all' ? 'ALL ENVIRONMENTS' : `${environment.toUpperCase()}`
  const headline = !hasApps ? 'No applications registered' : incidents.length ? `${incidents.length} active incident${incidents.length > 1 ? 's' : ''}` : healthy ? 'All monitored services are healthy' : `${data.summary.degraded} service${data.summary.degraded === 1 ? '' : 's'} need attention`
  const bannerDetail = !hasApps ? 'Register a service with its telemetry sources to start monitoring.'
    : incidents[0] ? `${incidents[0].application_name}: ${incidents[0].title} · ${incidentLabel(incidents[0].status)}`
      : data.summary.unknown ? `${data.summary.unknown} service(s) have no telemetry yet.` : `${data.summary.healthy} of ${data.summary.applications} services within thresholds.`

  return (
    <div className="dashboard-page">
      <div className="page-intro">
        <div><div className="page-overline"><span className="overline-pulse" />{environmentLabel} OVERVIEW <span className="overline-sep">/</span> {new Date().toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: '2-digit' }).toUpperCase()}</div><h1>{greeting()}, {user?.display_name.split(' ')[0]} <span className="wave">✳</span></h1><p>Live production health across your monitored services.</p></div>
        <div className="intro-actions"><span className="last-updated"><i />{data.last_sample_at ? `Telemetry ${relative(data.last_sample_at)}` : 'Waiting for telemetry'}{updatedAt ? ` · refreshed ${clock(new Date(updatedAt).toISOString(), true)}` : ''}</span>{incidents[0] ? <Link className="button button-secondary" to={`/incidents/${incidents[0].id}`}><Activity size={14} /> Open incident</Link> : !hasApps ? <Link className="button button-primary" to="/applications/new"><Plus size={14} /> Register application</Link> : null}</div>
      </div>
      {error && <ErrorNote message={`Live refresh failed: ${error}`} />}

      <section className={`environment-banner${healthy || !hasApps ? ' environment-banner-healthy' : incidents.some((item) => item.status === 'verifying') ? ' environment-banner-verifying' : ''}`}>
        <div className="environment-status"><span className="health-emblem"><HeartPulse size={20} /></span><div><span className="banner-label">{environmentLabel} HEALTH</span><strong>{hasApps ? title(data.status) : 'Not monitored'}</strong></div></div>
        <span className="banner-divider" />
        <p><strong>{headline}</strong><span>{bannerDetail}</span></p>
        {incidents[0] ? <Link to={`/incidents/${incidents[0].id}`} className="banner-link">View incident <ArrowRight size={15} /></Link> : <Link to={hasApps ? '/applications' : '/applications/new'} className="banner-link">{hasApps ? 'View services' : 'Register application'} <ArrowRight size={15} /></Link>}
        <div className="banner-decoration" aria-hidden="true">{Array.from({ length: 20 }, (_, index) => <i key={index} />)}</div>
      </section>

      <div className="metric-grid">
        <MetricCard label="Error rate" value={pct(signals.error_rate_percent)} delta={errorChange.text} direction={errorChange.direction} context="HTTP 5xx · traffic-weighted" icon="error" tone={errorTone} />
        <MetricCard label="P99 latency" value={ms(signals.p99_latency_ms)} delta={latencyChange.text} direction={latencyChange.direction} context="Slowest service · vs prior hour" icon="latency" tone={latencyTone} />
        <MetricCard label="Request rate" value={rate(signals.request_rate)} unit="req/s" delta={trafficChange.text} direction={trafficChange.direction} context="All services · current" icon="traffic" tone="neutral" />
        <MetricCard label="CPU usage" value={pct(signals.cpu_percent, 0)} delta={cpuChange.text} direction={cpuChange.direction} context="Average across services" icon="cpu" tone={cpuTone} />
        <MetricCard label="Memory" value={pct(signals.memory_percent, 0)} delta={memoryChange.text} direction={memoryChange.direction} context={signals.total_replicas ? `${signals.healthy_replicas}/${signals.total_replicas} replicas ready` : 'Runtime saturation'} icon="memory" tone="neutral" />
        <MetricCard label="Active incidents" value={String(incidents.length).padStart(2, '0')} delta={incidents.length ? title(incidents[0].severity) : 'All clear'} context={incidents.length ? `${new Set(incidents.map((item) => item.application_id)).size} service(s) affected` : `${data.summary.deployments_today} deployment(s) today`} icon="incident" tone={incidents.length ? 'critical' : 'healthy'} />
      </div>

      <div className="dashboard-grid dashboard-grid-top">
        <section className="panel chart-panel">
          <div className="panel-header chart-header"><div><div className="panel-title-row"><h2>Error rate</h2><span className="live-label">LIVE</span></div><p>HTTP 5xx · {focus.application_name ?? 'no service'} · last 45 minutes</p></div><Link to={focus.application_id ? `/metrics?application=${focus.application_id}` : '/metrics'} className="select-button">View metrics <ChevronRight size={13} /></Link></div>
          <div className="chart-summary"><div><strong>{pct(focus.series.at(-1)?.error_rate_percent)}</strong><span className={incidents.length ? 'chart-rise' : 'chart-recovered'}>{incidents.length ? <CircleAlert size={13} /> : <Check size={13} />} {incidents.length ? 'incident active' : 'within threshold'}</span></div>{focus.baseline_error_rate !== null && <span className="chart-baseline"><i />Pre-release baseline <b>{pct(focus.baseline_error_rate)}</b></span>}</div>
          {focus.series.length ? <ErrorRateChart data={focus.series.map((point) => ({ t: point.t, value: point.error_rate_percent }))} markers={focus.deployment_markers.map((marker) => ({ t: marker.t, label: `${marker.kind === 'rollback' ? 'ROLLBACK' : 'DEPLOY'} ${marker.version}` }))} baseline={focus.baseline_error_rate} healthy={!incidents.length} /> : <div className="chart-wrap chart-empty">No samples yet. The monitor stores one sample per service every cycle.</div>}
          <div className="chart-footer"><span><i className={incidents.length ? 'legend-error' : 'legend-baseline'} />Error rate</span>{focus.deployment_markers.length > 0 && <span className="chart-event"><i />{focus.deployment_markers.length} release event(s)</span>}<span className="chart-refresh">{focus.series.length} samples</span></div>
        </section>

        <LatestDeployment deployment={latestDeployment} />
      </div>

      <div className="dashboard-grid dashboard-grid-lower">
        <section className="panel services-panel">
          <div className="panel-header"><div><h2>Service health</h2><p>{data.summary.applications} monitored applications <span className="panel-dot-sep">·</span> <b className="healthy-text">{data.summary.healthy} healthy</b> <span className="panel-dot-sep">·</span> <b className={data.summary.degraded ? 'critical-text' : 'healthy-text'}>{data.summary.degraded} degraded</b></p></div><Link to="/applications" className="text-link">All services <ArrowRight size={14} /></Link></div>
          <div className="service-table-wrap"><table className="service-table"><thead><tr><th>APPLICATION</th><th>HEALTH</th><th>ERROR RATE</th><th>P99 LATENCY</th><th>REPLICAS</th><th>TREND</th><th /></tr></thead><tbody>
            {data.applications.map((app) => { const tone = appTone(app.status); const bad = tone === 'critical' || tone === 'warning'; return <tr key={app.id}><td><span className={`service-monogram ${tone}`}>{app.name.slice(0, 1)}</span><span className="service-name-cell"><strong>{app.name}</strong><small>{app.current_version ?? 'no release yet'}</small></span></td><td><StatusBadge tone={tone} dot compact>{title(app.status)}</StatusBadge></td><td className={bad ? 'critical-text' : ''}>{pct(app.latest?.error_rate_percent)}</td><td className={bad ? 'critical-text' : ''}>{ms(app.latest?.p99_latency_ms)}</td><td>{app.latest?.total_replicas ? `${app.latest.healthy_replicas}/${app.latest.total_replicas}` : '—'}</td><td><Sparkline values={app.sparkline} tone={bad ? 'critical' : app.sparkline.length ? 'healthy' : 'neutral'} /></td><td><Link to={`/applications/${app.id}`} aria-label={`Open ${app.name}`}><ChevronRight size={15} className="row-chevron" /></Link></td></tr> })}
            {!hasApps && <tr><td colSpan={7}><div className="empty-state"><span>No applications yet</span><small><Link className="text-link" to="/applications/new">Register your first service</Link></small></div></td></tr>}
          </tbody></table></div>
          <div className="services-foot"><span><i className="source-indicator" />Prometheus · runtime · health probes</span><span>{data.last_sample_at ? `Last sample ${relative(data.last_sample_at)}` : 'No samples yet'}</span></div>
        </section>
        <IncidentPanel incidents={incidents} summary={data.summary} />
      </div>

      <div className="dashboard-grid dashboard-grid-bottom">
        <section className="panel deployment-history-panel">
          <div className="panel-header"><div><h2>Recent deployments</h2><p>CI/CD result vs. verified production outcome</p></div><Link to="/deployments" className="text-link">View all <ArrowRight size={14} /></Link></div>
          <div className="deployment-list">{data.recent_deployments.slice(0, 5).map((deployment) => { const tone = deploymentTone(deployment.status); return <Link to={`/deployments/${deployment.id}`} className="deployment-row" key={deployment.id}><span className={`deployment-state ${tone}`}>{tone === 'critical' ? <ShieldAlert size={15} /> : <CircleCheck size={15} />}</span><div className="deployment-app"><strong>{deployment.application_name}</strong><span>{deployment.version} <i /> <code>{shortSha(deployment.commit_sha)}</code></span></div><span className="deployment-author"><span className="author-dot">{initials(deployment.author)}</span>{deployment.author ?? 'unknown'}</span><StatusBadge tone={tone} compact>{deploymentLabel(deployment.status)}</StatusBadge><span className={`risk-chip risk-${deployment.risk_level ?? 'low'}`}>{deployment.kind === 'rollback' ? 'Rollback' : `${title(deployment.risk_level ?? 'n/a')} risk`}</span><span className="deployment-time">{relative(deployment.finished_at ?? deployment.started_at)}</span></Link> })}
            {!data.recent_deployments.length && <div className="empty-state"><span>No deployments reported</span><small>Add the NexOps step to your GitHub Actions workflow (Settings → CI/CD webhook).</small></div>}
          </div>
        </section>
        <section className="panel pod-panel">
          <div className="panel-header"><div><h2>{focus.application_name ?? 'Workload'} replicas</h2><p>{focus.runtime ? `${title(focus.runtime.runtime)} · ${focus.runtime.target}` : 'No runtime data'}</p></div><span className="pod-count">{focus.runtime ? `${focus.runtime.healthy_replicas}/${focus.runtime.total_replicas} ready` : '—'}</span></div>
          <div className="pod-list">{(focus.runtime?.replicas ?? []).map((replica) => { const tone = replica.ready ? 'healthy' : 'critical'; return <div className="pod-row" key={replica.name}><span className={`pod-status-icon ${tone}`}><i /></span><div className="pod-info"><strong>{replica.name}</strong><span>{replica.version ?? replica.image} <i /> {replica.started_at ? relative(replica.started_at) : '—'}</span></div><span className={`pod-state ${tone}`}>{replica.reason ?? replica.status}</span>{replica.restarts > 0 && <span className="restart-count">{replica.restarts} restarts</span>}</div> })}
            {!focus.runtime?.replicas?.length && <div className="empty-state"><span>No replicas reported</span><small>Enable the Docker or Kubernetes integration for this service.</small></div>}
          </div>
          <div className="pod-footer"><span><Rocket size={13} /> {focus.runtime?.image ?? 'image unknown'}</span><Link to="/infrastructure">View infrastructure <ArrowRight size={13} /></Link></div>
        </section>
      </div>

      <div className="dashboard-grid log-strip-grid">
        <section className="panel log-preview-panel">
          <div className="log-preview-head"><div><span className="terminal-icon"><Terminal size={15} /></span><div><h2>Latest logs</h2><p>{focus.application_name ?? 'No service'} · {focus.log_source ?? 'no log source'}</p></div></div><Link to={focus.application_id ? `/logs?application=${focus.application_id}` : '/logs'} className="text-link">Open log explorer <ArrowRight size={14} /></Link></div>
          <div className="log-lines">{focus.logs.map((log, index) => <div className="log-line" key={`${log.timestamp}-${index}`}><span className="log-time">{clock(log.timestamp, true)}</span><span className={`log-level ${log.level.toLowerCase()}`}>{log.level}</span><code>{log.message}</code><span className="log-source">{log.source}</span></div>)}
            {!focus.logs.length && <div className="empty-state"><span>No recent log lines</span><small>Configure Loki, CloudWatch Logs, Docker or Kubernetes for this service.</small></div>}
          </div>
          <div className="log-footer"><span><i /> {focus.logs.length} most recent entries</span><span>{(focus.log_source ?? 'NONE').toUpperCase()}</span></div>
        </section>
      </div>
      <div className="dashboard-endnote"><span>NEXOPS RELIABILITY ENGINE</span><span>Deploy → Monitor → Detect → Investigate → Remediate → Verify → Learn</span><span>Live data <i /></span></div>
    </div>
  )
}

function LatestDeployment({ deployment }: { deployment: Deployment | undefined }) {
  if (!deployment) {
    return <section className="panel deploy-panel"><div className="panel-header"><div><h2>Latest deployment</h2><p>Waiting for the first CI/CD report</p></div></div><div className="empty-state"><span>No deployments yet</span><small>GitHub Actions reports releases to /api/webhooks/deployments.</small></div></section>
  }
  const tone = deploymentTone(deployment.status)
  const verify = deployment.status === 'healthy' ? 'success' : deployment.status === 'degraded' || deployment.status === 'rolled_back' ? 'failure' : deployment.status === 'verifying' ? 'in_progress' : 'pending'
  const stages = [...deployment.pipeline.filter((stage) => stage.name.toLowerCase() !== 'verify'), { name: 'Verify', status: verify }]
  const score = deployment.risk_score ?? 0
  return (
    <section className="panel deploy-panel">
      <div className="panel-header"><div><h2>Latest deployment</h2><p>{title(deployment.environment)} · {relative(deployment.finished_at ?? deployment.started_at)}</p></div><Link className="icon-button small-icon" aria-label="Deployment details" to={`/deployments/${deployment.id}`}><ArrowRight size={16} /></Link></div>
      <div className="deploy-identity"><div className="deploy-icon"><Rocket size={17} /></div><div><strong>{deployment.application_name}</strong><span>{deployment.previous_version ? `${deployment.previous_version} → ` : ''}{deployment.version} <i /> <code>{shortSha(deployment.commit_sha)}</code></span></div><StatusBadge tone={tone} dot compact>{deploymentLabel(deployment.status)}</StatusBadge></div>
      <div className="deployment-pipeline">{stages.map((stage) => { const failed = ['failure', 'failed', 'cancelled'].includes(stage.status); const running = ['in_progress', 'pending'].includes(stage.status); return <div className={`pipeline-step${failed ? ' pipeline-warning' : running ? ' pipeline-pending' : ''}`} key={stage.name}><span>{failed ? '!' : running ? <Clock3 size={11} /> : <Check size={12} strokeWidth={2.7} />}</span><small>{stage.name}</small></div> })}</div>
      <div className="deploy-risk"><div className="risk-heading"><span><ShieldAlert size={14} /> Deployment risk</span><StatusBadge tone={deployment.risk_level === 'high' ? 'critical' : deployment.risk_level === 'medium' ? 'warning' : 'healthy'} compact>{(deployment.risk_level ?? 'n/a').toUpperCase()}</StatusBadge></div><div className="risk-bar"><i style={{ width: `${score}%` }} /></div><div className="risk-foot"><span>{deployment.risk_factors[0] ?? 'Deployment Risk Agent estimate'}</span><span>{score} / 100</span></div></div>
      <div className="deploy-details"><span><GitCommitHorizontal size={14} /> <code>{shortSha(deployment.commit_sha)}</code></span><span><span className="author-dot">{initials(deployment.author)}</span> {deployment.author ?? 'unknown'}</span><span><Clock3 size={13} /> CI {deployment.ci_status}</span></div>
      <Link to={`/deployments/${deployment.id}`} className="panel-link">Deployment details <ArrowRight size={14} /></Link>
    </section>
  )
}

function IncidentPanel({ incidents, summary }: { incidents: Incident[]; summary: Dashboard['summary'] }) {
  const incident = incidents[0]
  if (!incident) {
    return <section className="panel demo-incident-panel demo-incident-healthy">
      <div className="demo-panel-icon"><Check size={16} /></div>
      <div className="demo-panel-copy"><span className="incident-number">PRODUCTION · MONITORED</span><h2>No active incidents</h2><p>Deterministic rules evaluate every telemetry sample. Incidents open automatically when a signal breaches its threshold on consecutive samples.</p>
        <div className="demo-panel-stats"><span>SERVICES <strong>{summary.applications}</strong></span><span>HEALTHY <strong>{summary.healthy}</strong></span><span>DEPLOYS TODAY <strong>{summary.deployments_today}</strong></span></div>
        <div className="demo-panel-actions"><Link className="incident-action" to="/incidents">Incident history <ArrowRight size={14} /></Link></div>
      </div>
      <span className="demo-panel-label">LIVE</span>
    </section>
  }
  return <section className="panel demo-incident-panel demo-incident-active">
    <div className="demo-panel-icon"><CircleAlert size={16} /></div>
    <div className="demo-panel-copy"><span className="incident-number">{incident.id} <i /> {incident.severity.toUpperCase()} SEVERITY</span><h2>{incident.title}</h2><p>{incident.summary}</p>
      <div className="demo-panel-stats"><span>ERROR RATE <strong className="critical-text">{pct(incident.observed.error_rate_percent as number | undefined)}</strong></span><span>P99 <strong>{ms(incident.observed.p99_latency_ms as number | undefined)}</strong></span><span>STATUS <strong className="warning-text">{incidentLabel(incident.status)}</strong></span></div>
      {incident.root_cause && <div className="demo-evidence"><Sparkles size={13} /><span><strong>{Math.round((incident.confidence ?? 0) * 100)}% confidence</strong> · {incident.root_cause}</span></div>}
      <div className="demo-panel-actions"><Link className="incident-action" to={`/incidents/${incident.id}`}>Open investigation <ArrowRight size={14} /></Link>{incidents.length > 1 && <Link className="demo-reset-link" to="/incidents">+{incidents.length - 1} more</Link>}</div>
    </div>
    <span className="demo-panel-label">{incidentLabel(incident.status).toUpperCase()}</span>
  </section>
}
