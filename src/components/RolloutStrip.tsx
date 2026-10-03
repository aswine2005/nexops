import type { RolloutProgress } from '../api/types'

/** Argo Rollouts progress: phase, canary weight and versions. */
export function RolloutStrip({ rollout }: { rollout: RolloutProgress }) {
  const tone = rollout.aborted || rollout.phase === 'Degraded' ? 'critical' : rollout.updating || rollout.paused ? 'warning' : 'healthy'
  const progress = rollout.updating ? (rollout.aborted ? `canary ${rollout.canary_version} aborted` : `canary ${rollout.canary_version} at ${rollout.canary_weight ?? 0}% · step ${(rollout.step ?? 0) + 1}/${rollout.steps}`) : `stable ${rollout.stable_version ?? '—'}`
  return <div className={`rollout-strip rollout-${tone}`}><span className="rollout-kind">Argo Rollout</span><strong>{rollout.phase ?? 'Unknown'}</strong><span>{progress}</span>{rollout.updating && <span className="rollout-bar" aria-label={`canary weight ${rollout.canary_weight ?? 0}%`}><i style={{ width: `${rollout.aborted ? 0 : rollout.canary_weight ?? 0}%` }} /></span>}{rollout.message && <small>{rollout.message}</small>}</div>
}
