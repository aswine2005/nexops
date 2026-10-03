import { useState } from 'react'
import type { FormEvent } from 'react'
import { Flag, Plus, ToggleLeft, ToggleRight, Trash2 } from 'lucide-react'
import { api, query } from '../api/client'
import type { Application, FeatureFlag } from '../api/types'
import { EmptyState, ErrorNote, Loading } from '../components/Feedback'
import { PageHeading } from '../components/PageHeading'
import { StatusBadge } from '../components/StatusBadge'
import { useResource } from '../hooks/useResource'
import { relative } from '../lib/format'
import { useAuth } from '../state/AuthContext'

export function FlagsPage() {
  const { can } = useAuth()
  const apps = useResource(() => api<Application[]>('/api/applications'), [])
  const [application, setApplication] = useState('')
  const flags = useResource(() => api<FeatureFlag[]>(`/api/flags${query({ application: application || undefined })}`), [application], 10000)
  const [draft, setDraft] = useState({ application: '', key: '', description: '', rollout_percent: 100 })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<number | null>(null)

  async function create(event: FormEvent) {
    event.preventDefault()
    setError(null)
    try {
      await api(`/api/applications/${draft.application}/flags`, { method: 'POST', json: { key: draft.key, description: draft.description || null, rollout_percent: draft.rollout_percent } })
      setDraft({ ...draft, key: '', description: '' })
      await flags.refresh()
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Could not create flag') }
  }

  async function update(flag: FeatureFlag, patch: { enabled?: boolean; rollout_percent?: number }) {
    const reason = patch.enabled === undefined ? 'Rollout changed' : window.prompt(`Reason for turning ${flag.key} ${patch.enabled ? 'on' : 'off'}?`, patch.enabled ? 'Launch' : 'Turn off')
    if (reason === null) return
    setBusy(flag.id)
    setError(null)
    try { await api(`/api/flags/${flag.id}`, { method: 'PATCH', json: { ...patch, reason } }); await flags.refresh() } catch (caught) { setError(caught instanceof Error ? caught.message : 'Update failed') } finally { setBusy(null) }
  }

  async function remove(flag: FeatureFlag) {
    if (!window.confirm(`Delete flag ${flag.key}? The application will treat it as off.`)) return
    try { await api(`/api/flags/${flag.id}`, { method: 'DELETE' }); await flags.refresh() } catch (caught) { setError(caught instanceof Error ? caught.message : 'Delete failed') }
  }

  const rows = flags.data ?? []
  return (
    <div className="work-page">
      <PageHeading eyebrow="RELEASE DELIVERY" title="Feature flags" description="Runtime switches for code paths. Applications poll them from NexOps; turning a flag on is a production change NexOps correlates with incidents, and switching a failing feature off is a remediation it can run on its own." action={<select className="filter-select" value={application} onChange={(event) => setApplication(event.target.value)} aria-label="Application"><option value="">All applications</option>{(apps.data ?? []).map((app) => <option key={app.id} value={app.id}>{app.name}</option>)}</select>} />
      {(error || flags.error) && <ErrorNote message={error ?? flags.error ?? ''} />}
      {can('operator') && <form className="panel form-section" onSubmit={create}>
        <div className="panel-header"><div><h2>New flag</h2><p>Flags start off. The application decides what the flag guards.</p></div><Flag size={16} className="muted-icon" /></div>
        <div className="form-grid">
          <label className="form-field"><span>Application</span><select className="form-input" required value={draft.application} onChange={(event) => setDraft({ ...draft, application: event.target.value })}><option value="">Choose…</option>{(apps.data ?? []).map((app) => <option key={app.id} value={app.id}>{app.name}</option>)}</select></label>
          <label className="form-field"><span>Key</span><input className="form-input mono" required pattern="[a-z0-9][a-z0-9._-]*[a-z0-9]" value={draft.key} onChange={(event) => setDraft({ ...draft, key: event.target.value })} placeholder="fraud-check" /></label>
          <label className="form-field"><span>Rollout % when on</span><input className="form-input" type="number" min={0} max={100} value={draft.rollout_percent} onChange={(event) => setDraft({ ...draft, rollout_percent: Number(event.target.value) })} /></label>
          <label className="form-field form-wide"><span>Description</span><input className="form-input" value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} placeholder="What the flag turns on" /></label>
          <div className="form-actions"><button className="button button-primary"><Plus size={13} /> Create flag</button></div>
        </div>
      </form>}
      <section className="panel catalog-panel">
        <div className="data-toolbar"><div className="toolbar-title"><div><h2>Flags</h2><p>{rows.length} flag(s)</p></div></div></div>
        {flags.loading && !flags.data ? <Loading /> : <div className="wide-table-wrap"><table className="data-table"><thead><tr><th>FLAG</th><th>APPLICATION</th><th>STATE</th><th>ROLLOUT</th><th>LAST CHANGE</th><th>HISTORY</th><th /></tr></thead><tbody>
          {rows.map((flag) => <tr key={flag.id}>
            <td><strong className="plain-strong"><code>{flag.key}</code></strong><small className="cell-subtext">{flag.description ?? ''}</small></td>
            <td>{flag.application_id}</td>
            <td>{can('operator') ? <button className="button button-secondary flag-toggle" disabled={busy === flag.id} onClick={() => void update(flag, { enabled: !flag.enabled })}>{flag.enabled ? <ToggleRight size={15} className="healthy-text" /> : <ToggleLeft size={15} />} {flag.enabled ? 'On' : 'Off'}</button> : <StatusBadge tone={flag.enabled ? 'healthy' : 'neutral'} compact>{flag.enabled ? 'On' : 'Off'}</StatusBadge>}</td>
            <td>{can('operator') ? <select className="filter-select" value={flag.rollout_percent} onChange={(event) => void update(flag, { rollout_percent: Number(event.target.value) })}>{[0, 1, 5, 10, 25, 50, 100].concat([flag.rollout_percent]).filter((value, index, all) => all.indexOf(value) === index).sort((a, b) => a - b).map((value) => <option key={value} value={value}>{value}%</option>)}</select> : `${flag.rollout_percent}%`}</td>
            <td>{flag.updated_by ?? '—'}<small className="cell-subtext">{relative(flag.updated_at)}</small></td>
            <td className="cell-subtext">{flag.history.slice(0, 3).map((change) => `${change.enabled ? 'on' : 'off'} by ${change.actor} ${relative(change.changed_at)}${change.reason ? ` (${change.reason})` : ''}`).join(' · ') || '—'}</td>
            <td>{can('admin') && <button className="icon-button" aria-label="Delete flag" onClick={() => void remove(flag)}><Trash2 size={13} /></button>}</td>
          </tr>)}
          {!rows.length && <tr><td colSpan={7}><EmptyState title="No feature flags" detail="Create a flag, then read it from the application via /api/flags/evaluate." /></td></tr>}
        </tbody></table></div>}
        <div className="table-footer"><span>Applications read <code>/api/flags/evaluate?application=&lt;id&gt;</code> (Bearer NEXOPS_FLAGS_TOKEN when set).</span><span>Changes are audited</span></div>
      </section>
    </div>
  )
}
