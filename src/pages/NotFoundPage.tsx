import { ArrowLeft, Compass } from 'lucide-react'
import { Link } from 'react-router-dom'

export function NotFoundPage() {
  return <div className="placeholder-page"><div className="placeholder-card"><span className="placeholder-icon"><Compass size={21} /></span><h1>Page not found</h1><p>This page is not part of the NexOps workspace.</p><Link className="text-link" to="/"><ArrowLeft size={14} /> Back to dashboard</Link></div></div>
}
