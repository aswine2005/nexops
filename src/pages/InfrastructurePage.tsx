import { Boxes, Cloud, Container, Database, Server } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api, query } from '../api/client'
import type { InfrastructureResponse } from '../api/types'
import { EmptyState, ErrorNote, Loading } from '../components/Feedback'
import { PageHeading } from '../components/PageHeading'
import { RolloutStrip } from '../components/RolloutStrip'
import { StatusBadge } from '../components/StatusBadge'
import { useResource } from '../hooks/useResource'
import { pct, relative, title } from '../lib/format'
import { useEnvironment } from '../state/EnvironmentContext'

interface Ec2Instance { id: string; name: string; type: string; state: string; zone: string; private_ip: string | null; public_ip: string | null; launched_at: string | null }
interface EcrRepository { name: string; uri: string; images: Array<{ tags: string[]; pushed_at: string | null; size_mb: number }> }
interface LogGroup { name: string; stored_mb: number; retention_days: number | null }
interface KubeSummary { nodes: Array<{ name: string; ready: boolean; instance_type: string | null; zone: string | null; kubelet: string | null }>; rollouts?: Array<{ namespace: string; name: string; phase: string | null; message: string | null }> | null }
interface DockerInfo { server_version: string; containers: number; running: number; cpus: number; memory_gb: number; os: string }

export function InfrastructurePage() {
  const { apiEnvironment } = useEnvironment()
  const { data, error, loading } = useResource(() => api<InfrastructureResponse>(`/api/infrastructure${query({ environment: apiEnvironment })}`), [apiEnvironment], 30000)
  if (loading && !data) return <div className="work-page"><Loading label="Querying runtimes and AWS…" /></div>
  if (!data) return <div className="work-page"><ErrorNote message={error ?? 'Infrastructure unavailable'} /></div>
  const providers = data.providers
  const ec2 = providers.ec2?.data as Ec2Instance[] | undefined
  const ecr = providers.ecr?.data as EcrRepository[] | undefined
  const logGroups = providers.cloudwatch_log_groups?.data as LogGroup[] | undefined
  const kube = providers.kubernetes?.data as KubeSummary | undefined
  const docker = providers.docker?.data as DockerInfo | undefined
  const failures = Object.entries(providers).filter(([, value]) => !value.available)

  return (
    <div className="work-page">
      <PageHeading eyebrow="OBSERVABILITY · INFRASTRUCTURE" title="Infrastructure" description="Workloads behind each monitored service, plus Docker hosts, Kubernetes clusters, EC2, ECR and CloudWatch when those integrations are enabled." action={<span className="snapshot-tag"><i /> {data.aws_region ? `AWS ${data.aws_region}` : 'AWS not connected'}</span>} />
      {failures.map(([name, value]) => <ErrorNote key={name} message={`${title(name)}: ${value.error}`} />)}
      <div className="summary-strip four-summary">
        <Stat icon={<Boxes size={16} />} label="Workloads" value={String(data.workloads.length)} note={`${data.workloads.reduce((sum, workload) => sum + (workload.healthy_replicas ?? 0), 0)} ready replicas`} tone="blue" />
        <Stat icon={<Container size={16} />} label="Docker" value={docker ? `${docker.running}/${docker.containers}` : '—'} note={docker ? `Engine ${docker.server_version} · ${docker.cpus} CPU · ${docker.memory_gb} GB` : 'DOCKER_ENABLED=false'} tone="green" />
        <Stat icon={<Server size={16} />} label="Kubernetes nodes" value={kube ? `${kube.nodes.filter((node) => node.ready).length}/${kube.nodes.length}` : '—'} note={kube ? 'Ready nodes' : 'KUBERNETES_ENABLED=false'} tone="violet" />
        <Stat icon={<Cloud size={16} />} label="EC2 instances" value={ec2 ? String(ec2.filter((instance) => instance.state === 'running').length) : '—'} note={ec2 ? `${ec2.length} total in ${data.aws_region}` : 'AWS_INTEGRATION_ENABLED=false'} tone="amber" />
      </div>

      <section className="panel catalog-panel">
        <div className="data-toolbar"><div className="toolbar-title"><div><h2>Service workloads</h2><p>Replica state from the latest monitor sample</p></div></div></div>
        <div className="wide-table-wrap"><table className="data-table"><thead><tr><th>APPLICATION</th><th>RUNTIME</th><th>TARGET</th><th>VERSION / IMAGE</th><th>READY</th><th>RESTARTS</th><th>REPLICAS</th><th>OBSERVED</th></tr></thead><tbody>
          {data.workloads.map((workload) => <tr key={workload.application_id}><td><Link className="table-primary-link" to={`/applications/${workload.application_id}`}><span><strong>{workload.application_name}</strong><small>{title(workload.environment)}</small></span></Link></td><td>{title(workload.runtime)}</td><td><code>{workload.target ?? '—'}</code></td><td><code>{workload.version ?? '—'}</code><small className="cell-subtext">{workload.image ?? ''}</small></td><td>{workload.total_replicas ? <StatusBadge tone={workload.healthy_replicas === workload.total_replicas ? 'healthy' : 'critical'} compact>{workload.healthy_replicas}/{workload.total_replicas}</StatusBadge> : '—'}</td><td>{workload.restarts ?? '—'}</td><td className="replica-cell">{workload.replicas.map((replica) => <span key={replica.name} className={`replica-chip ${replica.ready ? 'healthy' : 'critical'}${replica.track === 'canary' ? ' replica-canary' : ''}`} title={`${replica.name} · ${replica.reason ?? replica.status} · CPU ${pct(replica.cpu_percent, 0)} · mem ${pct(replica.memory_percent, 0)}`}>{replica.track === 'canary' ? 'canary · ' : ''}{replica.name.slice(-12)} · {replica.reason ?? (replica.ready ? 'ready' : replica.status)}</span>)}{workload.rollout && <RolloutStrip rollout={workload.rollout} />}</td><td>{relative(workload.observed_at)}</td></tr>)}
          {!data.workloads.length && <tr><td colSpan={8}><EmptyState title="No workloads" detail="Register applications with a Docker or Kubernetes runtime." /></td></tr>}
        </tbody></table></div>
      </section>

      {data.workloads.some((workload) => workload.events.length) && <section className="panel catalog-panel"><div className="data-toolbar"><div className="toolbar-title"><div><h2>Kubernetes events</h2><p>Recent events for monitored deployments</p></div></div></div><div className="wide-table-wrap"><table className="data-table"><thead><tr><th>TIME</th><th>TYPE</th><th>REASON</th><th>OBJECT</th><th>MESSAGE</th></tr></thead><tbody>{data.workloads.flatMap((workload) => workload.events).slice(0, 40).map((event, index) => <tr key={index}><td>{relative(event.time)}</td><td><StatusBadge tone={event.type === 'Warning' ? 'warning' : 'neutral'} compact>{event.type}</StatusBadge></td><td>{event.reason}</td><td><code>{event.object}</code></td><td>{event.message}</td></tr>)}</tbody></table></div></section>}

      <div className="detail-two-column">
        <section className="panel detail-section"><div className="panel-header"><div><h2>EC2 instances</h2><p>{data.aws_region ?? 'AWS integration disabled'}</p></div><Cloud size={16} className="muted-icon" /></div>{ec2?.length ? <div className="check-list">{ec2.map((instance) => <div className="check-row" key={instance.id}><span className={`pod-status-icon ${instance.state === 'running' ? 'healthy' : 'critical'}`}><i /></span><div className="check-main"><strong>{instance.name}</strong><small>{instance.id} · {instance.type} · {instance.zone} · {instance.private_ip ?? ''}</small></div><StatusBadge tone={instance.state === 'running' ? 'healthy' : 'warning'} compact>{instance.state}</StatusBadge></div>)}</div> : <EmptyState title="No EC2 data" detail="Enable AWS_INTEGRATION_ENABLED with an IAM role that can DescribeInstances." />}</section>
        <section className="panel detail-section"><div className="panel-header"><div><h2>ECR repositories</h2><p>Latest pushed images</p></div><Database size={16} className="muted-icon" /></div>{ecr?.length ? <div className="check-list">{ecr.map((repository) => <div className="check-row" key={repository.name}><span className="file-change-icon"><Container size={14} /></span><div className="check-main"><strong>{repository.name}</strong><small>{repository.images.slice(0, 3).map((image) => `${image.tags.join(',') || 'untagged'} (${relative(image.pushed_at)})`).join(' · ') || 'no images'}</small></div></div>)}</div> : <EmptyState title="No ECR data" detail="Enable the AWS integration to list repositories and images." />}</section>
      </div>
      <div className="detail-two-column detail-last-row">
        <section className="panel detail-section"><div className="panel-header"><div><h2>Kubernetes nodes</h2><p>Cluster capacity</p></div><Server size={16} className="muted-icon" /></div>{kube?.nodes.length ? <div className="check-list">{kube.nodes.map((node) => <div className="check-row" key={node.name}><span className={`pod-status-icon ${node.ready ? 'healthy' : 'critical'}`}><i /></span><div className="check-main"><strong>{node.name}</strong><small>{node.instance_type ?? '—'} · {node.zone ?? '—'} · kubelet {node.kubelet}</small></div><StatusBadge tone={node.ready ? 'healthy' : 'critical'} compact>{node.ready ? 'Ready' : 'NotReady'}</StatusBadge></div>)}</div> : <EmptyState title="No cluster connected" detail="Set KUBERNETES_ENABLED=true (in-cluster on EKS, or a kubeconfig)." />}{kube?.rollouts?.length ? <div className="check-list rollout-list">{kube.rollouts.map((item) => <div className="check-row" key={`${item.namespace}/${item.name}`}><span className={`pod-status-icon ${item.phase === 'Healthy' ? 'healthy' : item.phase === 'Degraded' ? 'critical' : 'warning'}`}><i /></span><div className="check-main"><strong>Rollout {item.namespace}/{item.name}</strong><small>{item.message ?? 'Argo Rollouts'}</small></div><StatusBadge tone={item.phase === 'Healthy' ? 'healthy' : item.phase === 'Degraded' ? 'critical' : 'warning'} compact>{item.phase ?? 'Unknown'}</StatusBadge></div>)}</div> : null}</section>
        <section className="panel detail-section"><div className="panel-header"><div><h2>CloudWatch log groups</h2><p>Log retention and size</p></div><Database size={16} className="muted-icon" /></div>{logGroups?.length ? <div className="check-list">{logGroups.slice(0, 12).map((group) => <div className="check-row" key={group.name}><div className="check-main"><strong>{group.name}</strong><small>{group.stored_mb} MB · retention {group.retention_days ? `${group.retention_days} days` : 'never expires'}</small></div></div>)}</div> : <EmptyState title="No CloudWatch data" detail="Enable the AWS integration to list log groups." />}</section>
      </div>
    </div>
  )
}

function Stat({ icon, label, value, note, tone }: { icon: React.ReactNode; label: string; value: string; note: string; tone: string }) {
  return <div className={`summary-stat summary-${tone}`}><span className="summary-icon">{icon}</span><div className="summary-copy"><span>{label}</span><strong>{value}</strong><small>{note}</small></div></div>
}
