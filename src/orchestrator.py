from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .agents import CoordinatorAgent, DeliveryAgent, OrderSellerAgent, PaymentAgent, PolicyAgent
from .config import get_agent_model_config
from .contracts import CaseScopes, Handoff
from .data_store import OlistStore
from .openrouter import OpenRouterClient
from .validation import validate_result


class DisputeOrchestrator:
    """Adaptive, sequential routing controlled by one Coordinator LLM."""

    MAX_TURNS = 8
    MAX_REROUTES = 1

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
    def _scope_for(scopes: CaseScopes, agent_name: str):
        return {
            "order_seller_agent": scopes.order_seller,
            "payment_agent": scopes.payment,
            "delivery_agent": scopes.delivery,
        }[agent_name]

    @staticmethod
    def _combined_facts(handoffs: dict[str, Handoff]) -> dict:
        return {key: value for handoff in handoffs.values()
                for key, value in handoff.facts.items()}

    @staticmethod
    def _state(handoffs: dict[str, Handoff], last_error: str | None,
               reroutes_used: int) -> dict:
        return {
            "completed_agents": sorted(handoffs),
            "handoffs": {
                name: {"facts": h.facts, "evidence_ids": h.evidence_ids,
                       "model_analysis": h.model_analysis}
                for name, h in handoffs.items()
            },
            "last_error": last_error,
            "reroutes_used": reroutes_used,
            "reroutes_remaining": DisputeOrchestrator.MAX_REROUTES - reroutes_used,
        }

    def run_case(self, case_path: Path) -> dict:
        scopes = self.store.context(case_path)
        if self.client and self.client.enabled:
            return self._run_adaptive(scopes)
        return self._run_offline(scopes)

    def _run_offline(self, scopes: CaseScopes) -> dict:
        """Reproducible sequential fallback without LLM routing."""
        names = ["order_seller_agent", "payment_agent"]
        if scopes.header.order_status not in {"canceled", "unavailable"}:
            names.append("delivery_agent")
        handoffs = {}
        for name in names:
            handoff = self.specialists[name].run(self._scope_for(scopes, name))
            handoffs[name] = handoff
            self._trace_handoff(handoff)
        policy = self.policy.run(scopes.header, self._combined_facts(handoffs))
        handoffs["policy_agent"] = policy
        self._trace_handoff(policy)
        result = self.coordinator.build_result(scopes.header, list(handoffs.values()))
        validate_result(scopes.verification, result)
        self._trace(scopes.header.case_id, self.coordinator.name, "result_checked", {
            "valid": True, "mode": "deterministic_guard",
        })
        return result

    def _complete_with_fallback(self, scopes: CaseScopes,
                                handoffs: dict[str, Handoff], reason: str) -> dict:
        """Finish safely when the one allowed corrective re-route is exhausted."""
        self._trace(scopes.header.case_id, self.coordinator.name, "fallback_activated", {
            "reason": reason, "completed_agents": sorted(handoffs),
        })
        required = ["order_seller_agent", "payment_agent"]
        if scopes.header.order_status not in {"canceled", "unavailable"}:
            required.append("delivery_agent")
        for name in required:
            if name in handoffs:
                continue
            handoff = self.specialists[name].run(
                self._scope_for(scopes, name), "Fallback: collect required evidence"
            )
            handoffs[name] = handoff
            self._trace_handoff(handoff)

        # Recompute policy from the complete fallback fact set. Any earlier,
        # premature policy handoff is deliberately replaced.
        specialist_handoffs = {
            name: handoffs[name] for name in required
        }
        policy = self.policy.run(
            scopes.header, self._combined_facts(specialist_handoffs),
            "Fallback: apply policy to complete evidence",
        )
        handoffs["policy_agent"] = policy
        self._trace_handoff(policy)
        result = self.coordinator.build_result(
            scopes.header, list(specialist_handoffs.values()) + [policy]
        )
        validate_result(scopes.verification, result)
        self._trace(scopes.header.case_id, self.coordinator.name, "result_checked", {
            "valid": True, "mode": "fallback", "reason": reason,
        })
        return result

    def _run_adaptive(self, scopes: CaseScopes) -> dict:
        handoffs: dict[str, Handoff] = {}
        last_error: str | None = None
        reroutes_used = 0

        def reject_route(error: str) -> bool:
            nonlocal last_error, reroutes_used
            if reroutes_used >= self.MAX_REROUTES:
                last_error = error
                return False
            reroutes_used += 1
            last_error = error
            return True

        for turn in range(1, self.MAX_TURNS + 1):
            state = self._state(handoffs, last_error, reroutes_used)
            try:
                decision = self.coordinator.plan(scopes.header, state)
            except ValueError as exc:
                if not reject_route(str(exc)):
                    return self._complete_with_fallback(scopes, handoffs, str(exc))
                self._trace(scopes.header.case_id, self.coordinator.name, "route_rejected", {
                    "turn": turn, "error": last_error, "reroutes_used": reroutes_used,
                })
                continue

            self._trace(scopes.header.case_id, self.coordinator.name, "route_decision", {
                "turn": turn, "decision": decision,
            })

            if decision["action"] == "delegate":
                target = decision["target_agent"]
                if target in handoffs:
                    error = f"{target} already completed; inspect its handoff or choose another domain"
                    if not reject_route(error):
                        return self._complete_with_fallback(scopes, handoffs, error)
                    continue

                if target == "policy_agent":
                    facts = self._combined_facts(handoffs)
                    required = {"order_status", "payment_total_brl"}
                    if scopes.header.order_status not in {"canceled", "unavailable"}:
                        required |= {"delivered_late", "late_seller_ids"}
                    missing = required - set(facts)
                    if missing:
                        error = f"policy_agent is premature; missing facts: {sorted(missing)}"
                        if not reject_route(error):
                            return self._complete_with_fallback(scopes, handoffs, error)
                        continue
                    handoff = self.policy.run(
                        scopes.header, facts, decision.get("task", "Apply policy")
                    )
                else:
                    handoff = self.specialists[target].run(
                        self._scope_for(scopes, target),
                        decision.get("task", "Collect relevant evidence"),
                    )
                handoffs[target] = handoff
                self._trace_handoff(handoff)
                last_error = None
                continue

            if "policy_agent" not in handoffs:
                error = "cannot finalize before policy_agent returns a supported decision"
                if not reject_route(error):
                    return self._complete_with_fallback(scopes, handoffs, error)
                continue
            try:
                result = self.coordinator.build_result(scopes.header, list(handoffs.values()))
                validate_result(scopes.verification, result)
            except (AssertionError, KeyError, ValueError) as exc:
                error = f"final result check failed: {exc}"
                if not reject_route(error):
                    return self._complete_with_fallback(scopes, handoffs, error)
                self._trace(scopes.header.case_id, self.coordinator.name, "result_rejected", {
                    "turn": turn, "error": last_error, "reroutes_used": reroutes_used,
                })
                continue
            self._trace(scopes.header.case_id, self.coordinator.name, "result_checked", {
                "turn": turn, "valid": True, "reason": decision.get("reason", ""),
            })
            return result

        return self._complete_with_fallback(
            scopes, handoffs,
            f"Coordinator reached {self.MAX_TURNS} turns; last_error={last_error!r}",
        )
