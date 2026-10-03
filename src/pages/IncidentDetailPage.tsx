import { useState } from 'react'
import {
  ArrowLeft, ArrowRight, Bot, Check, CircleAlert, Clock3, FileCode2, Gauge, GitBranch, HeartPulse, History, Info, RotateCcw, Server, ShieldAlert,
  Flag, ShieldCheck, Sparkles, Terminal, Waypoints, X,
} from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { EvidenceItem, IncidentWorkflow, Remediation } from '../api/types'
import { ErrorNote, Loading } from '../components/Feedback'
import { PageHeading } from '../components/PageHeading'
import { StatusBadge } from '../components/StatusBadge'
import { useResource } from '../hooks/useResource'
import { actionLabel, clock, dateTime, duration, incidentLabel, incidentTone, ms, pct, relative, severityTone, title } from '../lib/format'
import { useAuth } from '../state/AuthContext'
import { useLive } from '../state/LiveContext'

const evidenceIcons: Record<EvidenceItem['kind'], typeof Terminal> = { LOG: Terminal, METRIC: Gauge, DEPLOYMENT: GitBranch, CHANGE: FileCode2, TRACE: Waypoints, FLAG: Flag, HEALTH: HeartPulse, HISTORY: History, RUNTIME: Server, GAP: Info }
const agentSteps = [
  { label: 'Collect deployment context', detail: 'Release, commit and diff' },
  { label: 'Compare telemetry with baseline', detail: 'Golden signals' },
  { label: 'Inspect logs and replicas', detail: 'Errors, restarts, events' },
  { label: 'Correlate incident memory', detail: 'Similar past incidents' },
  { label: 'Likely root cause', detail: 'Validated structured output' },
]

export function IncidentDetailPage() {
  const { incidentId = '' } = useParams()
  const { can } = useAuth()
  const { refreshIncidents, settings } = useLive()
  const jaeger = settings?.jaeger_ui_url ?? null
  const [busy, setBusy] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [manualAction, setManualAction] = useState('rollback')
  const [manualFlag, setManualFlag] = useState('')
  const [showAllEvidence, setShowAllEvidence] = useState(false)
  const resource = useResource(() => api<IncidentWorkflow>(`/api/incidents/${encodeURIComponent(incidentId)}/workflow`), [incidentId], 5000)
  const { data, error, loading, refresh } = resource

  if (loading && !data) return <div className="work-page"><Loading label="Loading incident workflow…" /></div>
  if (!data) return <div className="work-page"><PageHeading eyebrow="INCIDENT CENTER" title="Incident not found" description={error ?? 'No incident matches this identifier.'} /><Link className="text-link" to="/incidents"><ArrowLeft size={14} /> Back to incidents</Link></div>

  const { incident, investigation, remediations, verification, postmortem, similar } = data
  const resolved = incident.status === 'resolved'
  const pending = remediations.find((item) => item.status === 'proposed')
  const inFlight = remediations.find((item) => ['approved', 'executing', 'dispatched'].includes(item.status))
  const latest = remediations[0]
  const cited = new Set(investigation?.cited_evidence_ids ?? [])
  const evidence = investigation?.evidence ?? []
  const citedEvidence = evidence.filter((item) => cited.has(item.id))
  const otherEvidence = evidence.filter((item) => !cited.has(item.id))
  const investigating = busy === 'investigate' || investigation?.status === 'running'
  const agentState = investigation?.status === 'completed' ? 'Complete' : investigating ? 'Running' : 'Queued'
  const stepsDone = investigation?.status === 'completed' ? agentSteps.length : investigating ? 2 : 0
  const provider = investigation?.provider === 'groq' ? `Groq · ${investigation.model}` : investigation ? 'Deterministic evidence rules' : 'Not started'
  const observed = incident.observed as Record<string, number>
  const peak = incident.peak as Record<string, number>
  const baseline = incident.baseline

  async function act(key: string, path: string, body?: unknown) {
    setBusy(key)
    setActionError(null)
    try {
      await api(path, { method: 'POST', json: body ?? {} })
      await refresh()
      void refreshIncidents()
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : 'Request failed')
    } finally {
      setBusy(null)
    }
  }

  function reject(remediation: Remediation) {
    const reason = window.prompt('Why are you rejecting this action?')
    if (reason && reason.trim().length >= 3) void act('reject', `/api/remediations/${remediation.id}/reject`, { reason })
  }

  function dismiss() {
    const reason = window.prompt('Close this incident without recovery verification. Reason:')
    if (reason && reason.trim().length >= 3) void act('dismiss', `/api/incidents/${incident.id}/dismiss`, { reason })
  }

  return (
    <div className="work-page incident-detail-page">
      <div className="detail-back-row"><Link to="/incidents"><ArrowLeft size={14} /> All incidents</Link><span>Workspace / Incidents / {incident.id}</span></div>
      <section className={`incident-detail-hero ${resolved ? 'incident-resolved-hero' : 'incident-active-hero'}`}>
        <div className="incident-hero-top"><span className="incident-hero-mark"><ShieldAlert size={18} /></span><div className="incident-hero-title"><span className="section-eyebrow">{incident.id} · DETECTED {dateTime(incident.detected_at).toUpperCase()}</span><h1>{incident.title}</h1><p>{incident.summary}</p></div><StatusBadge tone={incidentTone(incident.status)} dot>{incidentLabel(incident.status)}</StatusBadge></div>
        <div className="incident-hero-meta"><span><b>SERVICE</b><Link to={`/applications/${incident.application_id}`}>{incident.application_name} <ArrowRight size={12} /></Link></span><span><b>SEVERITY</b><StatusBadge tone={severityTone(incident.severity)} compact>{title(incident.severity)}</StatusBadge></span><span><b>RELATED DEPLOYMENT</b>{incident.deployment ? <Link to={`/deployments/${incident.deployment.id}`}>{incident.deployment.id} · {incident.deployment.previous_version ?? '?'} → {incident.deployment.version} <ArrowRight size={12} /></Link> : <strong>Not correlated</strong>}</span><span><b>DURATION</b><strong>{resolved ? duration(incident.duration_seconds) : `Ongoing · ${relative(incident.detected_at).replace(' ago', '')}`}</strong></span></div>
      </section>

      {(actionError || error) && <ErrorNote message={actionError ?? `Live refresh failed: ${error}`} />}
      <div className="incident-signal-strip">
        <Signal label="Error rate" value={pct(observed.error_rate_percent)} tone={resolved ? 'green' : (observed.error_rate_percent ?? 0) >= 5 ? 'red' : 'amber'} note={`baseline ${pct(baseline.error_rate_percent)} · peak ${pct(peak.error_rate_percent)}`} />
        <Signal label="P99 latency" value={ms(observed.p99_latency_ms)} tone={resolved ? 'green' : (observed.p99_latency_ms ?? 0) >= 1000 ? 'red' : 'amber'} note={`baseline ${ms(baseline.p99_latency_ms)} · peak ${ms(peak.p99_latency_ms)}`} />
        <Signal label="CPU usage" value={pct(observed.cpu_percent, 0)} tone={resolved ? 'green' : (observed.cpu_percent ?? 0) >= 85 ? 'amber' : 'green'} note={`baseline ${pct(baseline.cpu_percent, 0)}`} />
        <Signal label="Healthy replicas" value={observed.total_replicas ? `${observed.healthy_replicas}/${observed.total_replicas}` : '—'} tone={resolved || observed.healthy_replicas === observed.total_replicas ? 'green' : 'red'} note={`restarts ${observed.restarts ?? '—'} · DB errors ${observed.db_errors ?? '—'}`} />
      </div>

      <div className="incident-investigation-grid">
        <div className="incident-investigation-main">
          <section className="panel rca-panel">
            <div className="investigation-panel-head"><div><span className="ai-preview-icon"><Sparkles size={15} /></span><div><h2>Root cause analysis</h2><p>Evidence-backed hypothesis · {provider}</p></div></div>{investigation?.confidence !== null && investigation?.confidence !== undefined && <span className="confidence-pill">{Math.round(investigation.confidence * 100)}% confidence</span>}</div>
            {investigation?.status === 'completed' ? <>
              <div className="root-cause-box"><span className="root-cause-label">LIKELY ROOT CAUSE</span><p>{investigation.root_cause}</p><div className="cause-meta"><span>Recommended <strong>{actionLabel(investigation.recommended_action, investigation.recommended_flag)}</strong></span><span>Investigated <strong>{relative(investigation.completed_at)}</strong></span></div></div>
              {investigation.reasoning_summary && <p className="investigation-reasoning">{investigation.reasoning_summary}</p>}
              {investigation.error && <p className="investigation-reasoning warning-text">{investigation.error}</p>}
              <div className="investigation-subhead"><h3>Evidence cited in the analysis</h3><span>{citedEvidence.length} of {evidence.length} items</span></div>
              <div className="evidence-list">{citedEvidence.map((item) => <EvidenceRow key={item.id} item={item} jaeger={jaeger} cited />)}</div>
              {otherEvidence.length > 0 && <button className="demo-reset-link evidence-toggle" onClick={() => setShowAllEvidence(!showAllEvidence)}>{showAllEvidence ? 'Hide' : 'Show'} {otherEvidence.length} other evidence items</button>}
              {showAllEvidence && <div className="evidence-list">{otherEvidence.map((item) => <EvidenceRow key={item.id} item={item} jaeger={jaeger} />)}</div>}
              {(investigation.alternative_hypotheses.length > 0 || investigation.next_steps.length > 0) && <div className="hypothesis-grid">
                {investigation.alternative_hypotheses.length > 0 && <div><h3>Alternative hypotheses</h3><ul>{investigation.alternative_hypotheses.map((item) => <li key={item}>{item}</li>)}</ul></div>}
                {investigation.next_steps.length > 0 && <div><h3>Next steps</h3><ul>{investigation.next_steps.map((item) => <li key={item}>{item}</li>)}</ul></div>}
              </div>}
            </> : <div className="scenario-waiting"><Sparkles size={17} /><div><strong>{investigating ? 'Investigation running…' : 'Investigation has not run yet'}</strong><span>The Investigation Agent collects the release diff, telemetry vs baseline, logs, replica state and incident memory; the Root Cause Agent then proposes a likely cause.</span></div>{can('operator') && !resolved && <button className="button button-primary" disabled={Boolean(busy)} onClick={() => void act('investigate', `/api/incidents/${incident.id}/investigate`)}>{busy === 'investigate' ? 'Investigating…' : 'Run investigation'} <ArrowRight size={13} /></button>}</div>}
            {investigation?.status === 'completed' && !resolved && can('operator') && <div className="panel-inline-actions"><button className="demo-reset-link" disabled={Boolean(busy)} onClick={() => void act('investigate', `/api/incidents/${incident.id}/investigate`)}>{busy === 'investigate' ? 'Re-investigating…' : 'Re-run investigation with fresh evidence'}</button></div>}
          </section>

          <section className="panel recommendation-panel">
            <div className="panel-header"><div><h2>{resolved ? 'Resolution' : 'Remediation'}</h2><p>Recommendation → policy check → human approval → action → verification</p></div><StatusBadge tone={resolved ? 'healthy' : pending ? 'warning' : inFlight ? 'warning' : latest?.status === 'failed' ? 'critical' : 'neutral'} compact>{resolved ? 'Recovery verified' : pending ? 'Approval required' : inFlight ? title(inFlight.status) : latest ? title(latest.status) : 'No action yet'}</StatusBadge></div>
            {remediations.length === 0 && <div className="recommendation-main"><span className="recommendation-icon"><Info size={16} /></span><div><strong>{incident.recommended_action ? actionLabel(incident.recommended_action) : 'Awaiting investigation'}</strong><small>{resolved ? incident.resolution_summary : 'The Remediation Agent proposes an action after the investigation. Policies decide whether it runs automatically, needs approval, or is forbidden.'}</small></div></div>}
            {remediations.map((remediation) => <RemediationRow key={remediation.id} remediation={remediation} canAct={can('operator') && !resolved} busy={busy} onApprove={() => void act('approve', `/api/remediations/${remediation.id}/approve`)} onReject={() => reject(remediation)} />)}
            {!resolved && can('operator') && !pending && !inFlight && <div className="approval-callout manual-action"><ShieldCheck size={14} /><span>Propose an action manually. The policy engine still decides whether approval is required.</span><select className="filter-select" value={manualAction} onChange={(event) => setManualAction(event.target.value)}><option value="rollback">Rollback</option><option value="restart">Restart</option><option value="scale_up">Scale up</option><option value="pause_deployments">Pause deployments</option><option value="disable_feature">Disable feature flag</option></select>{manualAction === 'disable_feature' && <input className="form-input mono manual-flag" placeholder="flag key" value={manualFlag} onChange={(event) => setManualFlag(event.target.value)} />}<button className="button button-secondary" disabled={Boolean(busy) || (manualAction === 'disable_feature' && !manualFlag)} onClick={() => void act('propose', `/api/incidents/${incident.id}/remediations`, { action: manualAction, flag: manualFlag || undefined })}>Propose</button></div>}
            {resolved && <div className="scenario-phase-note scenario-success-note"><Check size={14} /><span>{incident.resolution_summary}</span></div>}
          </section>

          <section className="panel recommendation-panel">
            <div className="panel-header"><div><h2>Recovery verification</h2><p>A rollback is not success: NexOps checks the system actually recovered</p></div>{verification && <StatusBadge tone={verification.status === 'passed' ? 'healthy' : verification.status === 'failed' ? 'critical' : 'warning'} compact>{verification.status === 'running' ? `${verification.healthy_samples}/${verification.required_samples} healthy samples` : title(verification.status)}</StatusBadge>}</div>
            {verification ? <>
              <div className="check-list verification-checks">{verification.checks.map((check) => <div className="check-row" key={check.name}><span className={`check-mark ${check.passed ? 'healthy' : 'critical'}`}>{check.passed ? <Check size={14} /> : <X size={14} />}</span><div className="check-main"><strong>{check.name}</strong><small>expected {check.expected}</small></div><span className="check-latency">{check.observed}</span></div>)}
                {verification.checks.length === 0 && <div className="scenario-phase-note"><Clock3 size={14} /><span>Waiting for post-remediation samples (first sample counted after {clock(verification.not_before, true)}).</span></div>}
              </div>
              <div className="verification-foot">{verification.detail ?? `Started ${relative(verification.started_at)} · deadline ${clock(verification.deadline_at)}${verification.target_version ? ` · target ${verification.target_version}` : ''}`}</div>
            </> : <div className="recommendation-main"><span className="recommendation-icon"><HeartPulse size={16} /></span><div><strong>Not started</strong><small>Verification starts automatically after a remediation executes. If the service was fixed another way, start it manually.</small></div>{!resolved && can('operator') && <button className="button button-secondary" disabled={Boolean(busy)} onClick={() => void act('verify', `/api/incidents/${incident.id}/verify`)}>Verify recovery now</button>}</div>}
          </section>
        </div>

        <aside className="incident-investigation-side">
          <section className="panel agent-progress-panel"><div className="panel-header"><div><h2>Investigation agents</h2><p>Evidence collection → root cause</p></div><span className={`agent-status-tag${agentState === 'Running' ? ' agent-running' : ''}`}><i />{agentState}</span></div><div className="agent-completion"><span><Bot size={16} /></span><div><strong>{agentState === 'Complete' ? 'Analysis complete' : agentState === 'Running' ? 'Correlating production evidence' : 'Ready to investigate'}</strong><small>{provider}</small></div></div><div className="agent-step-list">{agentSteps.map((step, index) => <div className={`agent-step${index < stepsDone ? ' agent-step-done' : index === stepsDone && agentState === 'Running' ? ' agent-step-current' : ''}`} key={step.label}><span>{index < stepsDone ? <Check size={11} /> : <i />}</span><strong>{step.label}</strong><small>{step.detail}</small></div>)}</div></section>
          <section className="panel incident-timeline-panel"><div className="panel-header"><div><h2>Incident timeline</h2><p>Persisted workflow events</p></div><Clock3 size={15} className="muted-icon" /></div>{incident.timeline?.length ? <div className="timeline-list">{incident.timeline.map((event, index) => <div className="timeline-event" key={`${event.time}-${index}`}><span className={`timeline-marker ${event.severity === 'info' ? 'neutral' : event.severity}`}>{index === (incident.timeline?.length ?? 0) - 1 ? <Sparkles size={10} /> : <i />}</span><div className="timeline-event-copy"><div><strong>{event.title}</strong><time>{clock(event.time, true)}</time></div><small>{event.detail}{event.actor ? ` · ${event.actor}` : ''}</small></div></div>)}</div> : <div className="historical-timeline-empty"><Clock3 size={16} /><span>No events yet.</span></div>}</section>
          {!resolved && can('admin') && <button className="demo-reset-link dismiss-link" onClick={dismiss}><CircleAlert size={12} /> Close without verification (false positive)</button>}
        </aside>
      </div>

      {postmortem && <section className="panel postmortem-panel"><div className="panel-header"><div><h2>Incident postmortem</h2><p>Generated by the Postmortem Agent ({postmortem.provider}) · {relative(postmortem.generated_at)}</p></div><span className="snapshot-tag"><i /> RESOLVED</span></div><div className="postmortem-grid"><PostmortemItem label="SUMMARY" title={postmortem.summary} detail={postmortem.lessons.join(' ')} /><PostmortemItem label="IMPACT" title="Customer impact" detail={postmortem.impact} /><PostmortemItem label="ROOT CAUSE" title={postmortem.root_cause} detail={investigation?.reasoning_summary ?? ''} /><PostmortemItem label="DETECTION" title="How it was detected" detail={postmortem.detection} /><PostmortemItem label="RESOLUTION" title="What was done" detail={postmortem.resolution} /><PostmortemItem label="RECOVERY" title="Verified recovery" detail={postmortem.recovery} /></div><div className="postmortem-prevention"><strong>Preventive actions</strong><ul>{postmortem.preventive_actions.map((item) => <li key={item}>{item}</li>)}</ul></div><div className="postmortem-actions"><Link className="text-link" to={`/metrics?application=${incident.application_id}`}>View telemetry <ArrowRight size={14} /></Link>{incident.deployment && <Link className="text-link" to={`/deployments/${incident.deployment.id}`}>Failed release <ArrowRight size={14} /></Link>}</div></section>}

      <section className="panel similar-incidents-panel"><div className="panel-header"><div><h2>Similar historical incidents</h2><p>Incident memory used as investigation context</p></div><History size={15} className="muted-icon" /></div>{similar.length ? <div className="similar-incident-list">{similar.map((item) => item.id ? <Link to={`/incidents/${item.id}`} className="similar-incident" key={item.id}><span className="similar-id">{item.id}</span><span><strong>{item.title}</strong><small>{item.root_cause} · {item.action}</small></span><StatusBadge tone={item.recovery_successful ? 'healthy' : 'warning'} compact>{item.recovery_successful ? 'Recovered' : 'Unverified'}</StatusBadge><ArrowRight size={14} className="muted-icon" /></Link> : null)}</div> : <div className="historical-timeline-empty"><History size={16} /><span>No similar incidents in memory yet. Resolved incidents are added automatically.</span></div>}</section>
    </div>
  )
}

function RemediationRow({ remediation, canAct, busy, onApprove, onReject }: { remediation: Remediation; canAct: boolean; busy: string | null; onApprove: () => void; onReject: () => void }) {
  const done = remediation.status === 'succeeded'
  const failed = remediation.status === 'failed' || remediation.status === 'blocked'
  const label = actionLabel(remediation.action, remediation.target_flag ?? remediation.target_version)
  return <div className="remediation-row">
    <div className={`recommendation-main${done ? ' recommendation-resolved' : ''}`}><span className="recommendation-icon">{done ? <Check size={16} /> : failed ? <X size={16} /> : <RotateCcw size={16} />}</span><div><strong>{label} <span className="remediation-status">· {title(remediation.status)}</span></strong><small>{remediation.reason}</small><small>{remediation.policy_reasons.join(' ')}</small>{remediation.approved_by && <small>Approved by {remediation.approved_by} {relative(remediation.approved_at)}{remediation.executor ? ` · executed via ${remediation.executor}` : ''}</small>}{remediation.error && <small className="critical-text">{remediation.error}</small>}{remediation.rejected_reason && <small>Rejected: {remediation.rejected_reason}</small>}</div></div>
    {remediation.status === 'proposed' && <div className="approval-callout"><ShieldAlert size={14} /><span>Policy <code>{remediation.policy_id ?? 'default'}</code> requires human approval. {remediation.target_image ? `Target image ${remediation.target_image}.` : ''}</span>{canAct && <><button className="button button-secondary" disabled={Boolean(busy)} onClick={onReject}>Reject</button><button className="button button-primary" disabled={Boolean(busy)} onClick={onApprove}>{busy === 'approve' ? 'Approving…' : `Approve ${remediation.action.replace('_', ' ')}`}</button></>}</div>}
    {(remediation.status === 'approved' || remediation.status === 'executing') && <div className="scenario-phase-note"><RotateCcw size={14} /><span>{remediation.status === 'approved' ? 'Approved; queued for execution.' : `Executing ${label.toLowerCase()}…`}</span></div>}
    {remediation.status === 'dispatched' && <div className="scenario-phase-note"><RotateCcw size={14} /><span>GitHub Actions workflow dispatched. NexOps links the rollback when the workflow reports the deployment.</span></div>}
  </div>
}

function EvidenceRow({ item, jaeger, cited = false }: { item: EvidenceItem; jaeger: string | null; cited?: boolean }) {
  const Icon = evidenceIcons[item.kind] ?? Info
  const patch = typeof item.data?.patch === 'string' ? item.data.patch : null
  const traceId = typeof item.data?.trace_id === 'string' ? item.data.trace_id : null
  return <div className="evidence-row"><span className={`evidence-kind evidence-${item.kind.toLowerCase()}`}><Icon size={14} /></span><div className="evidence-copy"><span>{item.kind} · {item.id}</span><strong>{item.title}</strong><small className="evidence-detail">{item.detail}</small>{traceId && jaeger && <a className="text-link trace-link" href={`${jaeger}/trace/${traceId}`} target="_blank" rel="noreferrer">Open trace in Jaeger ↗</a>}{patch && <pre className="patch-view">{patch}</pre>}</div>{cited && <Check size={14} className="evidence-confirmed" />}</div>
}

function Signal({ label, value, tone, note }: { label: string; value: string; tone: string; note: string }) {
  return <div className={`incident-signal signal-${tone}`}><span>{label}</span><strong>{value}</strong><small>{note}</small></div>
}

function PostmortemItem({ label, title: heading, detail }: { label: string; title: string; detail: string }) {
  return <article className="postmortem-item"><span>{label}</span><strong>{heading}</strong><p>{detail}</p></article>
}
