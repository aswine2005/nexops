"""Groq LLM client (OpenAI-compatible Chat Completions) with structured JSON output."""
from __future__ import annotations

import json

import httpx

from app.integrations.types import IntegrationError

# Models that Groq documents as supporting `json_schema` with `strict: true`.
# Every other model falls back to JSON-object mode with the schema embedded in the prompt.
STRICT_SCHEMA_MODELS = {"openai/gpt-oss-120b", "openai/gpt-oss-20b"}


class GroqClient:
    provider = "groq"

    def __init__(self, api_key: str, model: str, base_url: str = "https://api.groq.com/openai/v1", timeout: float = 60.0) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _uses_strict_schema(self) -> bool:
        return self.model in STRICT_SCHEMA_MODELS or self.model.startswith("qwen/")

    def structured(self, system: str, payload: dict, schema_name: str, schema: dict, temperature: float = 0.2) -> dict:
        """Ask the model for a JSON object that matches `schema`. The caller still validates the result."""
        if self._uses_strict_schema():
            response_format: dict = {"type": "json_schema", "json_schema": {"name": schema_name, "strict": True, "schema": schema}}
            system_prompt = system
        else:
            response_format = {"type": "json_object"}
            system_prompt = f"{system}\n\nRespond with a single JSON object that matches this JSON Schema exactly:\n{json.dumps(schema)}"
        body = {
            "model": self.model,
            "temperature": temperature,
            "max_completion_tokens": 4096,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
            ],
            "response_format": response_format,
        }
        try:
            with httpx.Client(timeout=httpx.Timeout(self.timeout, connect=5.0)) as client:
                response = client.post(f"{self.base_url}/chat/completions", headers={"Authorization": f"Bearer {self.api_key}"}, json=body)
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except httpx.HTTPStatusError as error:
            raise IntegrationError(f"Groq returned HTTP {error.response.status_code}: {error.response.text[:300]}") from error
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as error:
            raise IntegrationError(f"Groq request failed: {error.__class__.__name__}: {error}") from error
        if not isinstance(parsed, dict):
            raise IntegrationError("Groq returned a non-object JSON response")
        return parsed

    def ping(self) -> bool:
        try:
            with httpx.Client(timeout=8.0) as client:
                response = client.get(f"{self.base_url}/models", headers={"Authorization": f"Bearer {self.api_key}"})
            return response.status_code == 200
        except httpx.HTTPError:
            return False
