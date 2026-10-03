"""Docker Engine adapter: container health, logs, and controlled redeploys on a Docker host (local or EC2)."""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.integrations.types import IntegrationError, LogLine, Replica, RuntimeState, parse_log_line

if TYPE_CHECKING:
    from app.models import Application

_DOCKER_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?Z\s?(.*)$")


def image_tag(image: str | None) -> str | None:
    if not image or ":" not in image.rsplit("/", 1)[-1]:
        return None
    return image.rsplit(":", 1)[-1]


def parse_timestamped_line(line: str) -> tuple[datetime, str]:
    match = _DOCKER_TS.match(line)
    if not match:
        return datetime.now(timezone.utc), line
    base, fraction, message = match.groups()
    micro = int((fraction or "0")[:6].ljust(6, "0"))
    timestamp = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc, microsecond=micro)
    return timestamp, message


class DockerRuntime:
    def __init__(self, base_url: str | None = None) -> None:
        try:
            import docker  # noqa: PLC0415 - optional dependency loaded on demand
        except ImportError as error:  # pragma: no cover - dependency is in requirements.txt
            raise IntegrationError("The 'docker' Python package is not installed.") from error
        self._docker = docker
        try:
            self.client = docker.DockerClient(base_url=base_url) if base_url else docker.from_env()
            self.client.ping()
        except Exception as error:  # docker raises several exception types for connection failures
            raise IntegrationError(f"Docker Engine is unreachable: {error}") from error

    def close(self) -> None:
        self.client.close()

    # ---------------------------------------------------------------- discovery
    def containers(self, app: Application) -> list[Any]:
        found: dict[str, Any] = {}
        for container in self.client.containers.list(all=True, filters={"label": f"nexops.application={app.id}"}):
            found[container.id] = container
        if app.docker_container:
            for container in self.client.containers.list(all=True, filters={"label": f"com.docker.compose.service={app.docker_container}"}):
                found[container.id] = container
            for container in self.client.containers.list(all=True, filters={"name": app.docker_container}):
                if container.name == app.docker_container:
                    found[container.id] = container
        return [container for container in found.values() if "-nexops-replaced-" not in container.name]

    # ---------------------------------------------------------------- state
    def state(self, app: Application, with_stats: bool = True) -> RuntimeState:
        containers = self.containers(app)
        if not containers:
            return RuntimeState(runtime="docker", target=app.docker_container or app.id)
        stats: dict[str, tuple[float | None, float | None]] = {}
        running = [container for container in containers if container.status == "running"]
        if with_stats and running:
            with ThreadPoolExecutor(max_workers=min(8, len(running))) as pool:
                for container, values in zip(running, pool.map(self._stats, running)):
                    stats[container.id] = values
        replicas = []
        for container in containers:
            attrs = container.attrs
            state = attrs.get("State", {})
            status = state.get("Status", container.status)
            health = (state.get("Health") or {}).get("Status")
            ready = status == "running" and health in (None, "healthy")
            reason = None
            if state.get("OOMKilled"):
                reason = "OOMKilled"
            elif status == "restarting":
                reason = "Restarting"
            elif status in {"exited", "dead"} and state.get("ExitCode", 0) != 0:
                reason = "Error"
            elif health == "unhealthy":
                reason = "Unhealthy"
            elif health == "starting":
                reason = "Starting"
            image = attrs.get("Config", {}).get("Image")
            labels = container.labels or {}
            cpu, memory = stats.get(container.id, (None, None))
            replicas.append(Replica(
                name=container.name,
                status=status if not health else f"{status} ({health})",
                ready=ready,
                restarts=int(attrs.get("RestartCount") or 0),
                image=image,
                version=labels.get("nexops.version") or labels.get("org.opencontainers.image.version") or image_tag(image),
                node=attrs.get("Config", {}).get("Hostname"),
                started_at=state.get("StartedAt"),
                reason=reason,
                exit_code=state.get("ExitCode") if status != "running" else None,
                cpu_percent=cpu,
                memory_percent=memory,
            ))
        versions = {replica.version for replica in replicas if replica.version}
        images = {replica.image for replica in replicas if replica.image}
        return RuntimeState(
            runtime="docker",
            target=app.docker_container or app.id,
            replicas=replicas,
            desired_replicas=len(containers),
            image=next(iter(images)) if len(images) == 1 else None,
            version=next(iter(versions)) if len(versions) == 1 else None,
        )

    @staticmethod
    def _stats(container: Any) -> tuple[float | None, float | None]:
        try:
            data = container.stats(stream=False)
            cpu_stats, pre = data.get("cpu_stats", {}), data.get("precpu_stats", {})
            cpu_delta = cpu_stats.get("cpu_usage", {}).get("total_usage", 0) - pre.get("cpu_usage", {}).get("total_usage", 0)
            system_delta = cpu_stats.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
            online = cpu_stats.get("online_cpus") or len(cpu_stats.get("cpu_usage", {}).get("percpu_usage") or []) or 1
            cpu = round(cpu_delta / system_delta * online * 100, 2) if system_delta > 0 and cpu_delta >= 0 else None
            memory_stats = data.get("memory_stats", {})
            usage = memory_stats.get("usage")
            limit = memory_stats.get("limit")
            inactive = (memory_stats.get("stats") or {}).get("inactive_file", 0)
            memory = round((usage - inactive) / limit * 100, 2) if usage and limit else None
            return cpu, memory
        except Exception:  # stats are best effort; a container may stop mid-read
            return None, None

    # ---------------------------------------------------------------- logs
    def logs(self, app: Application, since: datetime, until: datetime | None = None, limit: int = 200) -> list[LogLine]:
        lines: list[LogLine] = []
        for container in self.containers(app):
            kwargs: dict[str, Any] = {"since": int(since.timestamp()), "timestamps": True, "tail": limit}
            if until:
                kwargs["until"] = int(until.timestamp()) + 1
            try:
                raw = container.logs(**kwargs).decode("utf-8", errors="replace")
            except Exception:
                continue
            for line in raw.splitlines():
                if not line.strip():
                    continue
                timestamp, message = parse_timestamped_line(line)
                lines.append(parse_log_line(message, timestamp, container.name, app.name))
        lines.sort(key=lambda line: line.timestamp, reverse=True)
        return lines[:limit]

    # ---------------------------------------------------------------- actions
    def restart(self, app: Application) -> dict:
        containers = self.containers(app)
        if not containers:
            raise IntegrationError(f"No Docker containers found for {app.id}.")
        for container in containers:
            container.restart(timeout=10)
        return {"containers": [container.name for container in containers]}

    def redeploy(self, app: Application, image: str) -> dict:
        containers = self.containers(app)
        if not containers:
            raise IntegrationError(f"No Docker containers found for {app.id}.")
        try:
            self.client.images.get(image)
        except self._docker.errors.ImageNotFound:
            try:
                self.client.images.pull(image)
            except Exception as error:
                raise IntegrationError(f"Image {image} is not available on the Docker host: {error}") from error
        replaced = [self._recreate(container, image) for container in containers]
        return {"image": image, "containers": replaced}

    def _recreate(self, old: Any, image: str) -> str:
        api = self.client.api
        old.reload()
        attrs = old.attrs
        config = attrs.get("Config", {})
        host = attrs.get("HostConfig", {})
        networks = (attrs.get("NetworkSettings") or {}).get("Networks") or {}
        try:
            old_image_config = old.image.attrs.get("Config", {})
        except Exception:
            old_image_config = {}

        # Keep only settings supplied at run time; values baked into the old image must not leak into the new one.
        image_env = set(old_image_config.get("Env") or [])
        environment = [item for item in (config.get("Env") or []) if item not in image_env]
        image_labels = old_image_config.get("Labels") or {}
        labels = {key: value for key, value in (config.get("Labels") or {}).items() if image_labels.get(key) != value}
        version = image_tag(image)
        if version:
            labels["nexops.version"] = version
        command = config.get("Cmd") if config.get("Cmd") != old_image_config.get("Cmd") else None
        entrypoint = config.get("Entrypoint") if config.get("Entrypoint") != old_image_config.get("Entrypoint") else None
        healthcheck = config.get("Healthcheck") if config.get("Healthcheck") != old_image_config.get("Healthcheck") else None

        port_bindings: dict[str, list] = {}
        for container_port, bindings in (host.get("PortBindings") or {}).items():
            port_bindings[container_port] = [
                (binding.get("HostIp"), binding.get("HostPort")) if binding.get("HostIp") else binding.get("HostPort")
                for binding in (bindings or [])
            ]
        exposed = []
        for port in set(list((config.get("ExposedPorts") or {}).keys()) + list(port_bindings.keys())):
            number, _, protocol = port.partition("/")
            exposed.append((int(number), protocol or "tcp"))
        mounts = []
        for mount in host.get("Mounts") or []:
            mounts.append(self._docker.types.Mount(
                target=mount["Target"], source=mount.get("Source"), type=mount.get("Type", "volume"), read_only=mount.get("ReadOnly", False),
            ))

        host_config = api.create_host_config(
            port_bindings=port_bindings or None,
            binds=host.get("Binds") or None,
            mounts=mounts or None,
            restart_policy=host.get("RestartPolicy") or None,
            network_mode=host.get("NetworkMode") or None,
            extra_hosts=host.get("ExtraHosts") or None,
            log_config=host.get("LogConfig") or None,
            mem_limit=host.get("Memory") or None,
            nano_cpus=host.get("NanoCpus") or None,
        )
        network_names = list(networks.keys())
        networking_config = None
        primary = host.get("NetworkMode") if host.get("NetworkMode") in network_names else (network_names[0] if network_names else None)
        short_id = old.id[:12]

        def aliases(network: str) -> list[str]:
            return [alias for alias in (networks.get(network, {}).get("Aliases") or []) if alias != short_id]

        if primary and primary not in {"host", "none", "bridge"}:
            networking_config = api.create_networking_config({primary: api.create_endpoint_config(aliases=aliases(primary))})

        name = old.name
        backup_name = f"{name}-nexops-replaced-{int(time.time())}"
        old.stop(timeout=15)
        old.rename(backup_name)
        try:
            created = api.create_container(
                image=image, name=name, environment=environment, labels=labels, command=command, entrypoint=entrypoint,
                ports=exposed or None, host_config=host_config, networking_config=networking_config, healthcheck=healthcheck,
            )
            new_id = created["Id"]
            for network in network_names:
                if network != primary and network not in {"host", "none"}:
                    api.connect_container_to_network(new_id, network, aliases=aliases(network))
            api.start(new_id)
            deadline = time.time() + 30
            while time.time() < deadline:
                status = api.inspect_container(new_id)["State"]["Status"]
                if status == "running":
                    break
                if status in {"exited", "dead"}:
                    raise IntegrationError(f"Replacement container for {name} exited immediately.")
                time.sleep(1)
        except Exception:
            # Restore the previous container so a failed rollback never leaves the service without a workload.
            try:
                self.client.containers.get(name).remove(force=True)
            except Exception:
                pass
            old.rename(name)
            old.start()
            raise
        old.remove(force=True)
        return name
