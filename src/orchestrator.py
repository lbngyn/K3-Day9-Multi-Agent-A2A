from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .agents import (CoordinatorAgent, DeliveryAgent, OrderSellerAgent, PaymentAgent,
                     PolicyAgent, VerifierAgent)
from .config import get_agent_model_config
from .contracts import CaseScopes, Handoff
from .data_store import OlistStore
from .openrouter import OpenRouterClient


class DisputeOrchestrator:
    """Execute one LLM plan as a single sequential agent pipeline."""

    def __init__(self, store: OlistStore, trace_file: Path,
                 client: OpenRouterClient | None = None):
        self.store, self.trace_file, self.client = store, trace_file, client
        self.specialists = {
            "order_seller_agent": OrderSellerAgent(client),
            "payment_agent": PaymentAgent(client),
            "delivery_agent": DeliveryAgent(client),
        }
        self.policy = PolicyAgent(client)
        self.coordinator = CoordinatorAgent(client)
        self.verifier = VerifierAgent(client)

    def _trace(self, case_id: str, agent: str, event: str, payload: dict):
        model_config = get_agent_model_config(agent)
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(), "case_id": case_id,
            "agent": agent, "provider": "OpenRouter",
            "configured_model": model_config["model"], "event": event, "payload": payload,
        }
        with self.trace_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _trace_handoff(self, handoff: Handoff):
        self._trace(handoff.case_id, handoff.agent, "handoff", {
            "facts": handoff.facts, "evidence_ids": handoff.evidence_ids,
            "model_analysis": handoff.model_analysis,
        })

    @staticmethod
    def _combined_facts(handoffs: dict[str, Handoff]) -> dict:
        return {key: value for handoff in handoffs.values() for key, value in handoff.facts.items()}

    def run_case(self, case_path: Path) -> dict:
        scopes = self.store.context(case_path)
        if self.client and self.client.enabled:
            return self._run_llm_orchestrated(scopes)
        return self._run_offline(scopes)

    @staticmethod
    def _scope_for(scopes: CaseScopes, agent_name: str):
        return {
            "order_seller_agent": scopes.order_seller,
            "payment_agent": scopes.payment,
            "delivery_agent": scopes.delivery,
        }[agent_name]

    def _run_offline(self, scopes: CaseScopes) -> dict:
        """Deterministic fallback for tests and environments without an API key."""
        handoffs = {
            name: agent.run(self._scope_for(scopes, name))
            for name, agent in self.specialists.items()
        }
        for handoff in handoffs.values():
            self._trace_handoff(handoff)
        policy = self.policy.run(scopes.header, self._combined_facts(handoffs))
        self._trace_handoff(policy)
        result = self.coordinator.build_result(scopes.header, list(handoffs.values()) + [policy])
        self._trace(scopes.header.case_id, self.coordinator.name, "offline_build_draft", {"result": result})
        self.verifier.run(scopes.verification, result)
        self._trace(scopes.header.case_id, self.verifier.name, "verified", {
            "valid": True, "model_analysis": self.verifier.last_model_analysis,
        })
        return result

    def _run_llm_orchestrated(self, scopes: CaseScopes) -> dict:
        plan = self.coordinator.plan(scopes.header)
        self._trace(scopes.header.case_id, self.coordinator.name, "sequential_plan", plan)

        handoffs: dict[str, Handoff] = {}
        for target in plan["agents"]:
            # One agent at a time; each receives only its scoped projection.
            handoff = self.specialists[target].run(self._scope_for(scopes, target))
            handoffs[target] = handoff
            self._trace_handoff(handoff)

        policy = self.policy.run(scopes.header, self._combined_facts(handoffs))
        self._trace_handoff(policy)
        result = self.coordinator.build_result(
            scopes.header, list(handoffs.values()) + [policy]
        )
        self.verifier.run(scopes.verification, result)
        self._trace(scopes.header.case_id, self.verifier.name, "verified", {
            "valid": True, "model_analysis": self.verifier.last_model_analysis,
        })
        return result
