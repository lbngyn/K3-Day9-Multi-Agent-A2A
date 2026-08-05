from __future__ import annotations

import json
import os
import re
import urllib.request

from .config import OPENROUTER_URL, get_agent_model_config


class OpenRouterClient:
    """Dependency-free OpenRouter client with defensive structured-output parsing."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def complete_json(self, agent_id: str, system: str, payload: dict) -> dict:
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        agent_config = get_agent_model_config(agent_id)
        request_body = {
            "model": agent_config["model"],
            "temperature": agent_config["temperature"],
            "max_tokens": agent_config["max_tokens"],
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
        result = self._request(request_body)
        try:
            return self._extract_json(result, agent_id)
        except ValueError as first_error:
            # Some reasoning models spend the whole budget before producing
            # message.content. Retry once with hidden/low reasoning and more room.
            retry_body = dict(request_body)
            retry_body["max_tokens"] = max(800, agent_config["max_tokens"] * 2)
            retry_body["reasoning"] = {"effort": "low", "exclude": True}
            retry_body["messages"] = request_body["messages"] + [{
                "role": "user",
                "content": "Return the final JSON object now. No prose or markdown.",
            }]
            retry_result = self._request(retry_body)
            try:
                return self._extract_json(retry_result, agent_id)
            except ValueError as retry_error:
                raise RuntimeError(
                    f"OpenRouter agent '{agent_id}' returned no parseable JSON after retry. "
                    f"First response: {first_error}; retry response: {retry_error}"
                ) from retry_error

    def _request(self, request_body: dict) -> dict:
        body = json.dumps(request_body).encode()
        req = urllib.request.Request(OPENROUTER_URL, data=body, method="POST", headers={
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/vinai/K3-Day9-Multi-Agent-A2A",
            "X-Title": "Olist Multi-Agent Dispute Resolution",
        })
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.load(response)

    @staticmethod
    def _parse_json_text(value: str) -> dict:
        text = value.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
        if fenced:
            text = fenced.group(1)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            if start < 0:
                raise ValueError("response text contains no JSON object")
            try:
                parsed, _ = json.JSONDecoder().raw_decode(text[start:])
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON content: {exc.msg}") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"expected JSON object, got {type(parsed).__name__}")
        return parsed

    @classmethod
    def _extract_json(cls, result: dict, agent_id: str) -> dict:
        if result.get("error"):
            raise ValueError(f"API error: {result['error']}")
        choices = result.get("choices") or []
        if not choices:
            raise ValueError(f"no choices; response keys={sorted(result)}")
        choice = choices[0]
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, dict):
            return content
        if isinstance(content, str) and content.strip():
            return cls._parse_json_text(content)
        for tool_call in message.get("tool_calls") or []:
            arguments = (tool_call.get("function") or {}).get("arguments")
            if isinstance(arguments, dict):
                return arguments
            if isinstance(arguments, str) and arguments.strip():
                return cls._parse_json_text(arguments)
        finish = choice.get("finish_reason")
        reasoning = message.get("reasoning") or choice.get("reasoning")
        detail = "present" if reasoning else "absent"
        raise ValueError(
            f"message.content is empty (finish_reason={finish!r}, reasoning={detail}, agent={agent_id})"
        )
