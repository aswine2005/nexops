import { useState } from 'react'
import type { FormEvent } from 'react'
import { Pencil, Plus, ShieldAlert, ShieldCheck, ShieldX, Trash2, X } from 'lucide-react'
import { api } from '../api/client'
import type { Application, Policy } from '../api/types'
import { ErrorNote, Loading } from '../components/Feedback'
import { PageHeading } from '../components/PageHeading'
import { StatusBadge } from '../components/StatusBadge'
import { useResource } from '../hooks/useResource'
import { actionLabel, relative, title } from '../lib/format'
import { useAuth } from '../state/AuthContext'

type Draft = Omit<Policy, 'created_at' | 'updated_at'> & { isNew: boolean }
const NEW: Draft = { id: '', name: '', description: '', action: 'rollback', environment: 'production', application_id: null, mode: 'approval_required', enabled: true, conditions: {}, isNew: true }
const modeTone = { automatic: 'healthy', approval_required: 'warning', forbidden: 'critical' } as const
const modeIcon = { automatic: ShieldCheck, approval_required: ShieldAlert, forbidden: ShieldX }

export function PoliciesPage() {
  const { can } = useAuth()
  const policies = useResource(() => api<Policy[]>('/api/policies'), [])
  const apps = useResource(() => api<Application[]>('/api/applications'), [])
  const [draft, setDraft] = useState<Draft | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  async function save(event: FormEvent) {
    event.preventDefault()
    if (!draft) return
    setSaving(true)
    setError(null)
    const { isNew, ...body } = draft
    const conditions = Object.fromEntries(Object.entries(body.conditions).filter(([, value]) => value !== null && value !== undefined && !(typeof value === 'number' && Number.isNaN(value))))
    try {
      await api(isNew ? '/api/policies' : `/api/policies/${draft.id}`, { method: isNew ? 'POST' : 'PUT', json: { ...body, id: isNew ? (body.id || undefined) : undefined, conditions } })
      setDraft(null)
      await policies.refresh()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  async function remove(policy: Policy) {
    if (!window.confirm(`Delete policy "${policy.name}"?`)) return
    try { await api(`/api/policies/${policy.id}`, { method: 'DELETE' }); await policies.refresh() } catch (caught) { setError(caught instanceof Error ? caught.message : 'Delete failed') }
  }

  const setCondition = (key: string, value: number | boolean | undefined) => setDraft((current) => current && ({ ...current, conditions: { ...current.conditions, [key]: value as number | boolean } }))
  const rows = policies.data ?? []

  return (
    <div className="work-page">
      <PageHeading eyebrow="GOVERNANCE" title="Policies" description="Policies decide whether a remediation proposed by the agents may run automatically, needs a human approval, or is never permitted." action={can('admin') ? <button className="button button-primary" onClick={() => setDraft({ ...NEW })}><Plus size={14} /> New policy</button> : <span className="snapshot-tag"><i /> Admins can edit</span>} />
      {(error || policies.error) && <ErrorNote message={error ?? policies.error ?? ''} />}
      <div className="summary-strip three-summary">
        {(['automatic', 'approval_required', 'forbidden'] as const).map((mode) => { const Icon = modeIcon[mode]; return <div key={mode} className={`summary-stat summary-${mode === 'automatic' ? 'green' : mode === 'forbidden' ? 'red' : 'amber'}`}><span className="summary-icon"><Icon size={16} /></span><div className="summary-copy"><span>{title(mode)}</span><strong>{String(rows.filter((policy) => policy.mode === mode && policy.enabled).length).padStart(2, '0')}</strong><small>{mode === 'automatic' ? 'Run without approval' : mode === 'forbidden' ? 'Never executed' : 'Need an operator'}</small></div></div> })}
      </div>

      {draft && <form className="panel form-section policy-editor" onSubmit={save}>
        <div className="panel-header"><div><h2>{draft.isNew ? 'New policy' : `Edit ${draft.name}`}</h2><p>The most specific matching policy wins (application → environment → any). Forbidden always wins at the same level.</p></div><button type="button" className="icon-button" onClick={() => setDraft(null)} aria-label="Close"><X size={15} /></button></div>
        <div className="form-grid">
          <label className="form-field"><span>Name</span><input className="form-input" required value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></label>
          <label className="form-field"><span>Action</span><select className="form-input" value={draft.action} onChange={(event) => setDraft({ ...draft, action: event.target.value as Policy['action'] })}><option value="rollback">Rollback</option><option value="restart">Restart</option><option value="scale_up">Scale up</option><option value="pause_deployments">Pause deployments</option><option value="disable_feature">Disable feature flag</option><option value="database_modification">Database modification</option></select></label>
          <label className="form-field"><span>Mode</span><select className="form-input" value={draft.mode} onChange={(event) => setDraft({ ...draft, mode: event.target.value as Policy['mode'] })}><option value="automatic">Automatic</option><option value="approval_required">Human approval required</option><option value="forbidden">Not permitted</option></select></label>
          <label className="form-field"><span>Environment</span><select className="form-input" value={draft.environment} onChange={(event) => setDraft({ ...draft, environment: event.target.value as Policy['environment'] })}><option value="production">Production</option><option value="staging">Staging (also development)</option><option value="development">Development (also staging)</option><option value="*">Any environment</option></select></label>
          <label className="form-field"><span>Application</span><select className="form-input" value={draft.application_id ?? ''} onChange={(event) => setDraft({ ...draft, application_id: event.target.value || null })}><option value="">All applications</option>{(apps.data ?? []).map((app) => <option key={app.id} value={app.id}>{app.name}</option>)}</select></label>
          <label className="form-field"><span>Minimum confidence (0–1)</span><input className="form-input" type="number" min={0} max={1} step={0.05} value={(draft.conditions.min_confidence as number | undefined) ?? ''} onChange={(event) => setCondition('min_confidence', event.target.value === '' ? undefined : Number(event.target.value))} placeholder="automatic only" /></label>
          <label className="form-field"><span>Max automatic runs / hour</span><input className="form-input" type="number" min={1} value={(draft.conditions.max_executions_per_hour as number | undefined) ?? ''} onChange={(event) => setCondition('max_executions_per_hour', event.target.value === '' ? undefined : Number(event.target.value))} /></label>
          {draft.action === 'scale_up' && <label className="form-field"><span>Max replicas</span><input className="form-input" type="number" min={1} value={(draft.conditions.max_replicas as number | undefined) ?? ''} onChange={(event) => setCondition('max_replicas', event.target.value === '' ? undefined : Number(event.target.value))} /></label>}
          <label className="form-field form-wide"><span>Description</span><input className="form-input" value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} /></label>
          {draft.action === 'rollback' && <label className="form-check"><input type="checkbox" checked={Boolean(draft.conditions.require_previous_version)} onChange={(event) => setCondition('require_previous_version', event.target.checked)} /> Require a recorded previous version</label>}
          <label className="form-check"><input type="checkbox" checked={draft.enabled} onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} /> Enabled</label>
        </div>
        <div className="form-actions"><button type="button" className="button button-secondary" onClick={() => setDraft(null)}>Cancel</button><button className="button button-primary" disabled={saving}>{saving ? 'Saving…' : 'Save policy'}</button></div>
      </form>}

      <section className="panel catalog-panel">
        <div className="data-toolbar"><div className="toolbar-title"><div><h2>Remediation policies</h2><p>{rows.length} policies</p></div></div></div>
        {policies.loading && !policies.data ? <Loading /> : <div className="wide-table-wrap"><table className="data-table"><thead><tr><th>POLICY</th><th>ACTION</th><th>SCOPE</th><th>MODE</th><th>CONDITIONS</th><th>UPDATED</th><th /></tr></thead><tbody>
          {rows.map((policy) => <tr key={policy.id} className={policy.enabled ? '' : 'row-disabled'}><td><strong className="plain-strong">{policy.name}</strong><small className="cell-subtext">{policy.description}</small></td><td>{actionLabel(policy.action)}</td><td>{policy.environment === '*' ? 'Any environment' : title(policy.environment)}{policy.application_id ? ` · ${policy.application_id}` : ''}</td><td><StatusBadge tone={modeTone[policy.mode]} dot compact>{policy.enabled ? title(policy.mode) : 'Disabled'}</StatusBadge></td><td className="cell-subtext">{Object.entries(policy.conditions).map(([key, value]) => `${key.replace(/_/g, ' ')}: ${value}`).join(' · ') || '—'}</td><td>{relative(policy.updated_at)}</td><td>{can('admin') && <span className="row-actions"><button className="icon-button" aria-label="Edit policy" onClick={() => setDraft({ ...policy, isNew: false })}><Pencil size={13} /></button><button className="icon-button" aria-label="Delete policy" onClick={() => void remove(policy)}><Trash2 size={13} /></button></span>}</td></tr>)}
        </tbody></table></div>}
        <div className="table-footer"><span>No matching policy means human approval is required.</span><span>Database modification can only be forbidden.</span></div>
      </section>
    </div>
  )
}
