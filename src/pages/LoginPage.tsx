import { useState } from 'react'
import type { FormEvent } from 'react'
import { ArrowRight, LoaderCircle, ShieldCheck } from 'lucide-react'
import { useAuth } from '../state/AuthContext'

export function LoginPage() {
  const { login } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(email, password)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Sign-in failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-page">
      <form className="login-card panel" onSubmit={submit}>
        <div className="brand-row login-brand"><div className="brand-mark" aria-hidden="true"><span /><span /><span /><span /></div><div className="brand-copy"><span className="brand-name">nex<span>ops</span></span><span className="brand-tagline">RELIABILITY PLATFORM</span></div></div>
        <h1>Sign in</h1>
        <p>CI/CD tells you a release passed its tests. NexOps tells you whether production is actually healthy.</p>
        <label className="form-field"><span>Email</span><input className="form-input" type="email" autoComplete="username" value={email} onChange={(event) => setEmail(event.target.value)} required autoFocus /></label>
        <label className="form-field"><span>Password</span><input className="form-input" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required /></label>
        {error && <div className="form-error" role="alert">{error}</div>}
        <button className="button button-primary login-submit" disabled={busy}>{busy ? <LoaderCircle size={14} className="spin" /> : <ShieldCheck size={14} />} {busy ? 'Signing in…' : 'Sign in'} <ArrowRight size={14} /></button>
        <small className="login-hint">The first administrator comes from NEXOPS_ADMIN_EMAIL / NEXOPS_ADMIN_PASSWORD, or <code>python -m app.cli create-user</code>.</small>
      </form>
    </div>
  )
}
