import type { ReactNode } from 'react'
import type { HealthTone } from '../lib/format'

interface StatusBadgeProps {
  children: ReactNode
  tone?: HealthTone
  dot?: boolean
  compact?: boolean
}

export function StatusBadge({ children, tone = 'neutral', dot = false, compact = false }: StatusBadgeProps) {
  return (
    <span className={`status-badge tone-${tone}${compact ? ' badge-compact' : ''}`}>
      {dot && <span className="status-dot" aria-hidden="true" />}
      {children}
    </span>
  )
}
