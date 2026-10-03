export type Environment = 'production' | 'staging' | 'development'
export type Role = 'admin' | 'operator' | 'viewer'

export interface User {
  id: string
  email: string
  display_name: string
  role: Role
  is_active: boolean
  created_at: string | null
  last_login_at: string | null
}

export interface Sample {
  observed_at: string | null
  request_rate: number | null
  error_rate_percent: number | null
  p50_latency_ms: number | null
  p95_latency_ms: number | null
  p99_latency_ms: number | null
  cpu_percent: number | null
  memory_percent: number | null
  business_success_rate: number | null
  healthy_replicas: number | null
  total_replicas: number | null
  restarts: number | null
  crashlooping: number | null
  db_errors: number | null
  failed_health_checks: number | null
  health_checks_total: number | null
  version: string | null
  sources: string[]
  errors: Record<string, string>
}

export interface Replica {
  name: string
  status: string
  ready: boolean
  restarts: number
  image: string | null
  version: string | null
  node: string | null
  started_at: string | null
  reason: string | null
  exit_code: number | null
  cpu_percent: number | null
  memory_percent: number | null
  track?: 'stable' | 'canary' | null
}

export interface RolloutProgress {
  kind: 'Rollout'
  phase: string | null
  message: string | null
  step: number | null
  steps: number
  updating: boolean
  aborted: boolean
  paused: boolean
  canary_weight: number | null
  stable_version: string | null
  canary_version: string | null
}

export interface CanaryMeasurement { request_rate: number | null; error_rate_percent: number | null; p99_latency_ms: number | null; business_success_rate: number | null }

export interface CanaryAnalysis {
  at: string | null
  summary: string | null
  verdict: 'pass' | 'fail' | 'inconclusive'
  reasons: string[]
  canary: CanaryMeasurement
  stable: Partial<CanaryMeasurement>
  limits: { error_rate_percent: number; p99_latency_ms: number; business_success_rate: number } | null
  canary_weight: number | null
  canary_version: string | null
  window: string
}

export interface RuntimeEvent { time: string | null; type: string; reason: string; object: string; message: string; count: number | null }

export interface RuntimeState {
  runtime: string
  target: string
  image: string | null
  version: string | null
  desired_replicas: number | null
  healthy_replicas: number
  total_replicas: number
  restarts: number
  crashlooping: number | null
  replicas: Replica[]
  events: RuntimeEvent[]
  rollout?: RolloutProgress | null
}

export interface HealthCheckResult { name: string; url: string; status: 'passing' | 'failing'; http_status: number | null; latency_ms: number; error: string | null }

export interface BriefDeployment { id: string; version: string; status: string; kind: string; finished_at: string | null; started_at: string | null }
export interface BriefIncident { id: string; title: string; severity: string; status: string; detected_at: string | null }

export interface ApplicationConfig {
  id: string
  name: string
  description: string | null
  owner: string | null
  environment: Environment
  repository: string | null
  runtime: 'docker' | 'kubernetes' | 'ec2' | 'external'
  monitoring_enabled: boolean
  prometheus_selector: string | null
  metric_queries: Record<string, string>
  loki_selector: string | null
  cloudwatch_log_group: string | null
  tracing_service: string | null
  health_checks: Array<{ name: string; url: string }>
  docker_container: string | null
  k8s_namespace: string | null
  k8s_deployment: string | null
  k8s_container: string | null
  ec2_instance_id: string | null
  image_repository: string | null
  rollback_strategy: 'docker' | 'kubernetes' | 'github_actions' | 'none'
  github_workflow: string | null
  github_ref: string | null
  thresholds: Record<string, number>
  dependencies: string[]
}

export interface Application extends ApplicationConfig {
  current_version: string | null
  previous_version: string | null
  status: 'healthy' | 'degraded' | 'critical' | 'unknown'
  status_reason: string | null
  deploy_frozen: boolean
  deploy_freeze_reason: string | null
  last_checked_at: string | null
  latest: Sample | null
  sparkline: number[]
  last_deployment: BriefDeployment | null
  active_incident: BriefIncident | null
  created_at: string | null
}

export interface ApplicationDetail extends Application {
  runtime_state: RuntimeState | null
  health_check_results: HealthCheckResult[]
  deployments: Deployment[]
  incidents: Incident[]
}

export interface PipelineStage { name: string; status: string }

export interface DeploymentChange { file: string; change_type: string; additions: number; deletions: number; risk_tag: string | null; patch: string | null }

export interface Deployment {
  id: string
  number: number
  application_id: string
  application_name: string
  environment: Environment
  version: string
  previous_version: string | null
  kind: 'release' | 'rollback' | 'hotfix'
  source: string
  status: string
  status_reason: string | null
  ci_status: string
  ci_run_id: string | null
  ci_run_url: string | null
  commit_sha: string | null
  commit_message: string | null
  branch: string | null
  author: string | null
  repository: string | null
  image: string | null
  pipeline: PipelineStage[]
  risk_score: number | null
  risk_level: 'low' | 'medium' | 'high' | null
  risk_factors: string[]
  files_changed: number
  additions: number
  deletions: number
  summary: string | null
  started_at: string | null
  finished_at: string | null
  verified_at: string | null
  lead_time_minutes: number | null
  incident: BriefIncident | null
  changes?: DeploymentChange[]
  risk_assessment?: { summary: string; output: Record<string, unknown> } | null
  canary_analyses?: CanaryAnalysis[]
}

export interface Signal { code: string; title: string; detail: string; severity: string; observed_value: number; threshold: number; baseline_value: number | null }
export interface TimelineEvent { time: string | null; kind: string; title: string; detail: string; severity: string; actor: string | null }

export interface Incident {
  id: string
  number: number
  application_id: string
  application_name: string
  environment: Environment | null
  deployment: { id: string; version: string; previous_version: string | null; commit_sha: string | null; author: string | null; status: string; finished_at: string | null } | null
  title: string
  severity: 'critical' | 'high' | 'medium' | 'low'
  status: 'open' | 'investigating' | 'awaiting_approval' | 'mitigating' | 'verifying' | 'resolved'
  summary: string
  signals: Signal[]
  baseline: Record<string, number>
  observed: Record<string, number | string>
  peak: Record<string, number | string>
  deployment_correlated: boolean
  root_cause: string | null
  confidence: number | null
  recommended_action: string | null
  resolution_summary: string | null
  detected_at: string | null
  acknowledged_at: string | null
  mitigated_at: string | null
  resolved_at: string | null
  duration_seconds: number | null
  timeline?: TimelineEvent[]
}

export interface EvidenceItem { id: string; kind: 'DEPLOYMENT' | 'CHANGE' | 'METRIC' | 'LOG' | 'TRACE' | 'FLAG' | 'RUNTIME' | 'HEALTH' | 'HISTORY' | 'GAP'; title: string; detail: string; data: Record<string, unknown> }

export interface Investigation {
  id: number
  status: string
  provider: string | null
  model: string | null
  root_cause: string | null
  confidence: number | null
  reasoning_summary: string | null
  recommended_action: string | null
  recommended_flag: string | null
  recommendation_detail: string | null
  alternative_hypotheses: string[]
  next_steps: string[]
  evidence: EvidenceItem[]
  cited_evidence_ids: string[]
  error: string | null
  started_at: string | null
  completed_at: string | null
}

export interface Remediation {
  id: number
  action: string
  target_version: string | null
  target_image: string | null
  target_flag: string | null
  status: 'proposed' | 'approved' | 'rejected' | 'executing' | 'dispatched' | 'succeeded' | 'failed' | 'blocked' | 'superseded'
  mode: string
  policy_id: string | null
  policy_reasons: string[]
  reason: string | null
  proposed_by: string
  approved_by: string | null
  approved_at: string | null
  rejected_reason: string | null
  executor: string | null
  started_at: string | null
  completed_at: string | null
  outcome: Record<string, unknown>
  error: string | null
  created_at: string | null
}

export interface RecoveryCheck { name: string; passed: boolean; observed: string; expected: string }
export interface Verification {
  id: number
  status: 'running' | 'passed' | 'failed' | 'superseded'
  healthy_samples: number
  required_samples: number
  checks: RecoveryCheck[]
  baseline: Record<string, number>
  target_version: string | null
  detail: string | null
  started_at: string | null
  not_before: string | null
  deadline_at: string | null
  finished_at: string | null
}

export interface Postmortem {
  provider: string
  summary: string
  impact: string
  root_cause: string
  detection: string
  resolution: string
  recovery: string
  preventive_actions: string[]
  lessons: string[]
  metrics: Record<string, unknown>
  generated_at: string | null
}

export interface SimilarIncident { id: string | null; title: string; root_cause: string; action: string; failed_version: string | null; score: number; recovery_successful: boolean }

export interface IncidentWorkflow {
  incident: Incident
  investigation: Investigation | null
  remediations: Remediation[]
  verification: Verification | null
  postmortem: Postmortem | null
  similar: SimilarIncident[]
}

export interface MetricPoint {
  t: string
  request_rate: number | null
  error_rate_percent: number | null
  p50_latency_ms: number | null
  p95_latency_ms: number | null
  p99_latency_ms: number | null
  cpu_percent: number | null
  memory_percent: number | null
  healthy_replicas: number | null
  total_replicas: number | null
  restarts: number | null
  db_errors: number | null
  business_success_rate: number | null
}

export interface MetricsResponse {
  application_id: string
  application_name: string
  range: string
  start: string
  end: string
  points: MetricPoint[]
  latest: Sample | null
  previous: Record<string, number>
  thresholds: Record<string, number>
  deployments: Array<{ id: string; version: string; kind: string; status: string; t: string }>
  incidents: Array<{ id: string; title: string; detected_at: string; resolved_at: string | null; status: string }>
  sample_count: number
}

export interface LogEntry { timestamp: string; level: string; message: string; source: string; service: string; trace_id: string | null; application_id: string }
export interface LogsResponse { entries: LogEntry[]; sources: Record<string, string>; errors: Record<string, string>; start: string; end: string }

export interface Dashboard {
  environment: string | null
  generated_at: string
  status: 'healthy' | 'degraded' | 'critical' | 'unknown'
  summary: { applications: number; healthy: number; degraded: number; unknown: number; active_incidents: number; deployments_today: number }
  golden_signals: { request_rate?: number | null; error_rate_percent?: number | null; p99_latency_ms?: number | null; cpu_percent?: number | null; memory_percent?: number | null; healthy_replicas?: number | null; total_replicas?: number | null }
  previous_hour: Record<string, number>
  applications: Application[]
  recent_deployments: Deployment[]
  active_incidents: Incident[]
  focus: {
    application_id: string | null
    application_name: string | null
    series: Array<{ t: string; error_rate_percent: number | null; p99_latency_ms: number | null; request_rate: number | null }>
    deployment_markers: Array<{ id: string; version: string; kind: string; t: string }>
    runtime: (RuntimeState & { crashlooping?: number | null }) | null
    logs: Array<{ timestamp: string; level: string; message: string; source: string; service: string; trace_id: string | null }>
    log_source: string | null
    baseline_error_rate: number | null
  }
  last_sample_at: string | null
}

export interface DoraPeriod {
  month: string
  period_start: string
  deployments: number
  deployments_per_day: number
  lead_time_minutes: number | null
  change_failure_rate_percent: number | null
  recovery_time_minutes: number | null
  rework_rate_percent: number | null
  failed_deployments: number
  rework_deployments: number
  total_deployments: number
}

export interface DoraResponse {
  periods: DoraPeriod[]
  summary: {
    window_days: number
    deployments: number
    deployments_per_day: number
    lead_time_minutes: number | null
    change_failure_rate_percent: number | null
    recovery_time_minutes: number | null
    rework_rate_percent: number | null
    levels: Record<string, string | null>
  }
  environment: string | null
  application_id: string | null
}

export interface AgentRun {
  id: number
  agent: string
  status: string
  incident_id: string | null
  deployment_id: string | null
  provider: string | null
  model: string | null
  summary: string | null
  error: string | null
  output: Record<string, unknown>
  started_at: string | null
  finished_at: string | null
  duration_seconds: number | null
}

export interface Agent {
  id: string
  name: string
  stage: string
  description: string
  trigger: string
  uses_llm: boolean
  inputs: string[]
  outputs: string[]
  provider: string
  model: string | null
  status: 'running' | 'idle' | 'waiting'
  runs_total: number
  runs_24h: number
  success_rate: number | null
  last_run: AgentRun | null
}

export interface Policy {
  id: string
  name: string
  description: string
  action: 'rollback' | 'restart' | 'scale_up' | 'pause_deployments' | 'disable_feature' | 'database_modification'
  environment: Environment | '*'
  application_id: string | null
  mode: 'automatic' | 'approval_required' | 'forbidden'
  enabled: boolean
  conditions: Record<string, number | boolean>
  created_at: string | null
  updated_at: string | null
}

export interface IntegrationStatus { key: string; name: string; configured: boolean; healthy: boolean; detail: string }

export interface RuntimeSettings {
  version: string
  environment: string
  monitor_interval_seconds: number
  detection_consecutive_breaches: number
  correlation_window_minutes: number
  deployment_verification_minutes: number
  recovery_required_healthy_samples: number
  recovery_timeout_minutes: number
  auto_investigate: boolean
  remediation_enabled: boolean
  metric_retention_days: number
  prometheus_rate_window: string
  llm_provider: string
  llm_model: string | null
  webhook_url: string
  jaeger_ui_url: string | null
  notifications_enabled: boolean
}

export interface InfrastructureWorkload {
  application_id: string
  application_name: string
  environment: string
  runtime: string
  target: string | null
  image: string | null
  version: string | null
  healthy_replicas: number | null
  total_replicas: number | null
  restarts: number | null
  replicas: Replica[]
  events: RuntimeEvent[]
  rollout?: RolloutProgress | null
  observed_at: string | null
}

export interface InfrastructureResponse {
  workloads: InfrastructureWorkload[]
  providers: Record<string, { available: boolean; data?: unknown; error?: string }>
  aws_region: string | null
}

export interface FeatureFlag {
  id: number
  application_id: string
  key: string
  description: string | null
  enabled: boolean
  rollout_percent: number
  updated_by: string | null
  updated_at: string | null
  created_at: string | null
  history: Array<{ enabled: boolean; rollout_percent: number; actor: string; reason: string | null; changed_at: string | null }>
}

export interface AuditEntry { id: number; actor: string; action: string; target_type: string; target_id: string; detail: Record<string, unknown>; created_at: string }
