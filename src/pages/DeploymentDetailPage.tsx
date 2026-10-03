import { useState } from 'react'
import { ArrowLeft, ArrowRight, Check, Clock3, ExternalLink, FileCode2, GitCommitHorizontal, ShieldAlert, X } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { CanaryAnalysis, Deployment } from '../api/types'
import { EmptyState, Loading } from '../components/Feedback'
import { PageHeading } from '../components/PageHeading'
import { StatusBadge } from '../components/StatusBadge'
import { useResource } from '../hooks/useResource'
import { clock, dateTime, deploymentLabel, deploymentTone, minutes, ms, pct, shortSha, title } from '../lib/format'
import { RiskBadge } from './DeploymentsPage'

export function DeploymentDetailPage() {
  const { deploymentId = '' } = useParams()
  const { data: deployment, error, loading } = useResource(() => api<Deployment>(`/api/deployments/${encodeURIComponent(deploymentId)}`), [deploymentId], 15000)
  const [openPatch, setOpenPatch] = useState<string | null>(null)
  if (loading && !deployment) return <div className="work-page"><Loading /></div>
  if (!deployment) return <div className="work-page"><PageHeading eyebrow="RELEASE DELIVERY" title="Deployment not found" description={error ?? 'No release matches this identifier.'} /><Link className="text-link" to="/deployments"><ArrowLeft size={14} /> Back to deployments</Link></div>

  const verifyStatus = deployment.status === 'healthy' ? 'Passed' : deployment.status === 'degraded' ? 'Degraded' : deployment.status === 'rolled_back' ? 'Rolled back' : deployment.status === 'verifying' ? 'Verifying' : deployment.status === 'failed' ? 'Skipped' : deployment.status === 'aborted' ? 'Canary aborted' : title(deployment.status)
  const stages = [
    ...deployment.pipeline.filter((stage) => stage.name.toLowerCase() !== 'verify').map((stage) => ({ label: stage.name, description: `Reported by ${deployment.source.replace('_', ' ')}`, status: stage.status })),
    { label: 'Production verification', description: deployment.status_reason ?? 'NexOps compares post-release telemetry with the pre-release baseline.', status: verifyStatus },
  ]

  return (
    <div className="work-page">
      <div className="detail-back-row"><Link to="/deployments"><ArrowLeft size={14} /> All deployments</Link><span>Workspace / Deployments / {deployment.id}</span></div>
      <div className="detail-title-row"><div><span className="section-eyebrow">{deployment.kind.toUpperCase()} · {deployment.id}</span><h1>{deployment.application_name} <span className="detail-version">{deployment.version}</span></h1><p>{deployment.commit_message ?? deployment.summary ?? 'No change description'}</p></div><StatusBadge tone={deploymentTone(deployment.status)} dot>{deploymentLabel(deployment.status)}</StatusBadge></div>
      <div className="detail-overview-strip panel"><MetaBlock label="Environment" value={title(deployment.environment)} tone="blue" /><MetaBlock label="Previous version" value={deployment.previous_version ?? '—'} /><MetaBlock label="Commit" value={shortSha(deployment.commit_sha)} code /><MetaBlock label="Deployed by" value={deployment.author ?? 'unknown'} /><MetaBlock label="Finished" value={dateTime(deployment.finished_at ?? deployment.started_at)} /><div className="overview-risk"><span>CALCULATED RISK</span><RiskBadge risk={deployment.risk_level} score={deployment.risk_score} /></div></div>

      <section className="panel release-stage-panel"><div className="panel-header"><div><h2>Deployment verification</h2><p>CI/CD stages, then NexOps production verification</p></div>{deployment.ci_run_url ? <a className="snapshot-tag" href={deployment.ci_run_url} target="_blank" rel="noreferrer"><ExternalLink size={11} /> CI run</a> : <span className="snapshot-tag"><i /> {deployment.source}</span>}</div>
        <div className="stage-grid">{stages.map((stage, index) => { const lowered = stage.status.toLowerCase(); const failed = ['failure', 'failed', 'degraded', 'rolled back', 'cancelled', 'aborted', 'canary aborted'].includes(lowered); const attention = failed || ['in_progress', 'verifying', 'pending'].includes(lowered); return <div className={`stage-card${attention ? ' stage-warning' : ''}`} key={`${stage.label}-${index}`}><span className="stage-index">{failed ? <X size={13} /> : attention ? '!' : <Check size={13} />}</span><span className="stage-count">{String(index + 1).padStart(2, '0')}</span><strong>{stage.label}</strong><small>{stage.description}</small><StatusBadge tone={failed ? 'critical' : attention ? 'warning' : 'healthy'} compact>{title(stage.status)}</StatusBadge></div> })}</div>
      </section>

      {deployment.canary_analyses && deployment.canary_analyses.length > 0 && <CanaryPanel analyses={deployment.canary_analyses} />}

      <div className="detail-two-column">
        <section className="panel detail-section"><div className="panel-header"><div><h2>Change summary</h2><p>{deployment.files_changed} files · +{deployment.additions} −{deployment.deletions}</p></div><FileCode2 size={16} className="muted-icon" /></div>
          {deployment.changes?.length ? <div className="change-list">{deployment.changes.map((change) => <div key={change.file}><button className="change-row change-button" onClick={() => setOpenPatch(openPatch === change.file ? null : change.file)}><span className="file-change-icon"><FileCode2 size={14} /></span><span className="change-file"><strong>{change.file}</strong><small>{change.risk_tag ?? title(change.change_type)}</small></span><span className="diff-add">+{change.additions}</span><span className="diff-remove">−{change.deletions}</span></button>{openPatch === change.file && change.patch && <pre className="patch-view">{change.patch}</pre>}</div>)}</div> : <EmptyState title="No file-level changes" detail="CI did not send a diff and no GitHub token is configured." />}
          <div className="diff-summary"><span>Repository <code>{deployment.repository ?? '—'}</code></span><span className="risk-context"><ShieldAlert size={13} /> Risk is an estimate from changed files, size, CI status and history.</span></div>
        </section>
        <section className="panel detail-section"><div className="panel-header"><div><h2>Risk assessment</h2><p>{deployment.risk_assessment?.summary ?? 'Deployment Risk Agent'}</p></div><Clock3 size={16} className="muted-icon" /></div>
          <div className="metadata-list">
            <MetaRow label="Release" value={`${deployment.previous_version ?? '—'} → ${deployment.version}`} />
            <MetaRow label="Image" value={deployment.image ?? '—'} />
            <MetaRow label="Branch" value={deployment.branch ?? '—'} />
            <MetaRow label="Lead time" value={minutes(deployment.lead_time_minutes)} />
            <MetaRow label="Verified at" value={dateTime(deployment.verified_at)} />
          </div>
          {deployment.risk_factors.length > 0 && <ul className="risk-factor-list">{deployment.risk_factors.map((factor) => <li key={factor}>{factor}</li>)}</ul>}
          {deployment.incident && <Link className="related-incident-link" to={`/incidents/${deployment.incident.id}`}><span><ShieldAlert size={14} /> Correlated incident {deployment.incident.id} · {deployment.incident.title}</span><ArrowRight size={14} /></Link>}
        </section>
      </div>
      <div className="detail-footnote"><span><i /> {deployment.source === 'nexops' ? 'Rollback executed by NexOps' : `Reported by ${deployment.source}`}</span><span>CI/CD {deployment.ci_status} · production {deploymentLabel(deployment.status).toLowerCase()}</span></div>
    </div>
  )
}

function MetaBlock({ label, value, code, tone }: { label: string; value: string; code?: boolean; tone?: string }) {
  return <div className="overview-meta"><span>{label}</span><strong className={tone ? `overview-${tone}` : ''}>{code ? <><GitCommitHorizontal size={13} /> <code>{value}</code></> : value}</strong></div>
}

function MetaRow({ label, value }: { label: string; value: string }) {
  return <div className="metadata-row"><span>{label}</span><strong>{value}</strong></div>
}

function CanaryPanel({ analyses }: { analyses: CanaryAnalysis[] }) {
  const tone = (verdict: string) => verdict === 'pass' ? 'healthy' : verdict === 'fail' ? 'critical' : 'warning'
  return (
    <section className="panel detail-section"><div className="panel-header"><div><h2>Canary analysis</h2><p>Argo Rollouts asked NexOps for a verdict at each analysis step: canary pods vs stable pods in Prometheus</p></div><span className="snapshot-tag"><i /> {analyses.length} measurement{analyses.length === 1 ? '' : 's'}</span></div>
      <div className="wide-table-wrap"><table className="data-table canary-table"><thead><tr><th>TIME</th><th>CANARY</th><th>VERDICT</th><th>ERROR RATE</th><th>P99</th><th>BUSINESS SUCCESS</th><th>REASON</th></tr></thead><tbody>
        {analyses.map((item, index) => <tr key={`${item.at}-${index}`}>
          <td>{clock(item.at, true)}</td>
          <td><code>{item.canary_version ?? '—'}</code><small className="cell-subtext">{item.canary_weight !== null ? `${item.canary_weight}% of pods` : ''}</small></td>
          <td><StatusBadge tone={tone(item.verdict)} compact>{title(item.verdict)}</StatusBadge></td>
          <td>{pct(item.canary.error_rate_percent)}<small className="cell-subtext">stable {pct(item.stable.error_rate_percent)} · limit {pct(item.limits?.error_rate_percent)}</small></td>
          <td>{ms(item.canary.p99_latency_ms)}<small className="cell-subtext">stable {ms(item.stable.p99_latency_ms)} · limit {ms(item.limits?.p99_latency_ms)}</small></td>
          <td>{pct(item.canary.business_success_rate)}<small className="cell-subtext">min {pct(item.limits?.business_success_rate)}</small></td>
          <td className="cell-subtext">{item.reasons.join('; ') || '—'}</td>
        </tr>)}
      </tbody></table></div>
    </section>
  )
}
