import { useState } from 'react'
import { ArrowLeft, ArrowRight, Box, Cable, CheckCircle2, CircleAlert, Clock3, GitBranch, HeartPulse, Pause, Pencil, Play, Radar, Server } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { ApplicationDetail } from '../api/types'
import { RolloutStrip } from '../components/RolloutStrip'
import { EmptyState, ErrorNote, Loading } from '../components/Feedback'
import { PageHeading } from '../components/PageHeading'
import { StatusBadge } from '../components/StatusBadge'
import { useResource } from '../hooks/useResource'
import { appTone, deploymentLabel, deploymentTone, incidentLabel, incidentTone, ms, pct, rate, relative, shortSha, title } from '../lib/format'
import { useAuth } from '../state/AuthContext'

interface CheckResult { sources: string[]; errors: Record<string, string> }

export function ApplicationDetailPage() {
  const { slug = '' } = useParams()
  const { can } = useAuth()
  const { data: app, error, loading, refresh } = useResource(() => api<ApplicationDetail>(`/api/applications/${encodeURIComponent(slug)}`), [slug], 15000)
  const [busy, setBusy] = useState(false)
  const [check, setCheck] = useState<CheckResult | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  if (loading && !app) return <div className="work-page"><Loading /></div>
  if (!app) return <div className="work-page"><PageHeading eyebrow="APPLICATION" title="Service not found" description={error ?? 'No application matches this identifier.'} /><Link className="text-link" to="/applications"><ArrowLeft size={14} /> Back to applications</Link></div>

  const tone = appTone(app.status)
  const latest = app.latest
  const runtime = app.runtime_state

  async function runCheck() {
    setBusy(true)
    setActionError(null)
    try { setCheck(await api<CheckResult>(`/api/applications/${app!.id}/check`, { method: 'POST' })) } catch (caught) { setActionError(caught instanceof Error ? caught.message : 'Check failed') } finally { setBusy(false) }
  }

  async function toggleFreeze() {
    setBusy(true)
    setActionError(null)
    try {
      await api(`/api/applications/${app!.id}/${app!.deploy_frozen ? 'unfreeze' : 'freeze'}`, { method: 'POST', json: { reason: 'Paused from the NexOps console' } })
      await refresh()
    } catch (caught) { setActionError(caught instanceof Error ? caught.message : 'Request failed') } finally { setBusy(false) }
  }

  return (
    <div className="work-page">
      <div className="detail-back-row"><Link to="/applications"><ArrowLeft size={14} /> All applications</Link><span>Workspace / Applications / {app.name}</span></div>
      <div className="service-hero panel">
        <div className={`service-hero-icon ${tone}`}>{app.name[0]}</div>
        <div className="service-hero-main"><span className="section-eyebrow">APPLICATION · {app.id.toUpperCase()}</span><div className="service-hero-title"><h1>{app.name}</h1><StatusBadge tone={tone} dot>{app.status === 'unknown' ? 'No data' : title(app.status)}</StatusBadge></div><p>{app.description ?? app.status_reason ?? 'No description'}</p>
          {can('operator') && <div className="hero-actions"><Link className="button button-secondary" to={`/applications/${app.id}/edit`}><Pencil size={13} /> Edit configuration</Link><button className="button button-secondary" disabled={busy} onClick={() => void runCheck()}><Radar size={13} /> Test telemetry</button><button className="button button-secondary" disabled={busy} onClick={() => void toggleFreeze()}>{app.deploy_frozen ? <Play size={13} /> : <Pause size={13} />} {app.deploy_frozen ? 'Resume deployments' : 'Pause deployments'}</button></div>}
        </div>
        <div className="service-hero-version"><span>CURRENT VERSION</span><strong>{app.current_version ?? '—'}</strong><small><GitBranch size={12} /> {title(app.environment)}{app.previous_version ? ` · prev ${app.previous_version}` : ''}</small></div>
      </div>
      {app.deploy_frozen && <div className="scenario-phase-note"><Pause size={14} /><span>Deployments are paused: {app.deploy_freeze_reason}. CI deploy gates will block releases (rollbacks are still allowed).</span></div>}
      {actionError && <ErrorNote message={actionError} />}
      {check && <div className={`scenario-phase-note${Object.keys(check.errors).length ? '' : ' scenario-success-note'}`}><Radar size={14} /><span>Telemetry check · sources answering: {check.sources.join(', ') || 'none'}{Object.keys(check.errors).length ? ` · errors: ${Object.entries(check.errors).map(([key, value]) => `${key}: ${value}`).join('; ')}` : ''}</span></div>}

      <div className="metric-grid detail-metrics">
        <DetailMetric label="Request rate" value={rate(latest?.request_rate)} unit="req/s" note={latest ? `Sample ${relative(latest.observed_at)}` : 'No samples'} tone="blue" />
        <DetailMetric label="Error rate" value={pct(latest?.error_rate_percent)} note="HTTP 5xx" tone={(latest?.error_rate_percent ?? 0) >= 5 ? 'red' : 'green'} />
        <DetailMetric label="P50 latency" value={ms(latest?.p50_latency_ms)} note="Median" tone="blue" />
        <DetailMetric label="P95 latency" value={ms(latest?.p95_latency_ms)} note="95th percentile" tone="blue" />
        <DetailMetric label="P99 latency" value={ms(latest?.p99_latency_ms)} note="99th percentile" tone={(latest?.p99_latency_ms ?? 0) >= 1000 ? 'red' : 'green'} />
        <DetailMetric label="CPU usage" value={pct(latest?.cpu_percent, 0)} note="Runtime saturation" tone={(latest?.cpu_percent ?? 0) >= 85 ? 'amber' : 'blue'} />
        <DetailMetric label="Transactions" value={pct(latest?.business_success_rate, 1)} note="Business success rate" tone={latest?.business_success_rate !== null && latest?.business_success_rate !== undefined && latest.business_success_rate < 95 ? 'red' : 'green'} />
      </div>

      <div className="detail-two-column">
        <section className="panel detail-section"><div className="panel-header"><div><h2>Health checks</h2><p>HTTP probes run every monitor cycle</p></div><HeartPulse size={16} className="muted-icon" /></div>
          {app.health_check_results.length ? <div className="check-list">{app.health_check_results.map((result) => { const passing = result.status === 'passing'; return <div className="check-row" key={result.name}><span className={`check-mark ${passing ? 'healthy' : 'critical'}`}>{passing ? <CheckCircle2 size={15} /> : <CircleAlert size={15} />}</span><div className="check-main"><strong>{result.name}</strong><small>{result.url}{result.error ? ` · ${result.error}` : ''}</small></div><span className="check-latency">{result.latency_ms}ms</span><StatusBadge tone={passing ? 'healthy' : 'critical'} compact>{result.http_status ?? 'ERR'}</StatusBadge></div> })}</div> : <EmptyState title="No health checks" detail="Add /health or /ready URLs in the application configuration." />}
        </section>
        <section className="panel detail-section"><div className="panel-header"><div><h2>Service information</h2><p>Ownership and telemetry sources</p></div><Server size={16} className="muted-icon" /></div><div className="metadata-list">
          <MetaRow label="Owning team" value={app.owner ?? '—'} />
          <MetaRow label="Repository" value={app.repository ?? '—'} code />
          <MetaRow label="Runtime" value={`${title(app.runtime)}${app.docker_container ? ` · ${app.docker_container}` : app.k8s_deployment ? ` · ${app.k8s_namespace}/${app.k8s_deployment}` : ''}`} />
          <MetaRow label="Prometheus" value={app.prometheus_selector ?? 'not configured'} code />
          <MetaRow label="Logs" value={app.loki_selector ?? app.cloudwatch_log_group ?? `${app.runtime} runtime logs`} code />
          <MetaRow label="Traces" value={app.tracing_service ? `Jaeger · ${app.tracing_service}` : 'not configured'} />
          <MetaRow label="Rollback" value={app.rollback_strategy === 'none' ? 'Not configured' : `${title(app.rollback_strategy)}${app.image_repository ? ` · ${app.image_repository}` : ''}`} />
          <MetaRow label="Data sources" value={latest?.sources.join(', ') || '—'} />
        </div></section>
      </div>

      <div className="detail-two-column">
        <section className="panel detail-section"><div className="panel-header"><div><h2>Dependencies</h2><p>Declared backing services</p></div><Cable size={16} className="muted-icon" /></div>
          {app.dependencies.length ? <div className="dependency-list">{app.dependencies.map((dependency) => { const dbIssue = /postgres|database|db|mysql|redis/i.test(dependency) && (latest?.db_errors ?? 0) >= 5; return <div className="dependency-row" key={dependency}><span className={`dependency-node ${dbIssue ? 'dependency-warning' : ''}`}><Box size={14} /></span><span><strong>{dependency}</strong><small>{dbIssue ? `${latest?.db_errors} connection errors in the last window` : 'No errors attributed in logs'}</small></span><StatusBadge tone={dbIssue ? 'critical' : 'healthy'} dot compact>{dbIssue ? 'Errors' : 'OK'}</StatusBadge></div> })}</div> : <EmptyState title="No dependencies declared" detail="List databases and upstream services in the configuration." />}
        </section>
        <section className="panel detail-section"><div className="panel-header"><div><h2>Replicas</h2><p>{runtime ? `${title(runtime.runtime)} · ${runtime.target}` : 'No runtime data'}</p></div><span className="pod-count">{runtime ? `${runtime.healthy_replicas}/${runtime.total_replicas} ready` : '—'}</span></div>
          {runtime?.rollout && <RolloutStrip rollout={runtime.rollout} />}
          {runtime?.replicas.length ? <div className="check-list">{runtime.replicas.map((replica) => { const ok = replica.ready; return <div className="check-row pod-detail-row" key={replica.name}><span className={`pod-status-icon ${ok ? 'healthy' : 'critical'}`}><i /></span><div className="check-main"><strong>{replica.name}</strong><small>{replica.track === 'canary' ? <span className="track-tag">canary</span> : null}{replica.version ?? replica.image} · {replica.restarts} restarts{replica.exit_code !== null ? ` · exit ${replica.exit_code}` : ''}{replica.cpu_percent !== null ? ` · CPU ${pct(replica.cpu_percent, 0)}` : ''}</small></div><StatusBadge tone={ok ? 'healthy' : 'critical'} compact>{replica.reason ?? replica.status}</StatusBadge></div> })}</div> : <EmptyState title="No replicas reported" detail="Enable Docker or Kubernetes for this runtime." />}
        </section>
      </div>

      <div className="detail-two-column detail-last-row">
        <section className="panel detail-section"><div className="panel-header"><div><h2>Deployment history</h2><p>Releases reported by CI/CD</p></div><Link className="text-link" to="/deployments">All deployments <ArrowRight size={13} /></Link></div>{app.deployments.length ? <div className="compact-record-list">{app.deployments.map((deployment) => <Link className="compact-record" key={deployment.id} to={`/deployments/${deployment.id}`}><span className={`record-icon ${deploymentTone(deployment.status)}`}><Clock3 size={14} /></span><span className="compact-record-copy"><strong>{deployment.version} · {deployment.id}{deployment.kind === 'rollback' ? ' · rollback' : ''}</strong><small>{shortSha(deployment.commit_sha)} · {deployment.author ?? 'unknown'} · {relative(deployment.finished_at ?? deployment.started_at)}</small></span><StatusBadge tone={deploymentTone(deployment.status)} compact>{deploymentLabel(deployment.status)}</StatusBadge><ArrowRight size={14} className="muted-icon" /></Link>)}</div> : <EmptyState title="No deployments" detail="Report releases from CI/CD to NexOps." />}</section>
        <section className="panel detail-section"><div className="panel-header"><div><h2>Incidents</h2><p>Active and historical cases</p></div><Link className="text-link" to="/incidents">Incident center <ArrowRight size={13} /></Link></div>{app.incidents.length ? <div className="compact-record-list">{app.incidents.map((incident) => <Link className="compact-record" key={incident.id} to={`/incidents/${incident.id}`}><span className={`record-icon ${incident.status === 'resolved' ? 'healthy' : 'critical'}`}><CircleAlert size={14} /></span><span className="compact-record-copy"><strong>{incident.id} · {incident.title}</strong><small>{relative(incident.detected_at)} · {incident.duration_seconds ? `${Math.round(incident.duration_seconds / 60)} min` : 'ongoing'}</small></span><StatusBadge tone={incidentTone(incident.status)} compact>{incidentLabel(incident.status)}</StatusBadge><ArrowRight size={14} className="muted-icon" /></Link>)}</div> : <EmptyState title="No incidents" detail="This service has no incident history." />}</section>
      </div>
    </div>
  )
}

function DetailMetric({ label, value, unit, note, tone }: { label: string; value: string; unit?: string; note: string; tone: string }) {
  return <article className={`metric-card detail-metric tone-card-${tone}`}><span>{label}</span><strong>{value}{unit && value !== '—' && <small> {unit}</small>}</strong><small>{note}</small></article>
}

function MetaRow({ label, value, code }: { label: string; value: string; code?: boolean }) {
  return <div className="metadata-row"><span>{label}</span>{code ? <code>{value}</code> : <strong>{value}</strong>}</div>
}
