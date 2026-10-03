import { useState } from 'react'
import { ArrowRight, Bot, Check, CircleAlert, LoaderCircle } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api, query } from '../api/client'
import type { Agent, AgentRun } from '../api/types'
import { EmptyState, ErrorNote, Loading } from '../components/Feedback'
import { PageHeading } from '../components/PageHeading'
import { StatusBadge } from '../components/StatusBadge'
import { useResource } from '../hooks/useResource'
import { relative, title } from '../lib/format'

export function AgentsPage() {
  const agents = useResource(() => api<Agent[]>('/api/agents'), [], 10000)
  const [filter, setFilter] = useState('')
  const runs = useResource(() => api<AgentRun[]>(`/api/agents/runs${query({ agent: filter || undefined, limit: 80 })}`), [filter], 10000)
  if (agents.loading && !agents.data) return <div className="work-page"><Loading /></div>
  const names = Object.fromEntries((agents.data ?? []).map((agent) => [agent.id, agent.name]))

  return (
    <div className="work-page">
      <PageHeading eyebrow="AGENTIC RELIABILITY" title="AI Agents" description="Each agent owns one step of the reliability loop. Detection is deterministic; agents investigate, recommend and verify, and policies gate every action." action={<span className="snapshot-tag"><i /> {agents.data?.find((agent) => agent.uses_llm)?.provider === 'groq' ? 'Groq enabled' : 'Rules mode'}</span>} />
      {(agents.error || runs.error) && <ErrorNote message={agents.error ?? runs.error ?? ''} />}
      <div className="agent-grid">
        {(agents.data ?? []).map((agent) => <section key={agent.id} className={`panel agent-card${filter === agent.id ? ' agent-card-selected' : ''}`}>
          <div className="agent-card-head"><span className="ai-orb"><Bot size={15} /></span><div><span className="section-eyebrow">{agent.stage.toUpperCase()}</span><h2>{agent.name}</h2></div><span className={`agent-status-tag${agent.status === 'running' ? ' agent-running' : ''}`}><i />{title(agent.status)}</span></div>
          <p>{agent.description}</p>
          <div className="agent-meta"><span>Trigger <strong>{agent.trigger}</strong></span><span>Engine <strong>{agent.uses_llm ? (agent.provider === 'groq' ? `Groq · ${agent.model}` : 'Evidence rules (no LLM key)') : 'Deterministic rules'}</strong></span></div>
          <div className="agent-io"><div><small>INPUTS</small>{agent.inputs.map((item) => <span key={item}>{item}</span>)}</div><div><small>OUTPUTS</small>{agent.outputs.map((item) => <span key={item}>{item}</span>)}</div></div>
          <div className="agent-stats"><span><strong>{agent.runs_total}</strong> runs</span><span><strong>{agent.runs_24h}</strong> in 24h</span><span><strong>{agent.success_rate === null ? '—' : `${agent.success_rate}%`}</strong> success</span><span>Last <strong>{relative(agent.last_run?.started_at)}</strong></span></div>
          <button className="demo-reset-link" onClick={() => setFilter(filter === agent.id ? '' : agent.id)}>{filter === agent.id ? 'Show all runs' : 'Show runs'}</button>
        </section>)}
      </div>
      <section className="panel catalog-panel">
        <div className="data-toolbar"><div className="toolbar-title"><div><h2>Agent runs</h2><p>{filter ? names[filter] : 'All agents'} · most recent first</p></div></div></div>
        {runs.loading && !runs.data ? <Loading /> : <div className="wide-table-wrap"><table className="data-table"><thead><tr><th>AGENT</th><th>STATUS</th><th>SUMMARY</th><th>CONTEXT</th><th>ENGINE</th><th>DURATION</th><th>STARTED</th></tr></thead><tbody>
          {(runs.data ?? []).map((run) => <tr key={run.id}><td><strong className="plain-strong">{names[run.agent] ?? run.agent}</strong></td><td><StatusBadge tone={run.status === 'completed' ? 'healthy' : run.status === 'failed' ? 'critical' : 'warning'} compact>{run.status === 'running' ? <LoaderCircle size={10} className="spin" /> : run.status === 'completed' ? <Check size={10} /> : <CircleAlert size={10} />} {title(run.status)}</StatusBadge></td><td className="agent-summary-cell">{run.error ?? run.summary ?? '—'}</td><td>{run.incident_id ? <Link className="inline-record-link" to={`/incidents/${run.incident_id}`}>{run.incident_id}</Link> : run.deployment_id ? <Link className="inline-record-link" to={`/deployments/${run.deployment_id}`}>{run.deployment_id}</Link> : '—'}</td><td>{run.provider ?? '—'}{run.model ? ` · ${run.model}` : ''}</td><td>{run.duration_seconds === null ? '—' : `${run.duration_seconds}s`}</td><td>{relative(run.started_at)}</td></tr>)}
          {!runs.data?.length && <tr><td colSpan={7}><EmptyState title="No agent runs yet" detail="Agents run when deployments are reported and incidents are detected." /></td></tr>}
        </tbody></table></div>}
        <div className="table-footer"><span>Every agent execution is recorded for auditability.</span><Link className="text-link" to="/policies">Policies that govern actions <ArrowRight size={13} /></Link></div>
      </section>
    </div>
  )
}
