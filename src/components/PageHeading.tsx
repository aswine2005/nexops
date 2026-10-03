import type { ReactNode } from 'react'

interface PageHeadingProps {
  eyebrow: string
  title: string
  description: string
  action?: ReactNode
}

export function PageHeading({ eyebrow, title, description, action }: PageHeadingProps) {
  return (
    <div className="page-intro work-page-heading">
      <div><div className="page-overline"><span className="overline-pulse" />{eyebrow}</div><h1>{title}</h1><p>{description}</p></div>
      {action && <div className="intro-actions">{action}</div>}
    </div>
  )
}
