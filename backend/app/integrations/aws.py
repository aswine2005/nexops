"""AWS adapter: CloudWatch metrics and logs, EC2 and ECR inventory. Uses the standard AWS credential chain."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.integrations.types import IntegrationError, LogLine, parse_log_line


class AwsClient:
    def __init__(self, region: str) -> None:
        try:
            import boto3  # noqa: PLC0415 - optional dependency
            from botocore.config import Config  # noqa: PLC0415
        except ImportError as error:  # pragma: no cover
            raise IntegrationError("The 'boto3' Python package is not installed.") from error
        config = Config(region_name=region, retries={"max_attempts": 3, "mode": "standard"}, connect_timeout=4, read_timeout=10)
        self.region = region
        self._session = boto3.session.Session(region_name=region)
        self._config = config
        self._clients: dict[str, Any] = {}

    def _client(self, name: str) -> Any:
        if name not in self._clients:
            self._clients[name] = self._session.client(name, config=self._config)
        return self._clients[name]

    def identity(self) -> dict:
        try:
            return self._client("sts").get_caller_identity()
        except Exception as error:
            raise IntegrationError(f"AWS credentials are not usable: {error}") from error

    def ec2_cpu(self, instance_id: str, minutes: int = 5) -> float | None:
        end = datetime.now(timezone.utc)
        try:
            response = self._client("cloudwatch").get_metric_data(
                MetricDataQueries=[{
                    "Id": "cpu",
                    "MetricStat": {
                        "Metric": {"Namespace": "AWS/EC2", "MetricName": "CPUUtilization", "Dimensions": [{"Name": "InstanceId", "Value": instance_id}]},
                        "Period": 60,
                        "Stat": "Average",
                    },
                }],
                StartTime=end - timedelta(minutes=minutes),
                EndTime=end,
                ScanBy="TimestampDescending",
            )
        except Exception as error:
            raise IntegrationError(f"CloudWatch GetMetricData failed: {error}") from error
        values = response["MetricDataResults"][0].get("Values", [])
        return round(values[0], 2) if values else None

    def logs(self, log_group: str, start: datetime, end: datetime, service: str, filter_pattern: str | None = None, limit: int = 200) -> list[LogLine]:
        kwargs: dict[str, Any] = {
            "logGroupName": log_group,
            "startTime": int(start.timestamp() * 1000),
            "endTime": int(end.timestamp() * 1000),
            "limit": min(limit, 10000),
        }
        if filter_pattern:
            kwargs["filterPattern"] = filter_pattern
        lines: list[LogLine] = []
        try:
            paginator = self._client("logs").get_paginator("filter_log_events")
            for page in paginator.paginate(**kwargs, PaginationConfig={"MaxItems": limit * 3}):
                for event in page.get("events", []):
                    timestamp = datetime.fromtimestamp(event["timestamp"] / 1000, tz=timezone.utc)
                    lines.append(parse_log_line(event.get("message", ""), timestamp, event.get("logStreamName", log_group), service))
        except Exception as error:
            raise IntegrationError(f"CloudWatch Logs query failed: {error}") from error
        lines.sort(key=lambda line: line.timestamp, reverse=True)
        return lines[:limit]

    def ec2_instances(self) -> list[dict]:
        try:
            reservations = self._client("ec2").describe_instances(
                Filters=[{"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}],
            )["Reservations"]
        except Exception as error:
            raise IntegrationError(f"EC2 DescribeInstances failed: {error}") from error
        instances = []
        for reservation in reservations:
            for instance in reservation.get("Instances", []):
                tags = {tag["Key"]: tag["Value"] for tag in instance.get("Tags", [])}
                instances.append({
                    "id": instance["InstanceId"],
                    "name": tags.get("Name", instance["InstanceId"]),
                    "type": instance.get("InstanceType"),
                    "state": instance.get("State", {}).get("Name"),
                    "zone": instance.get("Placement", {}).get("AvailabilityZone"),
                    "private_ip": instance.get("PrivateIpAddress"),
                    "public_ip": instance.get("PublicIpAddress"),
                    "launched_at": instance["LaunchTime"].isoformat() if instance.get("LaunchTime") else None,
                    "tags": tags,
                })
        return instances

    def ecr_repositories(self) -> list[dict]:
        client = self._client("ecr")
        try:
            repositories = client.describe_repositories().get("repositories", [])
        except Exception as error:
            raise IntegrationError(f"ECR DescribeRepositories failed: {error}") from error
        result = []
        for repository in repositories:
            images = []
            try:
                details = client.describe_images(repositoryName=repository["repositoryName"], maxResults=100).get("imageDetails", [])
                details.sort(key=lambda item: item.get("imagePushedAt") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
                images = [
                    {"tags": item.get("imageTags", []), "pushed_at": item["imagePushedAt"].isoformat() if item.get("imagePushedAt") else None, "size_mb": round(item.get("imageSizeInBytes", 0) / 1e6, 1)}
                    for item in details[:5]
                ]
            except Exception:
                pass
            result.append({"name": repository["repositoryName"], "uri": repository["repositoryUri"], "images": images})
        return result

    def log_groups(self, prefix: str | None = None) -> list[dict]:
        kwargs = {"limit": 50}
        if prefix:
            kwargs["logGroupNamePrefix"] = prefix
        try:
            groups = self._client("logs").describe_log_groups(**kwargs).get("logGroups", [])
        except Exception as error:
            raise IntegrationError(f"CloudWatch DescribeLogGroups failed: {error}") from error
        return [{"name": group["logGroupName"], "stored_mb": round(group.get("storedBytes", 0) / 1e6, 1), "retention_days": group.get("retentionInDays")} for group in groups]
