import { Activity, AlertTriangle, ArrowDownRight, ArrowUpRight, Cpu, Gauge, HeartPulse, Minus, Siren } from 'lucide-react'
import type { HealthTone } from '../lib/format'

export type MetricIcon = 'error' | 'latency' | 'traffic' | 'cpu' | 'memory' | 'incident'
const iconMap = { error: Siren, latency: Gauge, traffic: Activity, cpu: Cpu, memory: HeartPulse, incident: AlertTriangle } satisfies Record<MetricIcon, typeof Siren>

interface MetricCardProps {
  label: string
  value: string
  unit?: string
  delta: string
  direction?: 'up' | 'down' | 'flat'
  context: string
  icon: MetricIcon
  tone: HealthTone
}

export function MetricCard({ label, value, unit, delta, direction = 'flat', context, icon, tone }: MetricCardProps) {
  const Icon = iconMap[icon]
  const negative = tone === 'critical' || tone === 'warning'
  const Arrow = direction === 'up' ? ArrowUpRight : direction === 'down' ? ArrowDownRight : Minus
  return (
    <article className={`metric-card metric-${tone}`}>
      <div className="metric-card-head"><span>{label}</span><span className={`metric-icon metric-icon-${tone}`}><Icon size={15} strokeWidth={1.8} /></span></div>
      <div className="metric-main"><strong>{value}</strong>{unit && <span>{unit}</span>}</div>
      <div className="metric-foot"><span className={`metric-delta${negative ? ' delta-negative' : ' delta-neutral'}`}><Arrow size={13} />{delta}</span><span className="metric-context">{context}</span></div>
    </article>
  )
}
