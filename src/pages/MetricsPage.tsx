import { useEffect, useMemo, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import { Activity, Clock3, Database, HeartPulse, RotateCw, ShoppingCart } from 'lucide-react'
import { Area, AreaChart, CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { useSearchParams } from 'react-router-dom'
import { api, query } from '../api/client'
import type { Application, MetricPoint, MetricsResponse } from '../api/types'
import { EmptyState, ErrorNote, Loading } from '../components/Feedback'
import { PageHeading } from '../components/PageHeading'
import { useResource } from '../hooks/useResource'
import { clock, ms, pct, rate } from '../lib/format'
import { useEnvironment } from '../state/EnvironmentContext'

type SignalKey = 'p99_latency_ms' | 'request_rate' | 'error_rate_percent' | 'cpu_percent'
type RangeKey = '15m' | '1h' | '6h' | '24h' | '7d'

function delta(current: number | null | undefined, previous: number | null | undefined, format: (value: number) => string): string {
  if (current === null || current === undefined) return 'No data in range'
  if (previous === null || previous === undefined) return 'No previous window'
  const diff = current - previous
  return `${diff >= 0 ? '+' : '−'}${format(Math.abs(diff))} vs previous window`
}

export function MetricsPage() {
  const { apiEnvironment } = useEnvironment()
  const [params, setParams] = useSearchParams()
  const apps = useResource(() => api<Application[]>(`/api/applications${query({ environment: apiEnvironment })}`), [apiEnvironment])
  const [selected, setSelected] = useState<SignalKey>('error_rate_percent')
  const [range, setRange] = useState<RangeKey>('1h')
  const appId = params.get('application') ?? apps.data?.[0]?.id ?? ''

  useEffect(() => {
    if (!params.get('application') && apps.data?.[0]) setParams({ application: apps.data[0].id }, { replace: true })
  }, [apps.data, params, setParams])

  const metrics = useResource(() => appId ? api<MetricsResponse>(`/api/metrics${query({ application: appId, range })}`) : Promise.resolve(null), [appId, range], 15000)
  const data = metrics.data
  const latest = data?.latest
  const previous = data?.previous ?? {}
  const thresholds = data?.thresholds ?? {}
  const signals = [
    { key: 'p99_latency_ms' as const, label: 'Latency · P99', value: ms(latest?.p99_latency_ms), sub: delta(latest?.p99_latency_ms, previous.p99_latency_ms, ms), color: '#66bdff', unit: 'ms', threshold: thresholds.p99_latency_ms },
    { key: 'request_rate' as const, label: 'Traffic · Requests', value: `${rate(latest?.request_rate)} req/s`, sub: delta(latest?.request_rate, previous.request_rate, rate), color: '#8e9aff', unit: 'req/s', threshold: undefined },
    { key: 'error_rate_percent' as const, label: 'Errors · HTTP 5xx', value: pct(latest?.error_rate_percent), sub: delta(latest?.error_rate_percent, previous.error_rate_percent, (value) => pct(value)), color: (latest?.error_rate_percent ?? 0) >= (thresholds.error_rate_percent ?? 5) ? '#ff7779' : '#59c99e', unit: '%', threshold: thresholds.error_rate_percent },
    { key: 'cpu_percent' as const, label: 'Saturation · CPU', value: pct(latest?.cpu_percent, 0), sub: delta(latest?.cpu_percent, previous.cpu_percent, (value) => pct(value, 0)), color: (latest?.cpu_percent ?? 0) >= (thresholds.cpu_percent ?? 85) ? '#f0b965' : '#59c99e', unit: '%', threshold: thresholds.cpu_percent },
  ]
  const signal = signals.find((item) => item.key === selected)!
  const chartData = useMemo(() => (data?.points ?? []).map((point) => ({ ...point, ts: new Date(point.t).getTime() })), [data])
  const longRange = range === '24h' || range === '7d'
  const tickFormat = (value: number) => longRange && range === '7d' ? new Date(value).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : clock(new Date(value).toISOString(), range === '15m')
  const markers = (data?.deployments ?? []).map((deployment) => ({ ts: new Date(deployment.t).getTime(), label: `${deployment.kind === 'rollback' ? 'ROLLBACK' : 'DEPLOY'} ${deployment.version}` }))

  return (
    <div className="work-page metrics-page">
      <PageHeading eyebrow="OBSERVABILITY · GOLDEN SIGNALS" title="Metrics" description="Latency, traffic, errors and saturation collected every monitor cycle, with release markers." action={<select className="filter-select" value={appId} onChange={(event) => setParams({ application: event.target.value })} aria-label="Application">{(apps.data ?? []).map((app) => <option key={app.id} value={app.id}>{app.name}</option>)}</select>} />
      {(apps.error || metrics.error) && <ErrorNote message={apps.error ?? metrics.error ?? ''} />}
      {!apps.loading && !apps.data?.length ? <EmptyState title="No applications registered" detail="Register a service to start collecting telemetry." /> : <>
        <div className="golden-signal-grid">{signals.map((item) => <button className={`golden-card${selected === item.key ? ' golden-selected' : ''}`} style={{ '--signal-color': item.color } as CSSProperties} key={item.key} onClick={() => setSelected(item.key)}><span className="golden-card-label">{item.label}</span><strong>{item.value}</strong><small>{item.sub}</small><span className="golden-card-trend"><Activity size={14} /> {data?.application_name ?? '—'}</span></button>)}</div>
        <section className="panel metric-explorer-panel"><div className="data-toolbar metrics-toolbar"><div className="toolbar-title"><div><h2>{signal.label}</h2><p>{data ? `${data.sample_count} samples · ${data.application_name}` : 'Loading…'}</p></div></div><div className="range-switch" role="group" aria-label="Metric time range">{(['15m', '1h', '6h', '24h', '7d'] as RangeKey[]).map((item) => <button className={range === item ? 'range-selected' : ''} key={item} onClick={() => setRange(item)}>{item}</button>)}</div></div>
          <div className="metric-chart-callout"><div><strong>{signal.value}</strong><span style={{ color: signal.color }}>{signal.sub}</span></div><span><Clock3 size={13} /> {signal.threshold !== undefined ? `Detection threshold ${signal.unit === 'ms' ? ms(signal.threshold) : `${signal.threshold}${signal.unit}`}` : 'No threshold for traffic'}</span></div>
          {metrics.loading && !data ? <Loading /> : chartData.length ? <div className="metric-main-chart"><ResponsiveContainer width="100%" height="100%"><AreaChart data={chartData} margin={{ top: 16, right: 26, left: -4, bottom: 0 }}><defs><linearGradient id="metricFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={signal.color} stopOpacity={0.24} /><stop offset="95%" stopColor={signal.color} stopOpacity={0.015} /></linearGradient></defs><CartesianGrid stroke="#202a35" strokeDasharray="3 5" vertical={false} /><XAxis dataKey="ts" type="number" scale="time" domain={['dataMin', 'dataMax']} tickFormatter={tickFormat} tick={{ fill: '#9aa8b8', fontSize: 13, fontFamily: 'DM Mono' }} axisLine={false} tickLine={false} minTickGap={30} dy={10} /><YAxis tick={{ fill: '#9aa8b8', fontSize: 13, fontFamily: 'DM Mono' }} axisLine={false} tickLine={false} tickFormatter={(value: number) => selected === 'request_rate' ? `${value}` : selected === 'p99_latency_ms' ? `${value}ms` : `${value}%`} width={55} /><Tooltip content={<MetricTooltip unit={signal.unit} timeLabel />} cursor={{ stroke: '#43566a', strokeDasharray: '3 4' }} />{markers.map((marker) => <ReferenceLine key={`${marker.ts}-${marker.label}`} x={marker.ts} stroke="#58b7ff" strokeDasharray="4 4" strokeWidth={1.3} label={{ value: marker.label, position: 'insideTopLeft', fill: '#91cbff', fontSize: 13, fontFamily: 'DM Mono' }} />)}{signal.threshold !== undefined && <ReferenceLine y={signal.threshold} stroke="#f0b965" strokeDasharray="6 4" strokeOpacity={0.6} />}<Area type="monotone" dataKey={selected} name={signal.label} stroke={signal.color} strokeWidth={2.3} fill="url(#metricFill)" connectNulls isAnimationActive={false} activeDot={{ r: 4, stroke: signal.color, strokeWidth: 2, fill: '#121821' }} /></AreaChart></ResponsiveContainer></div> : <EmptyState title="No samples in this range" detail="The monitor stores one sample per service per cycle once telemetry sources answer." />}
          <div className="metric-chart-legend"><span><i style={{ background: signal.color }} />{signal.label}</span>{markers.length > 0 && <span><i className="deploy-legend" />{markers.length} release event(s)</span>}<span>{chartData.length} points</span></div>
        </section>
        <div className="submetric-grid">
          <Submetric icon={<Database size={15} />} label="Database connection errors" value={String(latest?.db_errors ?? '—')} unit="in window" note="From logs (Loki / runtime)" tone={(latest?.db_errors ?? 0) >= (thresholds.db_error_count ?? 5) ? 'red' : 'green'} />
          <Submetric icon={<RotateCw size={15} />} label="Container restarts" value={String(latest?.restarts ?? '—')} unit="current replicas" note={latest?.total_replicas ? `${latest.healthy_replicas}/${latest.total_replicas} ready` : 'No runtime data'} tone={(latest?.restarts ?? 0) >= (thresholds.restart_count ?? 3) ? 'amber' : 'green'} />
          <Submetric icon={<HeartPulse size={15} />} label="Memory utilization" value={pct(latest?.memory_percent, 0)} unit="" note="Runtime saturation" tone="blue" />
          <Submetric icon={<ShoppingCart size={15} />} label="Business transactions" value={pct(latest?.business_success_rate, 1)} unit="" note="business_transactions_total success" tone={latest?.business_success_rate !== null && latest?.business_success_rate !== undefined && latest.business_success_rate < 95 ? 'red' : 'green'} />
        </div>
        <div className="mini-metric-chart-grid">
          <MiniTrend title="Database connection errors" dataKey="db_errors" color="#ff7779" unit="" data={data?.points ?? []} />
          <MiniTrend title="P95 latency" dataKey="p95_latency_ms" color="#66bdff" unit="ms" data={data?.points ?? []} />
          <MiniTrend title="Memory utilization" dataKey="memory_percent" color="#8e9aff" unit="%" data={data?.points ?? []} />
        </div>
      </>}
      <p className="page-disclaimer"><Activity size={13} /> Release markers show when deployments finished. They support correlation, not causation by themselves.</p>
    </div>
  )
}

function MetricTooltip({ active, payload, label, unit, timeLabel = false }: { active?: boolean; payload?: Array<{ value: number }>; label?: string | number; unit: string; timeLabel?: boolean }) {
  if (!active || !payload?.length || payload[0].value === null || payload[0].value === undefined) return null
  return <div className="chart-tooltip"><span>{timeLabel && typeof label === 'number' ? new Date(label).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : null}</span><strong>{Number(payload[0].value).toLocaleString(undefined, { maximumFractionDigits: 2 })}{unit === '%' ? '%' : unit === 'ms' ? ' ms' : ` ${unit}`}</strong><small>Collected sample</small></div>
}

function Submetric({ icon, label, value, unit, note, tone }: { icon: ReactNode; label: string; value: string; unit: string; note: string; tone: string }) {
  return <div className={`submetric-card submetric-${tone}`}><div className="submetric-top"><span>{icon}</span><small>{label}</small></div><strong>{value}<small> {unit}</small></strong><span>{note}</span></div>
}

function MiniTrend({ title, dataKey, color, unit, data }: { title: string; dataKey: keyof MetricPoint; color: string; unit: string; data: MetricPoint[] }) {
  const current = data.length ? data[data.length - 1][dataKey] : null
  return <section className="panel mini-metric-panel"><div className="mini-metric-heading"><span>{title}</span><strong>{current === null || current === undefined ? '—' : `${Math.round(Number(current) * 10) / 10}${unit}`}</strong></div><div className="mini-metric-chart"><ResponsiveContainer width="100%" height="100%"><LineChart data={data} margin={{ top: 5, right: 3, bottom: 0, left: 3 }}><YAxis hide domain={['auto', 'auto']} /><Tooltip content={<MetricTooltip unit={unit} />} /><Line type="monotone" dataKey={dataKey} stroke={color} strokeWidth={1.8} dot={false} connectNulls activeDot={{ r: 3, fill: color, stroke: '#11171e', strokeWidth: 2 }} /></LineChart></ResponsiveContainer></div><span className="mini-metric-period">{data.length} samples</span></section>
}
