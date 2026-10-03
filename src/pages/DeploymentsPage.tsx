import { useMemo, useState } from 'react'
import { ArrowRight, GitCommitHorizontal, Rocket, RotateCcw, Search } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api, query } from '../api/client'
import type { Deployment } from '../api/types'
import { ErrorNote, Loading } from '../components/Feedback'
import { PageHeading } from '../components/PageHeading'
import { StatusBadge } from '../components/StatusBadge'
import { useResource } from '../hooks/useResource'
import { deploymentLabel, deploymentTone, relative, shortSha, title } from '../lib/format'
import { useEnvironment } from '../state/EnvironmentContext'

const STATUSES = ['in_progress', 'verifying', 'healthy', 'degraded', 'rolled_back', 'failed', 'superseded', 'unverified']

export function DeploymentsPage() {
  const { apiEnvironment } = useEnvironment()
  const { data, error, loading } = useResource(() => api<Deployment[]>(`/api/deployments${query({ environment: apiEnvironment, limit: 300 })}`), [apiEnvironment], 15000)
  const [term, setTerm] = useState('')
  const [status, setStatus] = useState('all')
  const [kind, setKind] = useState('all')
  const deployments = data ?? []
  const visible = useMemo(() => deployments.filter((item) => {
    const matches = `${item.id} ${item.application_name} ${item.version} ${item.commit_sha ?? ''} ${item.author ?? ''} ${item.commit_message ?? ''}`.toLowerCase().includes(term.toLowerCase())
    return matches && (status === 'all' || item.status === status) && (kind === 'all' || item.kind === kind)
  }), [deployments, term, status, kind])
  const monthStart = new Date(new Date().getFullYear(), new Date().getMonth(), 1).getTime()
  const thisMonth = deployments.filter((item) => item.finished_at && new Date(item.finished_at).getTime() >= monthStart && item.kind !== 'rollback')
  const verified = thisMonth.filter((item) => item.status === 'healthy')
  const attention = deployments.filter((item) => item.status === 'degraded' || item.status === 'verifying')
  const scored = deployments.filter((item) => item.risk_score !== null).map((item) => item.risk_score as number).sort((a, b) => a - b)
  const medianRisk = scored.length ? scored[Math.floor(scored.length / 2)] : null

  return (
    <div className="work-page">
      <PageHeading eyebrow="RELEASE DELIVERY" title="Deployments" description="Every release reported by CI/CD, and whether production actually stayed healthy afterwards." action={<span className="snapshot-tag"><i /> Live from CI/CD webhooks</span>} />
      <div className="summary-strip four-summary">
        <MiniStat label="Deployments this month" value={String(thisMonth.length)} note="Releases + hotfixes" tone="blue" />
        <MiniStat label="Verified healthy" value={String(verified.length)} note={thisMonth.length ? `${Math.round((verified.length / thisMonth.length) * 100)}% of this month's releases` : 'No releases this month'} tone="green" />
        <MiniStat label="Verifying / degraded" value={String(attention.length).padStart(2, '0')} note={attention[0] ? `${attention[0].application_name} · ${attention[0].id}` : 'No releases under watch'} tone={attention.some((item) => item.status === 'degraded') ? 'red' : 'green'} />
        <MiniStat label="Median risk score" value={medianRisk === null ? '—' : `${medianRisk} / 100`} note="Deployment Risk Agent" tone="amber" />
      </div>
      {error && <ErrorNote message={error} />}
      <section className="panel catalog-panel">
        <div className="data-toolbar"><div className="toolbar-title"><div><h2>Release history</h2><p>{visible.length} of {deployments.length} deployments</p></div></div><div className="toolbar-controls"><label className="search-field"><Search size={15} /><input value={term} onChange={(event) => setTerm(event.target.value)} placeholder="Search releases" /></label><select className="filter-select" value={status} onChange={(event) => setStatus(event.target.value)} aria-label="Filter deployment status"><option value="all">All outcomes</option>{STATUSES.map((item) => <option key={item} value={item}>{deploymentLabel(item)}</option>)}</select><select className="filter-select" value={kind} onChange={(event) => setKind(event.target.value)} aria-label="Filter kind"><option value="all">All kinds</option><option value="release">Releases</option><option value="hotfix">Hotfixes</option><option value="rollback">Rollbacks</option></select></div></div>
        {loading && !data ? <Loading /> : <div className="wide-table-wrap"><table className="data-table deployments-table"><thead><tr><th>DEPLOYMENT</th><th>APPLICATION</th><th>ENVIRONMENT</th><th>COMMIT / AUTHOR</th><th>CI/CD</th><th>PRODUCTION</th><th>RISK</th><th>CHANGES</th><th>TIME</th><th /></tr></thead><tbody>
          {visible.map((deployment) => <tr key={deployment.id}>
            <td><Link className="table-primary-link deployment-id-link" to={`/deployments/${deployment.id}`}><span className="deployment-table-icon">{deployment.kind === 'rollback' ? <RotateCcw size={15} /> : <Rocket size={15} />}</span><strong>{deployment.id}</strong></Link></td>
            <td><strong className="plain-strong">{deployment.application_name}</strong><small className="cell-subtext">{deployment.previous_version ? `${deployment.previous_version} → ` : ''}{deployment.version}</small></td>
            <td><span className="environment-cell"><i className={`env-indicator ${deployment.environment}`} />{title(deployment.environment)}</span></td>
            <td><span className="commit-cell"><GitCommitHorizontal size={13} /><code>{shortSha(deployment.commit_sha)}</code></span><small className="cell-subtext">{deployment.author ?? 'unknown'}</small></td>
            <td><StatusBadge tone={deployment.ci_status === 'success' ? 'healthy' : deployment.ci_status === 'in_progress' ? 'warning' : 'critical'} compact>{deployment.ci_status === 'success' ? 'Passed' : title(deployment.ci_status)}</StatusBadge></td>
            <td><StatusBadge tone={deploymentTone(deployment.status)} dot compact>{deploymentLabel(deployment.status)}</StatusBadge></td>
            <td>{deployment.kind === 'rollback' ? <span className="risk-label">Rollback</span> : <RiskBadge risk={deployment.risk_level} score={deployment.risk_score} />}</td>
            <td><span>{deployment.files_changed} files</span><small className="cell-subtext">+{deployment.additions} −{deployment.deletions}</small></td>
            <td>{relative(deployment.finished_at ?? deployment.started_at)}</td>
            <td><Link to={`/deployments/${deployment.id}`} className="row-open-link" aria-label={`Open ${deployment.id}`}><ArrowRight size={15} /></Link></td>
          </tr>)}
          {visible.length === 0 && <tr><td colSpan={10}><div className="empty-state"><span>{deployments.length ? 'No deployments match those filters' : 'No deployments reported yet'}</span><small>{deployments.length ? 'Try a different search or filter.' : 'Add the NexOps notify step to your CI/CD pipeline (see Settings).'}</small></div></td></tr>}
        </tbody></table></div>}
        <div className="table-footer"><span>CI/CD shows whether tests passed. Production shows what NexOps observed after the release.</span><span>{visible.length} shown <i /> Live</span></div>
      </section>
    </div>
  )
}

function MiniStat({ label, value, note, tone }: { label: string; value: string; note: string; tone: string }) {
  return <div className={`summary-stat summary-${tone}`}><div className="summary-copy"><span>{label}</span><strong>{value}</strong><small>{note}</small></div></div>
}

export function RiskBadge({ risk, score }: { risk: string | null; score?: number | null }) {
  if (!risk) return <span className="risk-label">—</span>
  return <span className={`risk-label risk-label-${risk}`}><i />{title(risk)}{score !== null && score !== undefined ? ` · ${score}` : ''}</span>
}
