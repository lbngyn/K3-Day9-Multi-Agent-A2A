from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .agents import (CoordinatorAgent, DeliveryAgent, OrderSellerAgent, PaymentAgent,
                     PolicyAgent, VerifierAgent)
from .config import get_agent_model_config
from .contracts import Handoff
from .data_store import OlistStore
from .openrouter import OpenRouterClient


class DisputeOrchestrator:
    """Execute either an LLM-directed command loop or a reproducible offline flow."""

    MAX_TURNS = 15
    MAX_PLAN_ERRORS = 3

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

    @staticmethod
    def _planner_state(handoffs: dict[str, Handoff], policy: Handoff | None,
                       draft: dict | None, verified: bool, last_error: str | None,
                       turn: int) -> dict:
        return {
            "turn": turn,
            "completed_specialists": sorted(handoffs),
            "specialist_handoffs": {name: h.facts for name, h in handoffs.items()},
            "policy_completed": policy is not None,
            "policy_facts": policy.facts if policy else None,
            "draft_built": draft is not None,
            "verification_passed": verified,
            "last_error": last_error,
        }

    def run_case(self, case_path: Path) -> dict:
        ctx = self.store.context(case_path)
        if self.client and self.client.enabled:
            return self._run_llm_orchestrated(ctx)
        return self._run_offline(ctx)

    def _run_offline(self, ctx) -> dict:
        """Deterministic fallback for tests and environments without an API key."""
        handoffs = {name: agent.run(ctx) for name, agent in self.specialists.items()}
        for handoff in handoffs.values():
            self._trace_handoff(handoff)
        policy = self.policy.run(ctx, self._combined_facts(handoffs))
        self._trace_handoff(policy)
        result = self.coordinator.build_result(ctx, list(handoffs.values()) + [policy])
        self._trace(ctx.case_id, self.coordinator.name, "offline_build_draft", {"result": result})
        self.verifier.run(ctx, result)
        self._trace(ctx.case_id, self.verifier.name, "verified", {
            "valid": True, "model_analysis": self.verifier.last_model_analysis,
        })
        return result

    def _run_llm_orchestrated(self, ctx) -> dict:
        handoffs: dict[str, Handoff] = {}
        policy: Handoff | None = None
        draft: dict | None = None
        verified = False
        last_error: str | None = None
        plan_errors = 0

        for turn in range(1, self.MAX_TURNS + 1):
            state = self._planner_state(handoffs, policy, draft, verified, last_error, turn)
            try:
                command = self.coordinator.plan(ctx, state)
            except (RuntimeError, ValueError) as exc:
                plan_errors += 1
                last_error = str(exc)
                self._trace(ctx.case_id, self.coordinator.name, "invalid_plan", {
                    "turn": turn, "error": last_error,
                })
                if plan_errors >= self.MAX_PLAN_ERRORS:
                    raise RuntimeError(
                        f"Coordinator failed to produce a valid plan for {ctx.case_id}: {last_error}"
                    ) from exc
                continue

            self._trace(ctx.case_id, self.coordinator.name, "orchestration_command", {
                "turn": turn, "command": command, "state_before": state,
            })
            action = command["action"]
            try:
                if action == "delegate":
                    target = command["target_agent"]
                    if target in handoffs:
                        raise ValueError(f"{target} already completed")
                    handoff = self.specialists[target].run(ctx)
                    handoffs[target] = handoff
                    self._trace_handoff(handoff)

                elif action == "apply_policy":
                    missing = set(self.specialists) - set(handoffs)
                    if missing:
                        raise ValueError(f"policy requires specialist handoffs: {sorted(missing)}")
                    if policy is not None:
                        raise ValueError("policy_agent already completed")
                    policy = self.policy.run(ctx, self._combined_facts(handoffs))
                    self._trace_handoff(policy)

                elif action == "build_draft":
                    if policy is None:
                        raise ValueError("build_draft requires policy handoff")
                    if draft is not None:
                        raise ValueError("draft already built")
                    draft = self.coordinator.build_result(ctx, list(handoffs.values()) + [policy])
                    self._trace(ctx.case_id, self.coordinator.name, "draft_built", {"result": draft})

                elif action == "verify":
                    if draft is None:
                        raise ValueError("verify requires a draft")
                    self.verifier.run(ctx, draft)
                    verified = True
                    self._trace(ctx.case_id, self.verifier.name, "verified", {
                        "valid": True, "model_analysis": self.verifier.last_model_analysis,
                    })

                elif action == "finalize":
                    if draft is None or not verified:
                        raise ValueError("finalize requires a verified draft")
                    self._trace(ctx.case_id, self.coordinator.name, "finalized", {
                        "turn": turn, "reason": command.get("reason", ""),
                    })
                    return draft

                last_error = None
            except (AssertionError, KeyError, ValueError) as exc:
                last_error = f"Command {action!r} rejected: {exc}"
                self._trace(ctx.case_id, self.coordinator.name, "command_rejected", {
                    "turn": turn, "command": command, "error": last_error,
                })

        raise RuntimeError(
            f"Coordinator exceeded {self.MAX_TURNS} turns for {ctx.case_id}; "
            f"last_error={last_error!r}"
        )
