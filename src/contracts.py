from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CaseContext:
    case_id: str
    order_id: str
    policy_version: str
    order: dict[str, str]
    items: list[dict[str, str]]
    payments: list[dict[str, str]]


@dataclass(frozen=True)
class Handoff:
    agent: str
    case_id: str
    facts: dict[str, Any]
    evidence_ids: list[str]
    model_analysis: dict[str, Any] | None = None
