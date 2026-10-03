import { useState } from 'react'
import type { FormEvent } from 'react'
import { CheckCircle2, CircleAlert, CircleDashed, Copy, Plus, RefreshCw } from 'lucide-react'
import { api } from '../api/client'
import type { AuditEntry, IntegrationStatus, Role, RuntimeSettings, User } from '../api/types'
import { ErrorNote, Loading } from '../components/Feedback'
import { PageHeading } from '../components/PageHeading'
import { StatusBadge } from '../components/StatusBadge'
import { useResource } from '../hooks/useResource'
import { relative, title } from '../lib/format'
import { useAuth } from '../state/AuthContext'

export function SettingsPage() {
  const { can, user } = useAuth()
  const integrations = useResource(() => api<IntegrationStatus[]>('/api/integrations'), [])
  const settings = useResource(() => api<RuntimeSettings>('/api/settings'), [])
  const users = useResource(() => can('admin') ? api<User[]>('/api/users') : Promise.resolve([] as User[]), [])
  const audit = useResource(() => can('admin') ? api<AuditEntry[]>('/api/audit?limit=50') : Promise.resolve([] as AuditEntry[]), [])
  const [newUser, setNewUser] = useState({ email: '', display_name: '', role: 'operator' as Role, password: '' })
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [testResult, setTestResult] = useState<string | null>(null)

  async function sendTestAlert() {
    setTestResult('Sending…')
    try {
      const result = await api<{ ok: boolean; status: number | null; detail: string }>('/api/integrations/notifications/test', { method: 'POST' })
      setTestResult(result.ok ? 'Test alert delivered.' : `Webhook answered ${result.status ?? 'no response'}: ${result.detail}`)
    } catch (caught) { setTestResult(caught instanceof Error ? caught.message : 'Test failed') }
  }
  const webhook = settings.data?.webhook_url ?? '/api/webhooks/deployments'
  const snippet = `- name: Report deployment to NexOps
  if: always()
  env:
    NEXOPS_URL: \${{ vars.NEXOPS_URL }}
    NEXOPS_WEBHOOK_SECRET: \${{ secrets.NEXOPS_WEBHOOK_SECRET }}
  run: |
    python3 scripts/nexops_notify.py deployment --event finished \\
      --application payment-api --version "v\${{ env.VERSION }}" \\
      --image "\${{ env.IMAGE }}" --ci-status "\${{ job.status }}" \\
      --stage Build=success --stage Tests=success --stage Deploy=\${{ job.status }}`

  async function createUser(event: FormEvent) {
    event.preventDefault()
    setError(null)
    try {
      await api('/api/users', { method: 'POST', json: newUser })
      setNewUser({ email: '', display_name: '', role: 'operator', password: '' })
      await users.refresh()
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Could not create user') }
  }

  async function updateUser(target: User, patch: Partial<User>) {
    setError(null)
    try { await api(`/api/users/${target.id}`, { method: 'PATCH', json: patch }); await users.refresh() } catch (caught) { setError(caught instanceof Error ? caught.message : 'Update failed') }
  }

  return (
    <div className="work-page">
      <PageHeading eyebrow="GOVERNANCE" title="Settings" description="Integration health, monitoring configuration, CI/CD webhooks, and access control. Secrets are configured as environment variables on the API and worker." action={<button className="button button-secondary" onClick={() => void integrations.refresh()}><RefreshCw size={13} /> Re-check integrations</button>} />
      {error && <ErrorNote message={error} />}

      <section className="panel catalog-panel">
        <div className="data-toolbar"><div className="toolbar-title"><div><h2>Integrations</h2><p>Live connectivity checks from the API</p></div></div></div>
        {integrations.loading && !integrations.data ? <Loading label="Checking providers…" /> : <div className="integration-grid">{(integrations.data ?? []).map((item) => <div key={item.key} className={`integration-card ${item.healthy ? 'healthy' : item.configured ? 'critical' : 'neutral'}`}><div className="integration-head">{item.healthy ? <CheckCircle2 size={15} /> : item.configured ? <CircleAlert size={15} /> : <CircleDashed size={15} />}<strong>{item.name}</strong><StatusBadge tone={item.healthy ? 'healthy' : item.configured ? 'critical' : 'neutral'} compact>{item.healthy ? 'Connected' : item.configured ? 'Error' : 'Not configured'}</StatusBadge></div><small>{item.detail}</small></div>)}</div>}
      </section>

      <div className="detail-two-column">
        <section className="panel detail-section"><div className="panel-header"><div><h2>Monitoring & remediation</h2><p>From environment variables</p></div></div>{settings.data ? <div className="metadata-list">
          <Row label="Monitor interval" value={`${settings.data.monitor_interval_seconds}s`} />
          <Row label="Consecutive breaches to open an incident" value={String(settings.data.detection_consecutive_breaches)} />
          <Row label="Deployment correlation window" value={`${settings.data.correlation_window_minutes} min`} />
          <Row label="Post-release verification window" value={`${settings.data.deployment_verification_minutes} min`} />
          <Row label="Healthy samples to verify recovery" value={`${settings.data.recovery_required_healthy_samples} (timeout ${settings.data.recovery_timeout_minutes} min)`} />
          <Row label="Auto-investigate new incidents" value={settings.data.auto_investigate ? 'Yes' : 'No'} />
          <Row label="Remediation execution" value={settings.data.remediation_enabled ? 'Enabled (policy-gated)' : 'Disabled'} />
          <Row label="AI engine" value={settings.data.llm_provider === 'groq' ? `Groq · ${settings.data.llm_model}` : 'Deterministic evidence rules'} />
          <Row label="Prometheus rate window" value={settings.data.prometheus_rate_window} />
          <Row label="Metric retention" value={`${settings.data.metric_retention_days} days`} />
          <Row label="Trace UI" value={settings.data.jaeger_ui_url ?? 'Jaeger not configured'} />
          <Row label="Incident notifications" value={settings.data.notifications_enabled ? 'Webhook configured' : 'Off (NEXOPS_NOTIFY_WEBHOOK_URL)'} />
          {settings.data.notifications_enabled && can('admin') && <div className="metadata-row"><span>Alerts</span><span className="row-actions"><button className="button button-secondary" onClick={() => void sendTestAlert()}>Send test alert</button>{testResult && <small className="settings-hint inline-hint">{testResult}</small>}</span></div>}
        </div> : <Loading />}</section>
        <section className="panel detail-section"><div className="panel-header"><div><h2>CI/CD deployment webhook</h2><p>HMAC-SHA256 signed with NEXOPS_WEBHOOK_SECRET</p></div><button className="icon-button" aria-label="Copy snippet" onClick={() => { void navigator.clipboard.writeText(snippet).then(() => { setCopied(true); window.setTimeout(() => setCopied(false), 1500) }) }}><Copy size={14} /></button></div>
          <div className="metadata-list"><Row label="Endpoint" value={webhook} /><Row label="Signature header" value="X-NexOps-Signature: sha256=<hex>" /><Row label="Deploy gate" value={webhook.replace('/deployments', '/deploy-gate')} /></div>
          <pre className="patch-view code-snippet">{snippet}</pre>
          <small className="settings-hint">{copied ? 'Copied.' : 'See ci/github-actions/deploy-payment-api.yml for a complete pipeline (build, test, scan, push, deploy, notify).'}</small>
        </section>
      </div>

      {can('admin') && <div className="detail-two-column detail-last-row">
        <section className="panel detail-section"><div className="panel-header"><div><h2>Users</h2><p>Viewers read; operators approve and remediate; admins manage policies and users</p></div></div>
          <div className="check-list">{(users.data ?? []).map((item) => <div className="check-row" key={item.id}><div className="check-main"><strong>{item.display_name}{item.id === user?.id ? ' (you)' : ''}</strong><small>{item.email} · last sign-in {relative(item.last_login_at)}</small></div><select className="filter-select" value={item.role} disabled={item.id === user?.id} onChange={(event) => void updateUser(item, { role: event.target.value as Role })}><option value="viewer">Viewer</option><option value="operator">Operator</option><option value="admin">Admin</option></select><button className="button button-secondary" disabled={item.id === user?.id} onClick={() => void updateUser(item, { is_active: !item.is_active })}>{item.is_active ? 'Deactivate' : 'Activate'}</button></div>)}</div>
          <form className="form-grid user-form" onSubmit={createUser}>
            <label className="form-field"><span>Email</span><input className="form-input" type="email" required value={newUser.email} onChange={(event) => setNewUser({ ...newUser, email: event.target.value })} /></label>
            <label className="form-field"><span>Name</span><input className="form-input" required value={newUser.display_name} onChange={(event) => setNewUser({ ...newUser, display_name: event.target.value })} /></label>
            <label className="form-field"><span>Role</span><select className="form-input" value={newUser.role} onChange={(event) => setNewUser({ ...newUser, role: event.target.value as Role })}><option value="viewer">Viewer</option><option value="operator">Operator</option><option value="admin">Admin</option></select></label>
            <label className="form-field"><span>Initial password (10+ chars)</span><input className="form-input" type="password" minLength={10} required value={newUser.password} onChange={(event) => setNewUser({ ...newUser, password: event.target.value })} /></label>
            <div className="form-actions"><button className="button button-primary"><Plus size={13} /> Add user</button></div>
          </form>
        </section>
        <section className="panel detail-section"><div className="panel-header"><div><h2>Audit log</h2><p>Approvals, policy and configuration changes</p></div></div>
          <div className="check-list">{(audit.data ?? []).map((entry) => <div className="check-row" key={entry.id}><div className="check-main"><strong>{entry.action}</strong><small>{entry.actor} · {entry.target_type} {entry.target_id} · {relative(entry.created_at)}</small></div></div>)}{!audit.data?.length && <div className="empty-state"><span>No audit entries yet</span></div>}</div>
        </section>
      </div>}
      {!can('admin') && <p className="page-disclaimer">Signed in as {user?.email} ({title(user?.role)}). Ask an administrator to change your role.</p>}
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return <div className="metadata-row"><span>{label}</span><strong>{value}</strong></div>
}
