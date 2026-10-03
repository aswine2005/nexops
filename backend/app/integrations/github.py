"""GitHub REST API adapter: commit/diff context for deployments and workflow dispatch for rollbacks."""
from __future__ import annotations

import httpx

from app.integrations.types import IntegrationError


class GitHubClient:
    def __init__(self, token: str, api_url: str = "https://api.github.com", timeout: float = 10.0) -> None:
        self.api_url = api_url.rstrip("/")
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=4.0),
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "nexops",
            },
        )

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            response = self._client.request(method, f"{self.api_url}{path}", **kwargs)
        except httpx.HTTPError as error:
            raise IntegrationError(f"GitHub request failed: {error}") from error
        if response.status_code >= 400:
            message = response.json().get("message", response.text[:200]) if response.headers.get("content-type", "").startswith("application/json") else response.text[:200]
            raise IntegrationError(f"GitHub API {response.status_code}: {message}")
        return response

    def get_commit(self, repository: str, sha: str) -> dict:
        data = self._request("GET", f"/repos/{repository}/commits/{sha}").json()
        return self._commit_summary(data)

    def compare(self, repository: str, base: str, head: str) -> dict:
        data = self._request("GET", f"/repos/{repository}/compare/{base}...{head}").json()
        files = data.get("files", [])
        commits = data.get("commits", [])
        return {
            "files": [self._file(item) for item in files],
            "commits": [{"sha": item["sha"], "message": item["commit"]["message"], "author": item["commit"]["author"]["name"]} for item in commits],
        }

    def dispatch_workflow(self, repository: str, workflow: str, ref: str, inputs: dict[str, str]) -> None:
        self._request("POST", f"/repos/{repository}/actions/workflows/{workflow}/dispatches", json={"ref": ref, "inputs": inputs})

    def rate_limit(self) -> dict:
        return self._request("GET", "/rate_limit").json().get("resources", {}).get("core", {})

    @staticmethod
    def _file(item: dict) -> dict:
        return {
            "filename": item.get("filename"),
            "status": item.get("status", "modified"),
            "additions": item.get("additions", 0),
            "deletions": item.get("deletions", 0),
            "patch": (item.get("patch") or "")[:6000] or None,
        }

    def _commit_summary(self, data: dict) -> dict:
        commit = data.get("commit", {})
        return {
            "sha": data.get("sha"),
            "message": commit.get("message"),
            "author": (data.get("author") or {}).get("login") or commit.get("author", {}).get("name"),
            "date": commit.get("committer", {}).get("date") or commit.get("author", {}).get("date"),
            "stats": data.get("stats", {}),
            "files": [self._file(item) for item in data.get("files", [])],
        }
