import { useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import { BarChart3, CircleHelp, Clock3, RotateCcw, Rocket, ShieldCheck } from 'lucide-react'
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, query } from '../api/client'
import type { DoraPeriod, DoraResponse } from '../api/types'
import { ErrorNote, Loading } from '../components/Feedback'
import { PageHeading } from '../components/PageHeading'
import { useResource } from '../hooks/useResource'
import { minutes, title } from '../lib/format'
import { useEnvironment } from '../state/EnvironmentContext'

type MetricKey = 'deployments' | 'lead_time_minutes' | 'change_failure_rate_percent' | 'recovery_time_minutes' | 'rework_rate_percent'

const METRICS: Array<{ key: MetricKey; level?: string; title: string; icon: ReactNode; color: string; definition: string; format: (value: number | null) => string; unit: string }> = [
  { key: 'deployments', level: 'deployment_frequency', title: 'Deployment frequency', icon: <Rocket size={16} />, color: '#63bcff', definition: 'Successful production releases (rollbacks excluded).', format: (value) => value === null ? '—' : String(value), unit: 'releases' },
  { key: 'lead_time_minutes', level: 'lead_time', title: 'Lead time for changes', icon: <Clock3 size={16} />, color: '#9c9cff', definition: 'Median time from commit to running in production.', format: minutes, unit: 'min' },
  { key: 'change_failure_rate_percent', level: 'change_failure_rate', title: 'Change failure rate', icon: <ShieldCheck size={16} />, color: '#f1b966', definition: 'Share of releases that degraded production or were rolled back.', format: (value) => value === null ? '—' : `${value}%`, unit: '%' },
  { key: 'recovery_time_minutes', level: 'recovery_time', title: 'Failed deployment recovery time', icon: <RotateCcw size={16} />, color: '#54cbaa', definition: 'Median time from a failed release to verified recovery.', format: minutes, unit: 'min' },
  { key: 'rework_rate_percent', title: 'Deployment rework rate', icon: <BarChart3 size={16} />, color: '#d598ed', definition: 'Share of deployments that were unplanned: rollbacks, hotfixes, or releases made during an open incident.', format: (value) => value === null ? '—' : `${value}%`, unit: '%' },
]

export function DoraPage() {
  const { apiEnvironment, environment } = useEnvironment()
  const [months, setMonths] = useState(6)
  const [selected, setSelected] = useState<MetricKey>('lead_time_minutes')
  const { data, error, loading } = useResource(() => api<DoraResponse>(`/api/dora${query({ months, environment: apiEnvironment ?? 'all' })}`), [months, apiEnvironment], 60000)
  if (loading && !data) return <div className="work-page"><Loading /></div>
  if (!data) return <div className="work-page"><ErrorNote message={error ?? 'DORA metrics unavailable'} /></div>

  const summary = data.summary
  const periods = data.periods
  const metric = METRICS.find((item) => item.key === selected)!
  const summaryValue = (key: MetricKey) => key === 'deployments' ? summary.deployments : summary[key]
  const totalDeployments = periods.reduce((sum, period) => sum + period.total_deployments, 0)

  return (
    <div className="work-page dora-page">
      <PageHeading eyebrow="SOFTWARE DELIVERY PERFORMANCE" title="DORA metrics" description="Computed from the deployments and incidents NexOps recorded. Delivery performance, separate from live infrastructure health." action={<select className="filter-select dora-period" value={months} onChange={(event) => setMonths(Number(event.target.value))}><option value={3}>Last 3 months</option><option value={6}>Last 6 months</option><option value={12}>Last 12 months</option></select>} />
      <div className="dora-definition-banner"><span><BarChart3 size={16} /></span><p><strong>Last 30 days · {environment === 'all' ? 'all environments' : environment}.</strong> Cards show the rolling 30-day value and its DORA performance band. The table shows calendar months.</p><small>{totalDeployments} DEPLOYMENTS RECORDED</small></div>
      <div className="dora-card-grid">
        {METRICS.map((item) => { const value = summaryValue(item.key); const level = item.level ? summary.levels[item.level] : null; return <button key={item.key} className={`dora-card${selected === item.key ? ' dora-selected' : ''}`} style={{ '--dora-color': item.color } as CSSProperties} onClick={() => setSelected(item.key)}><div className="dora-card-top"><span className="dora-icon">{item.icon}</span>{level && <span className={`dora-trend dora-level-${level}`}>{title(level)}</span>}</div><span className="dora-card-label">{item.title}<CircleHelp size={12} aria-label={item.definition} /></span><strong>{item.format(value as number | null)}{item.key === 'deployments' && <small> / 30d</small>}</strong><p>{item.definition}</p></button> })}
      </div>
      <div className="dora-chart-grid">
        <section className="panel dora-trend-panel">
          <div className="data-toolbar dora-chart-toolbar"><div className="toolbar-title"><div><h2>{metric.title}</h2><p>Monthly · last {months} months</p></div></div><div className="chart-unit-tag">{metric.unit.toUpperCase()}</div></div>
          <div className="dora-line-chart"><ResponsiveContainer width="100%" height="100%"><LineChart data={periods} margin={{ top: 18, right: 26, left: -4, bottom: 0 }}><CartesianGrid stroke="#202a35" strokeDasharray="3 5" vertical={false} /><XAxis dataKey="month" tick={{ fill: '#9aa8b8', fontSize: 13, fontFamily: 'DM Mono' }} axisLine={false} tickLine={false} dy={9} /><YAxis tick={{ fill: '#9aa8b8', fontSize: 13, fontFamily: 'DM Mono' }} axisLine={false} tickLine={false} /><Tooltip content={<DoraTooltip format={metric.format} />} cursor={{ stroke: '#43566a', strokeDasharray: '3 4' }} /><Line type="monotone" dataKey={selected} stroke={metric.color} strokeWidth={2.4} connectNulls dot={{ r: 3, fill: metric.color, stroke: '#121820', strokeWidth: 2 }} activeDot={{ r: 5 }} /></LineChart></ResponsiveContainer></div>
          <div className="chart-footnote"><span><i style={{ background: metric.color }} />{metric.title}</span><span>Months without data are left empty</span></div>
        </section>
        <section className="panel frequency-panel"><div className="panel-header"><div><h2>Deployment frequency</h2><p>Production releases per month</p></div><span className="frequency-current">{periods.at(-1)?.deployments ?? 0} <small>/ mo</small></span></div><div className="frequency-chart"><ResponsiveContainer width="100%" height="100%"><BarChart data={periods} margin={{ top: 13, right: 16, left: -8, bottom: 0 }}><CartesianGrid stroke="#202a35" strokeDasharray="3 5" vertical={false} /><XAxis dataKey="month" tick={{ fill: '#9aa8b8', fontSize: 13, fontFamily: 'DM Mono' }} axisLine={false} tickLine={false} dy={8} /><YAxis allowDecimals={false} tick={{ fill: '#9aa8b8', fontSize: 13, fontFamily: 'DM Mono' }} axisLine={false} tickLine={false} /><Tooltip content={<DoraTooltip format={(value) => `${value} releases`} />} cursor={{ fill: '#1b2731' }} /><Bar dataKey="deployments" fill="#59b7fa" radius={[4, 4, 0, 0]} maxBarSize={25} /></BarChart></ResponsiveContainer></div><div className="frequency-footer"><span>{summary.deployments_per_day.toFixed(2)} per day (30d)</span><span>{months}-month view</span></div></section>
      </div>
      <section className="panel dora-table-panel"><div className="panel-header"><div><h2>Monthly delivery performance</h2><p>Computed from recorded deployments and incidents</p></div><span className="snapshot-tag"><i /> {environment === 'all' ? 'All environments' : title(environment)}</span></div><div className="wide-table-wrap"><table className="data-table dora-data-table"><thead><tr><th>MONTH</th><th>RELEASES</th><th>LEAD TIME</th><th>CHANGE FAILURE RATE</th><th>RECOVERY TIME</th><th>REWORK RATE</th><th>FAILED / REWORK</th></tr></thead><tbody>{[...periods].reverse().map((row: DoraPeriod) => <tr key={row.period_start}><td><strong className="plain-strong">{new Date(row.period_start).toLocaleDateString(undefined, { month: 'short', year: 'numeric' })}</strong></td><td>{row.deployments}</td><td>{minutes(row.lead_time_minutes)}</td><td>{row.change_failure_rate_percent === null ? '—' : `${row.change_failure_rate_percent}%`}</td><td>{minutes(row.recovery_time_minutes)}</td><td>{row.rework_rate_percent === null ? '—' : `${row.rework_rate_percent}%`}</td><td>{row.failed_deployments} / {row.rework_deployments}</td></tr>)}</tbody></table></div></section>
      <p className="page-disclaimer"><CircleHelp size={13} /> Lead time needs commit timestamps from CI. Recovery time counts only failed releases whose incident was resolved with verified recovery.</p>
    </div>
  )
}

function DoraTooltip({ active, payload, label, format }: { active?: boolean; payload?: Array<{ value: number }>; label?: string; format: (value: number | null) => string }) {
  if (!active || !payload?.length) return null
  return <div className="chart-tooltip"><span>{label}</span><strong>{format(payload[0].value ?? null)}</strong><small>Delivery performance</small></div>
}
