import { CircleAlert, LoaderCircle } from 'lucide-react'
import type { ReactNode } from 'react'

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return <div className="feedback-block"><LoaderCircle size={16} className="spin" /><span>{label}</span></div>
}

export function ErrorNote({ message, action }: { message: string; action?: ReactNode }) {
  return <div className="feedback-block feedback-error" role="alert"><CircleAlert size={15} /><span>{message}</span>{action}</div>
}

export function EmptyState({ title, detail, action }: { title: string; detail: string; action?: ReactNode }) {
  return <div className="empty-state"><span>{title}</span><small>{detail}</small>{action && <div className="empty-action">{action}</div>}</div>
}
