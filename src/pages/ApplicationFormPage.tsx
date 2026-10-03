import { useEffect, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { ArrowLeft, Plus, Save, Trash2 } from 'lucide-react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { ApplicationConfig, ApplicationDetail } from '../api/types'
import { ErrorNote, Loading } from '../components/Feedback'
import { PageHeading } from '../components/PageHeading'
import { useAuth } from '../state/AuthContext'

const THRESHOLDS: Array<{ key: string; label: string; placeholder: string }> = [
  { key: 'error_rate_percent', label: 'Error rate %', placeholder: '5' },
  { key: 'p99_latency_ms', label: 'P99 latency ms', placeholder: '1000' },
  { key: 'cpu_percent', label: 'CPU %', placeholder: '85' },
  { key: 'memory_percent', label: 'Memory %', placeholder: '85' },
  { key: 'restart_count', label: 'Restarts', placeholder: '3' },
  { key: 'db_error_count', label: 'DB errors / window', placeholder: '5' },
]
const METRIC_KEYS = ['request_rate', 'error_rate_percent', 'p50_latency_ms', 'p95_latency_ms', 'p99_latency_ms', 'cpu_percent', 'memory_percent', 'business_success_rate']

const EMPTY: ApplicationConfig = {
  id: '', name: '', description: '', owner: '', environment: 'production', repository: '', runtime: 'docker', monitoring_enabled: true,
  prometheus_selector: '', metric_queries: {}, loki_selector: '', cloudwatch_log_group: '', tracing_service: '', health_checks: [], docker_container: '',
  k8s_namespace: '', k8s_deployment: '', k8s_container: '', ec2_instance_id: '', image_repository: '', rollback_strategy: 'none',
  github_workflow: '', github_ref: 'main', thresholds: {}, dependencies: [],
}

function clean(form: ApplicationConfig, isNew: boolean) {
  const blank = (value: string | null) => (value && value.trim() ? value.trim() : null)
  return {
    ...(isNew && form.id.trim() ? { id: form.id.trim() } : {}),
    name: form.name.trim(), description: blank(form.description), owner: blank(form.owner), environment: form.environment,
    repository: blank(form.repository), runtime: form.runtime, monitoring_enabled: form.monitoring_enabled,
    prometheus_selector: blank(form.prometheus_selector), metric_queries: Object.fromEntries(Object.entries(form.metric_queries).filter(([, value]) => value.trim())),
    loki_selector: blank(form.loki_selector), cloudwatch_log_group: blank(form.cloudwatch_log_group), tracing_service: blank(form.tracing_service),
    health_checks: form.health_checks.filter((check) => check.url.trim()),
    docker_container: blank(form.docker_container), k8s_namespace: blank(form.k8s_namespace), k8s_deployment: blank(form.k8s_deployment), k8s_container: blank(form.k8s_container),
    ec2_instance_id: blank(form.ec2_instance_id), image_repository: blank(form.image_repository), rollback_strategy: form.rollback_strategy,
    github_workflow: blank(form.github_workflow), github_ref: blank(form.github_ref),
    thresholds: Object.fromEntries(Object.entries(form.thresholds).filter(([, value]) => value !== null && !Number.isNaN(value))),
    dependencies: form.dependencies.map((item) => item.trim()).filter(Boolean),
  }
}

export function ApplicationFormPage() {
  const { slug } = useParams()
  const isNew = !slug
  const navigate = useNavigate()
  const { can } = useAuth()
  const [form, setForm] = useState<ApplicationConfig>(EMPTY)
  const [loading, setLoading] = useState(!isNew)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (isNew) return
    api<ApplicationDetail>(`/api/applications/${encodeURIComponent(slug!)}`).then((app) => {
      const config = Object.fromEntries(Object.keys(EMPTY).map((key) => [key, app[key as keyof ApplicationConfig] ?? EMPTY[key as keyof ApplicationConfig]])) as unknown as ApplicationConfig
      setForm(config)
    }).catch((caught: Error) => setError(caught.message)).finally(() => setLoading(false))
  }, [isNew, slug])

  const set = <K extends keyof ApplicationConfig>(key: K, value: ApplicationConfig[K]) => setForm((current) => ({ ...current, [key]: value }))

  async function submit(event: FormEvent) {
    event.preventDefault()
    setSaving(true)
    setError(null)
    try {
      const body = clean(form, isNew)
      const saved = await api<ApplicationDetail>(isNew ? '/api/applications' : `/api/applications/${slug}`, { method: isNew ? 'POST' : 'PUT', json: body })
      navigate(`/applications/${saved.id}`)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  async function remove() {
    if (!slug || !window.confirm(`Delete ${form.name}? Its deployments, incidents and telemetry samples are deleted too.`)) return
    try {
      await api(`/api/applications/${slug}`, { method: 'DELETE' })
      navigate('/applications')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Delete failed')
    }
  }

  if (loading) return <div className="work-page"><Loading /></div>
  if (!can('operator')) return <div className="work-page"><ErrorNote message="Registering applications requires the operator role." /></div>

  return (
    <div className="work-page">
      <div className="detail-back-row"><Link to={isNew ? '/applications' : `/applications/${slug}`}><ArrowLeft size={14} /> {isNew ? 'All applications' : form.name}</Link></div>
      <PageHeading eyebrow="SERVICE CATALOG" title={isNew ? 'Register application' : `Edit ${form.name}`} description="Tell NexOps where this service's metrics, logs and workloads live, and how it may be rolled back." />
      {error && <ErrorNote message={error} />}
      <form className="app-form" onSubmit={submit}>
        <FormSection title="Service" description="Identity and ownership.">
          <Field label="Name"><input className="form-input" required value={form.name} onChange={(event) => set('name', event.target.value)} placeholder="Payment API" /></Field>
          <Field label="ID (slug)" hint={isNew ? 'Used in webhooks and URLs. Defaults from the name.' : 'Cannot be changed.'}><input className="form-input" disabled={!isNew} value={form.id} onChange={(event) => set('id', event.target.value)} placeholder="payment-api" pattern="[a-z0-9][a-z0-9-]*[a-z0-9]" /></Field>
          <Field label="Environment"><select className="form-input" value={form.environment} onChange={(event) => set('environment', event.target.value as ApplicationConfig['environment'])}><option value="production">Production</option><option value="staging">Staging</option><option value="development">Development</option></select></Field>
          <Field label="Owner"><input className="form-input" value={form.owner ?? ''} onChange={(event) => set('owner', event.target.value)} placeholder="Payments Platform" /></Field>
          <Field label="Repository" hint="owner/repo on GitHub"><input className="form-input" value={form.repository ?? ''} onChange={(event) => set('repository', event.target.value)} placeholder="acme/payment-api" /></Field>
          <Field label="Description" wide><input className="form-input" value={form.description ?? ''} onChange={(event) => set('description', event.target.value)} /></Field>
          <Field label="Dependencies" hint="Comma separated" wide><input className="form-input" value={form.dependencies.join(', ')} onChange={(event) => set('dependencies', event.target.value.split(','))} placeholder="PostgreSQL, Redis" /></Field>
          <label className="form-check"><input type="checkbox" checked={form.monitoring_enabled} onChange={(event) => set('monitoring_enabled', event.target.checked)} /> Monitoring enabled</label>
        </FormSection>

        <FormSection title="Runtime" description="Where the workload runs. NexOps reads replica health from here and executes rollbacks against it.">
          <Field label="Runtime"><select className="form-input" value={form.runtime} onChange={(event) => set('runtime', event.target.value as ApplicationConfig['runtime'])}><option value="docker">Docker (local / EC2)</option><option value="kubernetes">Kubernetes / EKS</option><option value="ec2">EC2 (CloudWatch only)</option><option value="external">External</option></select></Field>
          {form.runtime === 'docker' && <Field label="Container name or compose service"><input className="form-input" value={form.docker_container ?? ''} onChange={(event) => set('docker_container', event.target.value)} placeholder="payment-api" /></Field>}
          {form.runtime === 'kubernetes' && <>
            <Field label="Namespace"><input className="form-input" value={form.k8s_namespace ?? ''} onChange={(event) => set('k8s_namespace', event.target.value)} placeholder="payments" /></Field>
            <Field label="Deployment"><input className="form-input" value={form.k8s_deployment ?? ''} onChange={(event) => set('k8s_deployment', event.target.value)} placeholder="payment-api" /></Field>
            <Field label="Container (optional)"><input className="form-input" value={form.k8s_container ?? ''} onChange={(event) => set('k8s_container', event.target.value)} /></Field>
          </>}
          <Field label="EC2 instance ID (optional)" hint="CloudWatch CPUUtilization fallback"><input className="form-input" value={form.ec2_instance_id ?? ''} onChange={(event) => set('ec2_instance_id', event.target.value)} placeholder="i-0abc123" /></Field>
          <Field label="Image repository" hint="Rollback image = repository:previous-version"><input className="form-input" value={form.image_repository ?? ''} onChange={(event) => set('image_repository', event.target.value)} placeholder="123456789012.dkr.ecr.us-east-1.amazonaws.com/payment-api" /></Field>
        </FormSection>

        <FormSection title="Telemetry" description="Golden signals come from Prometheus; logs from Loki, CloudWatch, or the runtime.">
          <Field label="Prometheus label selector" hint='e.g. job="payment-api" (without braces)' wide><input className="form-input mono" value={form.prometheus_selector ?? ''} onChange={(event) => set('prometheus_selector', event.target.value)} placeholder='job="payment-api"' /></Field>
          <Field label="Loki stream selector"><input className="form-input mono" value={form.loki_selector ?? ''} onChange={(event) => set('loki_selector', event.target.value)} placeholder='{container="payment-api"}' /></Field>
          <Field label="CloudWatch log group"><input className="form-input mono" value={form.cloudwatch_log_group ?? ''} onChange={(event) => set('cloudwatch_log_group', event.target.value)} placeholder="/nexops/payment-api" /></Field>
          <Field label="Jaeger service name" hint="OpenTelemetry service.name; enables trace evidence"><input className="form-input mono" value={form.tracing_service ?? ''} onChange={(event) => set('tracing_service', event.target.value)} placeholder="payment-api" /></Field>
          <div className="form-field form-wide"><span>Health checks</span>
            {form.health_checks.map((check, index) => <div className="form-row" key={index}>
              <input className="form-input" value={check.name} onChange={(event) => set('health_checks', form.health_checks.map((item, i) => i === index ? { ...item, name: event.target.value } : item))} placeholder="readiness" />
              <input className="form-input mono" value={check.url} onChange={(event) => set('health_checks', form.health_checks.map((item, i) => i === index ? { ...item, url: event.target.value } : item))} placeholder="http://payment-api:8000/ready" />
              <button type="button" className="icon-button" aria-label="Remove health check" onClick={() => set('health_checks', form.health_checks.filter((_, i) => i !== index))}><Trash2 size={14} /></button>
            </div>)}
            <button type="button" className="button button-secondary form-add" onClick={() => set('health_checks', [...form.health_checks, { name: '', url: '' }])}><Plus size={13} /> Add health check</button>
          </div>
          <details className="form-wide form-details"><summary>Custom PromQL (optional)</summary><small>Override any golden-signal query. Use <code>$selector</code> and <code>$window</code> placeholders.</small>
            {METRIC_KEYS.map((key) => <Field key={key} label={key} wide><input className="form-input mono" value={form.metric_queries[key] ?? ''} onChange={(event) => set('metric_queries', { ...form.metric_queries, [key]: event.target.value })} placeholder="default query" /></Field>)}
          </details>
        </FormSection>

        <FormSection title="Remediation" description="How NexOps rolls this service back once a policy allows it.">
          <Field label="Rollback strategy"><select className="form-input" value={form.rollback_strategy} onChange={(event) => set('rollback_strategy', event.target.value as ApplicationConfig['rollback_strategy'])}><option value="none">None (recommend only)</option><option value="docker">Docker: recreate container with previous image</option><option value="kubernetes">Kubernetes: set previous image on the deployment</option><option value="github_actions">GitHub Actions: dispatch the deploy workflow</option></select></Field>
          {form.rollback_strategy === 'github_actions' && <>
            <Field label="Workflow file"><input className="form-input mono" value={form.github_workflow ?? ''} onChange={(event) => set('github_workflow', event.target.value)} placeholder="deploy-payment-api.yml" /></Field>
            <Field label="Ref"><input className="form-input mono" value={form.github_ref ?? ''} onChange={(event) => set('github_ref', event.target.value)} placeholder="main" /></Field>
          </>}
        </FormSection>

        <FormSection title="Detection thresholds" description="Leave blank to use the platform defaults.">
          {THRESHOLDS.map((item) => <Field key={item.key} label={item.label}><input className="form-input" type="number" step="any" value={form.thresholds[item.key] ?? ''} onChange={(event) => set('thresholds', { ...form.thresholds, [item.key]: event.target.value === '' ? (undefined as unknown as number) : Number(event.target.value) })} placeholder={item.placeholder} /></Field>)}
        </FormSection>

        <div className="form-actions">
          {!isNew && can('admin') && <button type="button" className="button button-danger" onClick={() => void remove()}><Trash2 size={13} /> Delete application</button>}
          <Link className="button button-secondary" to={isNew ? '/applications' : `/applications/${slug}`}>Cancel</Link>
          <button className="button button-primary" disabled={saving}><Save size={13} /> {saving ? 'Saving…' : isNew ? 'Register application' : 'Save changes'}</button>
        </div>
      </form>
    </div>
  )
}

function FormSection({ title, description, children }: { title: string; description: string; children: ReactNode }) {
  return <section className="panel form-section"><div className="panel-header"><div><h2>{title}</h2><p>{description}</p></div></div><div className="form-grid">{children}</div></section>
}

function Field({ label, hint, wide, children }: { label: string; hint?: string; wide?: boolean; children: ReactNode }) {
  return <label className={`form-field${wide ? ' form-wide' : ''}`}><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>
}
