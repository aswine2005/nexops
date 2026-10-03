import { useEffect, useState } from 'react'
import { Copy, FileSearch, RefreshCw, Search, Terminal } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { api, query } from '../api/client'
import type { Application, LogsResponse } from '../api/types'
import { ErrorNote, Loading } from '../components/Feedback'
import { PageHeading } from '../components/PageHeading'
import { useResource } from '../hooks/useResource'
import { dateTime } from '../lib/format'
import { useEnvironment } from '../state/EnvironmentContext'
import { useLive } from '../state/LiveContext'

export function LogsPage() {
  const { apiEnvironment } = useEnvironment()
  const jaeger = useLive().settings?.jaeger_ui_url ?? null
  const [params, setParams] = useSearchParams()
  const apps = useResource(() => api<Application[]>(`/api/applications${query({ environment: apiEnvironment })}`), [apiEnvironment])
  const application = params.get('application') ?? ''
  const [input, setInput] = useState('')
  const [term, setTerm] = useState('')
  const [level, setLevel] = useState('')
  const [range, setRange] = useState('1h')
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    const timer = window.setTimeout(() => setTerm(input), 400)
    return () => window.clearTimeout(timer)
  }, [input])

  const logs = useResource(() => api<LogsResponse>(`/api/logs${query({ application: application || undefined, environment: application ? undefined : apiEnvironment, level: level || undefined, q: term || undefined, range, limit: 300 })}`), [application, apiEnvironment, level, term, range], 20000)
  const entries = logs.data?.entries ?? []
  const sources = Object.entries(logs.data?.sources ?? {}).map(([app, source]) => `${app}: ${source}`).join(' · ') || 'none'
  const providerErrors = Object.entries(logs.data?.errors ?? {})

  async function copyVisible() {
    const text = entries.map((entry) => `${entry.timestamp} ${entry.level} ${entry.service} ${entry.source} ${entry.message}`).join('\n')
    try { await navigator.clipboard.writeText(text); setCopied(true); window.setTimeout(() => setCopied(false), 1600) } catch { setCopied(false) }
  }

  return (
    <div className="work-page logs-page">
      <PageHeading eyebrow="OBSERVABILITY · APPLICATION LOGS" title="Logs" description="Live log search across Loki, CloudWatch Logs, Docker and Kubernetes, depending on each service's configuration." action={<button className="button button-secondary" onClick={() => void logs.refresh()}><RefreshCw size={13} /> Refresh</button>} />
      {logs.error && <ErrorNote message={logs.error} />}
      {providerErrors.length > 0 && <ErrorNote message={`Log provider errors · ${providerErrors.map(([app, message]) => `${app}: ${message}`).join(' · ')}`} />}
      <section className="panel log-explorer-panel"><div className="data-toolbar log-toolbar"><div className="toolbar-title"><span className="logs-title-icon"><Terminal size={16} /></span><div><h2>Log explorer</h2><p>{entries.length} entries · newest first</p></div></div><div className="toolbar-controls"><label className="search-field log-search"><Search size={15} /><input value={input} onChange={(event) => setInput(event.target.value)} placeholder="Search message text or trace ID" /></label><select className="filter-select" value={application} onChange={(event) => setParams(event.target.value ? { application: event.target.value } : {})} aria-label="Filter by service"><option value="">All services</option>{(apps.data ?? []).map((app) => <option key={app.id} value={app.id}>{app.name}</option>)}</select><select className="filter-select" value={level} onChange={(event) => setLevel(event.target.value)} aria-label="Filter log level"><option value="">All levels</option><option value="ERROR">ERROR</option><option value="WARN">WARN</option><option value="ERROR,WARN">ERROR + WARN</option><option value="INFO">INFO</option><option value="DEBUG">DEBUG</option></select><select className="filter-select time-select" value={range} onChange={(event) => setRange(event.target.value)} aria-label="Select time range"><option value="15m">Last 15 minutes</option><option value="1h">Last 1 hour</option><option value="6h">Last 6 hours</option><option value="24h">Last 24 hours</option></select></div></div>
        <div className="log-query-bar"><span><FileSearch size={14} /> Query</span><code>{term ? `line contains "${term}"` : 'all lines'}{level ? ` · level in (${level})` : ''} · {range}</code><span className="query-source">Source: {sources}</span></div>
        <div className="log-table-toolbar"><span><i /> {entries.length} EVENTS</span><button className="button button-secondary copy-logs" onClick={() => void copyVisible()}><Copy size={13} />{copied ? 'Copied' : 'Copy visible'}</button></div>
        {logs.loading && !logs.data ? <Loading label="Querying log providers…" /> : <div className="wide-table-wrap log-table-wrap"><table className="data-table log-data-table"><thead><tr><th>TIME</th><th>LEVEL</th><th>SERVICE</th><th>SOURCE</th><th>MESSAGE</th><th>TRACE ID</th></tr></thead><tbody>{entries.map((entry, index) => <tr key={`${entry.timestamp}-${index}`}><td className="log-timestamp">{dateTime(entry.timestamp)}</td><td><span className={`log-level level-${entry.level.toLowerCase()}`}>{entry.level}</span></td><td>{entry.service}</td><td><code>{entry.source}</code></td><td className={`log-message-cell ${entry.level === 'ERROR' ? 'log-message-error' : entry.level === 'WARN' ? 'log-message-warn' : ''}`}>{entry.message}</td><td>{entry.trace_id ? <span className="trace-cell"><button className="trace-button" title="Show all log lines of this request" onClick={() => setInput(entry.trace_id ?? '')}>{entry.trace_id.slice(0, 16)}</button>{jaeger && entry.trace_id.length === 32 && <a className="text-link" href={`${jaeger}/trace/${entry.trace_id}`} target="_blank" rel="noreferrer" title="Open trace in Jaeger">↗</a>}</span> : '—'}</td></tr>)}{entries.length === 0 && <tr><td colSpan={6}><div className="empty-state"><span>No log events found</span><small>Adjust the query, or configure a log source (Loki selector, CloudWatch log group, Docker or Kubernetes) for the service.</small></div></td></tr>}</tbody></table></div>}
        <div className="table-footer"><span>Queries run live against each service's log provider.</span><span>Newest first <i /> {range}</span></div>
      </section>
    </div>
  )
}
