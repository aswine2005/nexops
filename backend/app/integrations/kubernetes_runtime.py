"""Kubernetes adapter: Deployment or Argo Rollout health, events, logs, and controlled rollout changes."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.integrations.docker_runtime import image_tag, parse_timestamped_line
from app.integrations.types import IntegrationError, LogLine, Replica, RuntimeState, parse_log_line

if TYPE_CHECKING:
    from app.models import Application

_SUFFIX = {
    "n": 1e-9, "u": 1e-6, "m": 1e-3, "": 1, "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12,
    "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4,
}


def parse_quantity(value: str | None) -> float | None:
    if not value:
        return None
    value = str(value).strip()
    for suffix in sorted(_SUFFIX, key=len, reverse=True):
        if suffix and value.endswith(suffix):
            try:
                return float(value[: -len(suffix)]) * _SUFFIX[suffix]
            except ValueError:
                return None
    try:
        return float(value)
    except ValueError:
        return None


ROLLOUTS = ("argoproj.io", "v1alpha1", "rollouts")
HASH_LABEL = "rollouts-pod-template-hash"


@dataclass
class Workload:
    """The parts of a Deployment or Argo Rollout NexOps needs, in one shape."""
    kind: str  # Deployment | Rollout
    namespace: str
    name: str
    replicas: int | None
    selector: dict[str, str]
    containers: list[dict]  # {"name", "image", "limits"}
    rollout: dict = field(default_factory=dict)  # raw Rollout object (kind == "Rollout")

    @property
    def label_selector(self) -> str:
        return ",".join(f"{key}={value}" for key, value in self.selector.items())

    @property
    def ref(self) -> str:
        return f"{self.namespace}/{self.name}"


class KubernetesRuntime:
    def __init__(self, in_cluster: bool = False, kubeconfig: str | None = None, context: str | None = None) -> None:
        try:
            from kubernetes import client, config  # noqa: PLC0415 - optional dependency
        except ImportError as error:  # pragma: no cover
            raise IntegrationError("The 'kubernetes' Python package is not installed.") from error
        try:
            if in_cluster:
                config.load_incluster_config()
            else:
                config.load_kube_config(config_file=kubeconfig, context=context)
        except Exception as error:
            raise IntegrationError(f"Kubernetes configuration could not be loaded: {error}") from error
        self.core = client.CoreV1Api()
        self.apps = client.AppsV1Api()
        self.custom = client.CustomObjectsApi()
        self._exceptions = client.exceptions
        self._kinds: dict[str, str] = {}  # namespace/name -> Deployment | Rollout

    def _require(self, app: Application) -> tuple[str, str]:
        if not app.k8s_namespace or not app.k8s_deployment:
            raise IntegrationError(f"{app.id} has no Kubernetes namespace/deployment configured.")
        return app.k8s_namespace, app.k8s_deployment

    def _workload(self, app: Application) -> Workload:
        """Read the app's workload: an apps/v1 Deployment, or an Argo Rollout with the same name."""
        namespace, name = self._require(app)
        key = f"{namespace}/{name}"
        order = ["Rollout", "Deployment"] if self._kinds.get(key) == "Rollout" else ["Deployment", "Rollout"]
        reasons = []
        for kind in order:
            try:
                workload = self._read_rollout(namespace, name) if kind == "Rollout" else self._read_deployment(namespace, name)
            except self._exceptions.ApiException as error:
                if error.status == 404:
                    reasons.append(f"no {kind}")
                    continue
                raise IntegrationError(f"{kind} {key} could not be read: {error.reason}") from error
            self._kinds[key] = kind
            return workload
        raise IntegrationError(f"Kubernetes workload {key} not found ({', '.join(reasons)}).")

    def _read_deployment(self, namespace: str, name: str) -> Workload:
        deployment = self.apps.read_namespaced_deployment(name, namespace)
        containers = [
            {"name": item.name, "image": item.image, "limits": (item.resources.limits if item.resources and item.resources.limits else None) or {}}
            for item in deployment.spec.template.spec.containers
        ]
        return Workload("Deployment", namespace, deployment.metadata.name, deployment.spec.replicas, deployment.spec.selector.match_labels or {}, containers)

    def _read_rollout(self, namespace: str, name: str) -> Workload:
        rollout = self.custom.get_namespaced_custom_object(*ROLLOUTS[:2], namespace, ROLLOUTS[2], name)
        spec = rollout.get("spec") or {}
        containers = [
            {"name": item.get("name"), "image": item.get("image"), "limits": (item.get("resources") or {}).get("limits") or {}}
            for item in ((spec.get("template") or {}).get("spec") or {}).get("containers") or []
        ]
        if not containers:
            raise IntegrationError(f"Rollout {namespace}/{name} has no inline pod template (workloadRef is not supported).")
        return Workload("Rollout", namespace, name, spec.get("replicas"), (spec.get("selector") or {}).get("matchLabels") or {}, containers, rollout)

    @staticmethod
    def _container(app: Application, workload: Workload) -> tuple[int, dict]:
        for index, container in enumerate(workload.containers):
            if container["name"] == (app.k8s_container or workload.containers[0]["name"]):
                return index, container
        return 0, workload.containers[0]

    def state(self, app: Application) -> RuntimeState:
        workload = self._workload(app)
        _, spec_container = self._container(app, workload)
        container_name = spec_container["name"]
        cpu_limit = parse_quantity(spec_container["limits"].get("cpu"))
        memory_limit = parse_quantity(spec_container["limits"].get("memory"))
        pods = [pod for pod in self.core.list_namespaced_pod(workload.namespace, label_selector=workload.label_selector).items if pod.metadata.deletion_timestamp is None]
        usage = self._pod_usage(workload.namespace, workload.label_selector, container_name)
        stable_hash = ((workload.rollout.get("status") or {}).get("stableRS")) if workload.kind == "Rollout" else None
        replicas = []
        for pod in pods:
            status = next((item for item in (pod.status.container_statuses or []) if item.name == container_name), None)
            reason = None
            exit_code = None
            restarts = 0
            ready = False
            image = spec_container["image"]
            if status:
                restarts = status.restart_count or 0
                ready = bool(status.ready)
                image = status.image or image
                if status.state and status.state.waiting:
                    reason = status.state.waiting.reason
                elif status.state and status.state.terminated:
                    reason = status.state.terminated.reason
                    exit_code = status.state.terminated.exit_code
                if status.last_state and status.last_state.terminated:
                    exit_code = exit_code if exit_code is not None else status.last_state.terminated.exit_code
                    if status.last_state.terminated.reason == "OOMKilled" and not reason:
                        reason = "OOMKilled"
            # The pod spec holds the image as written in the template (status.image may be a resolved digest).
            spec_image = next((item.image for item in pod.spec.containers if item.name == container_name), image)
            cpu_cores, memory_bytes = usage.get(pod.metadata.name, (None, None))
            pod_hash = (pod.metadata.labels or {}).get(HASH_LABEL)
            replicas.append(Replica(
                name=pod.metadata.name,
                status=reason or pod.status.phase or "Unknown",
                ready=ready,
                restarts=restarts,
                image=spec_image,
                version=image_tag(spec_image),
                node=pod.spec.node_name,
                started_at=pod.status.start_time.isoformat() if pod.status.start_time else None,
                reason=reason,
                exit_code=exit_code,
                cpu_percent=round(cpu_cores / cpu_limit * 100, 2) if cpu_cores is not None and cpu_limit else None,
                memory_percent=round(memory_bytes / memory_limit * 100, 2) if memory_bytes is not None and memory_limit else None,
                track=("stable" if pod_hash == stable_hash else "canary") if workload.kind == "Rollout" and stable_hash else None,
            ))
        image = spec_container["image"]
        rollout = self._rollout_progress(workload, replicas) if workload.kind == "Rollout" else None
        if rollout and rollout.get("stable_image"):
            image = rollout["stable_image"]  # what serves production traffic; the canary is reported separately
        return RuntimeState(
            runtime="kubernetes",
            target=f"{workload.ref}" if workload.kind == "Deployment" else f"{workload.ref} (Argo Rollout)",
            replicas=replicas,
            desired_replicas=workload.replicas,
            events=self._events(workload.namespace, workload.name, [pod.metadata.name for pod in pods]),
            image=image,
            version=image_tag(image),
            rollout=rollout,
        )

    @staticmethod
    def _rollout_progress(workload: Workload, replicas: list[Replica]) -> dict:
        spec = workload.rollout.get("spec") or {}
        status = workload.rollout.get("status") or {}
        steps = ((spec.get("strategy") or {}).get("canary") or {}).get("steps") or []
        index = status.get("currentStepIndex")
        stable_hash, current_hash = status.get("stableRS"), status.get("currentPodHash")
        updating = bool(stable_hash and current_hash and stable_hash != current_hash)
        weight = None
        if updating:
            weight = 0
            if not status.get("abort") and index is not None:
                for step in steps[: index + 1]:
                    if "setWeight" in step:
                        weight = int(step["setWeight"])
        stable_images = [replica.image for replica in replicas if replica.track == "stable" and replica.image]
        template_image = workload.containers[0]["image"]
        stable_image = max(set(stable_images), key=stable_images.count) if stable_images else (None if updating else template_image)
        return {
            "kind": "Rollout",
            "phase": status.get("phase"),
            "message": status.get("message"),
            "step": index,
            "steps": len(steps),
            "updating": updating,
            "aborted": bool(status.get("abort")),
            "paused": bool(spec.get("paused") or status.get("pauseConditions")),
            "canary_weight": weight,
            "stable_hash": stable_hash,
            "canary_hash": current_hash if updating else None,
            "stable_image": stable_image,
            "stable_version": image_tag(stable_image) if stable_image else None,
            "canary_version": image_tag(template_image) if updating else None,
        }

    def _pod_usage(self, namespace: str, selector: str, container_name: str) -> dict[str, tuple[float | None, float | None]]:
        try:
            response = self.custom.list_namespaced_custom_object("metrics.k8s.io", "v1beta1", namespace, "pods", label_selector=selector)
        except Exception:
            return {}  # metrics-server is optional
        usage = {}
        for item in response.get("items", []):
            container = next((entry for entry in item.get("containers", []) if entry.get("name") == container_name), None)
            if container:
                usage[item["metadata"]["name"]] = (parse_quantity(container["usage"].get("cpu")), parse_quantity(container["usage"].get("memory")))
        return usage

    def _events(self, namespace: str, workload_name: str, pod_names: list[str]) -> list[dict]:
        try:
            items = self.core.list_namespaced_event(namespace).items
        except Exception:
            return []
        names = set(pod_names) | {workload_name}
        events = []
        for event in items:
            involved = event.involved_object.name if event.involved_object else ""
            # ReplicaSets and Argo AnalysisRuns are named after the workload.
            if involved in names or involved.startswith(workload_name):
                timestamp = event.last_timestamp or event.event_time or event.first_timestamp
                events.append({
                    "time": timestamp.isoformat() if timestamp else None,
                    "type": event.type,
                    "reason": event.reason,
                    "object": involved,
                    "message": (event.message or "")[:500],
                    "count": event.count,
                })
        events.sort(key=lambda item: item["time"] or "", reverse=True)
        return events[:30]

    def logs(self, app: Application, since: datetime, limit: int = 200) -> list[LogLine]:
        workload = self._workload(app)
        _, container = self._container(app, workload)
        since_seconds = max(1, int((datetime.now(timezone.utc) - since).total_seconds()))
        lines: list[LogLine] = []
        for pod in self.core.list_namespaced_pod(workload.namespace, label_selector=workload.label_selector).items:
            for previous in (False, True):
                try:
                    # Raw response: recent clients return str(bytes) ("b'...'") when the body is deserialized.
                    response = self.core.read_namespaced_pod_log(
                        pod.metadata.name, workload.namespace, container=container["name"], since_seconds=since_seconds,
                        tail_lines=limit, timestamps=True, previous=previous, _preload_content=False,
                    )
                    raw = response.data.decode("utf-8", errors="replace")
                except Exception:
                    continue
                for line in (raw or "").splitlines():
                    if line.strip():
                        timestamp, message = parse_timestamped_line(line)
                        lines.append(parse_log_line(message, timestamp, pod.metadata.name, app.name))
        lines.sort(key=lambda line: line.timestamp, reverse=True)
        return lines[:limit]

    def _patch_rollout(self, workload: Workload, body: list | dict) -> None:
        content_type = "application/json-patch+json" if isinstance(body, list) else "application/merge-patch+json"
        self.custom.patch_namespaced_custom_object(*ROLLOUTS[:2], workload.namespace, ROLLOUTS[2], workload.name, body, _content_type=content_type)

    def set_image(self, app: Application, image: str) -> dict:
        """Rollback. On an Argo Rollout, returning to the stable (or a recent) revision skips the canary steps."""
        workload = self._workload(app)
        index, container = self._container(app, workload)
        try:
            if workload.kind == "Rollout":
                self._patch_rollout(workload, [{"op": "replace", "path": f"/spec/template/spec/containers/{index}/image", "value": image}])
            else:
                patch = {"spec": {"template": {
                    "metadata": {"annotations": {"nexops.io/rolled-back-at": datetime.now(timezone.utc).isoformat()}},
                    "spec": {"containers": [{"name": container["name"], "image": image}]},
                }}}
                self.apps.patch_namespaced_deployment(workload.name, workload.namespace, patch)
        except self._exceptions.ApiException as error:
            raise IntegrationError(f"Kubernetes rejected the image change: {error.reason}") from error
        return {workload.kind.lower(): workload.ref, "container": container["name"], "image": image}

    def restart(self, app: Application) -> dict:
        workload = self._workload(app)
        now = datetime.now(timezone.utc)
        if workload.kind == "Rollout":
            self._patch_rollout(workload, {"spec": {"restartAt": now.strftime("%Y-%m-%dT%H:%M:%SZ")}})
        else:
            self.apps.patch_namespaced_deployment(workload.name, workload.namespace, {"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": now.isoformat()}}}}})
        return {workload.kind.lower(): workload.ref, "action": "rollout restart"}

    def scale(self, app: Application, replicas: int) -> dict:
        workload = self._workload(app)
        if workload.kind == "Rollout":
            self._patch_rollout(workload, {"spec": {"replicas": replicas}})
        else:
            self.apps.patch_namespaced_deployment_scale(workload.name, workload.namespace, {"spec": {"replicas": replicas}})
        return {workload.kind.lower(): workload.ref, "replicas": replicas}

    def cluster_summary(self) -> dict:
        nodes = self.core.list_node().items
        summary: dict[str, Any] = {
            "nodes": [
                {
                    "name": node.metadata.name,
                    "ready": any(condition.type == "Ready" and condition.status == "True" for condition in (node.status.conditions or [])),
                    "instance_type": (node.metadata.labels or {}).get("node.kubernetes.io/instance-type"),
                    "zone": (node.metadata.labels or {}).get("topology.kubernetes.io/zone"),
                    "kubelet": node.status.node_info.kubelet_version if node.status.node_info else None,
                }
                for node in nodes
            ],
        }
        try:
            items = self.custom.list_cluster_custom_object(*ROLLOUTS[:2], ROLLOUTS[2]).get("items", [])
            summary["rollouts"] = [
                {"namespace": item["metadata"]["namespace"], "name": item["metadata"]["name"], "phase": (item.get("status") or {}).get("phase"), "message": (item.get("status") or {}).get("message")}
                for item in items
            ]
        except Exception:
            summary["rollouts"] = None  # Argo Rollouts not installed
        return summary
