export type HealthTone = 'healthy' | 'degraded' | 'critical' | 'warning' | 'neutral'

export function pct(value: number | null | undefined, digits?: number): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  const places = digits ?? (Math.abs(value) < 1 ? 2 : 1)
  return `${value.toFixed(places)}%`
}

export function ms(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value >= 1000 ? `${(value / 1000).toFixed(2)}s` : `${Math.round(value)}ms`
}

export function rate(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return value >= 100 ? Math.round(value).toLocaleString() : value.toFixed(value >= 10 ? 1 : 2)
}

export function num(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined) return '—'
  return value.toLocaleString(undefined, { maximumFractionDigits: digits })
}

export function minutes(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  if (value < 1) return `${Math.round(value * 60)}s`
  if (value < 90) return `${value.toFixed(value < 10 ? 1 : 0)} min`
  const hours = value / 60
  return hours < 48 ? `${hours.toFixed(1)} h` : `${(hours / 24).toFixed(1)} d`
}

export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return 'Ongoing'
  return minutes(seconds / 60)
}

export function relative(iso: string | null | undefined): string {
  if (!iso) return '—'
  const then = new Date(iso).getTime()
  const diff = Math.round((Date.now() - then) / 1000)
  if (diff < 0) return 'just now'
  if (diff < 45) return `${diff}s ago`
  if (diff < 3600) return `${Math.round(diff / 60)} min ago`
  if (diff < 86400) return `${Math.round(diff / 3600)} hr ago`
  if (diff < 86400 * 7) return `${Math.round(diff / 86400)} d ago`
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export function clock(iso: string | null | undefined, seconds = false): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', ...(seconds ? { second: '2-digit' } : {}), hour12: false })
}

export function shortSha(sha: string | null | undefined): string {
  if (!sha) return '—'
  return sha.startsWith('local-') ? sha : sha.slice(0, 7)
}

export function initials(name: string | null | undefined): string {
  if (!name) return '?'
  return name.split(/[\s._@-]+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join('') || '?'
}

export function title(text: string | null | undefined): string {
  if (!text) return '—'
  return text.replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

export function appTone(status: string | null | undefined): HealthTone {
  switch (status) {
    case 'healthy': return 'healthy'
    case 'degraded': return 'warning'
    case 'critical': return 'critical'
    default: return 'neutral'
  }
}

export function deploymentTone(status: string | null | undefined): HealthTone {
  switch (status) {
    case 'healthy': return 'healthy'
    case 'degraded': case 'failed': return 'critical'
    case 'verifying': case 'in_progress': case 'aborted': return 'warning'
    default: return 'neutral'
  }
}

export function deploymentLabel(status: string | null | undefined): string {
  const labels: Record<string, string> = {
    in_progress: 'Deploying', verifying: 'Verifying', healthy: 'Verified healthy', degraded: 'Degraded', failed: 'CI failed',
    rolled_back: 'Rolled back', superseded: 'Superseded', unverified: 'Unverified', aborted: 'Canary aborted',
  }
  return status ? labels[status] ?? title(status) : '—'
}

export function incidentTone(status: string | null | undefined): HealthTone {
  if (status === 'resolved') return 'healthy'
  if (status === 'verifying' || status === 'mitigating') return 'warning'
  return 'critical'
}

export function incidentLabel(status: string | null | undefined): string {
  const labels: Record<string, string> = {
    open: 'Open', investigating: 'Investigating', awaiting_approval: 'Awaiting approval', mitigating: 'Mitigating', verifying: 'Verifying recovery', resolved: 'Resolved',
  }
  return status ? labels[status] ?? title(status) : '—'
}

export function severityTone(severity: string | null | undefined): HealthTone {
  return severity === 'critical' || severity === 'high' ? 'critical' : severity === 'medium' ? 'warning' : 'neutral'
}

export function actionLabel(action: string | null | undefined, target?: string | null): string {
  const labels: Record<string, string> = { rollback: 'Rollback', restart: 'Restart', scale_up: 'Scale up', pause_deployments: 'Pause deployments', disable_feature: 'Disable feature', investigate_further: 'Investigate further' }
  const label = action ? labels[action] ?? title(action) : '—'
  if (!target) return label
  return action === 'disable_feature' ? `${label} ${target}` : `${label} to ${target}`
}

export function greeting(): string {
  const hour = new Date().getHours()
  return hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening'
}
