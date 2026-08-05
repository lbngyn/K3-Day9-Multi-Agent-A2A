from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from .contracts import (CaseHeader, DeliveryScope, Handoff, OrderSellerScope,
                        PaymentScope, VerificationScope)
from .openrouter import OpenRouterClient
from .prompt_registry import get_agent_system_prompt

CENT = Decimal("0.01")


def money(value: Decimal) -> float:
    return float(value.quantize(CENT, rounding=ROUND_HALF_UP))


def timestamp(value: str | None):
    return datetime.fromisoformat(value) if value else None


class ModelBackedAgent:
    name = ""

    def __init__(self, client: OpenRouterClient | None = None):
        self.client = client

    def consult(self, payload: dict) -> dict | None:
        if not self.client or not self.client.enabled:
            return None
        try:
            return self.client.complete_json(
                agent_id=self.name,
                system=get_agent_system_prompt(self.name),
                payload=payload,
            )
        except Exception as exc:
            # Trusted facts are computed locally. A provider/model formatting
            # failure is auditable but must not abort all 50 cases.
            return {"openrouter_error": str(exc), "fallback": "deterministic_guard"}


class OrderSellerAgent(ModelBackedAgent):
    name = "order_seller_agent"

    def run(self, ctx: OrderSellerScope) -> Handoff:
        carrier_at = timestamp(ctx.delivered_carrier_date)
        late_sellers = sorted({i.seller_id for i in ctx.items
                               if carrier_at and carrier_at > timestamp(i.shipping_limit_date)})
        facts = {
            "order_status": ctx.order_status,
            "late_seller_ids": late_sellers,
            "item_total_brl": money(sum((Decimal(i.price) for i in ctx.items), Decimal("0"))),
            "freight_total_brl": money(sum((Decimal(i.freight_value) for i in ctx.items), Decimal("0"))),
            "item_ids": [f'{ctx.header.order_id}:{i.order_item_id}' for i in ctx.items][:5],
            "seller_ids": sorted({i.seller_id for i in ctx.items})[:5],
        }
        evidence = (
            [f"order:{ctx.header.order_id}"]
            + [f'item:{ctx.header.order_id}:{i.order_item_id}' for i in ctx.items][:5]
            + [f"seller:{s}" for s in late_sellers][:3]
        )
        return Handoff(self.name, ctx.header.case_id, facts, evidence, self.consult(facts))


class PaymentAgent(ModelBackedAgent):
    name = "payment_agent"

    def run(self, ctx: PaymentScope) -> Handoff:
        total = sum((Decimal(p.payment_value) for p in ctx.payments), Decimal("0"))
        expected = Decimal(ctx.expected_order_total_brl)
        facts = {
            "payment_total_brl": money(total), "payment_count": len(ctx.payments),
            "payment_reconciled": abs(total - expected) <= Decimal("0.10"),
            "payment_ids": [f'{ctx.header.order_id}:{p.payment_sequential}' for p in ctx.payments][:5],
        }
        evidence = [f'payment:{ctx.header.order_id}:{p.payment_sequential}' for p in ctx.payments][:5]
        return Handoff(self.name, ctx.header.case_id, facts, evidence, self.consult(facts))


class DeliveryAgent(ModelBackedAgent):
    name = "delivery_agent"

    def run(self, ctx: DeliveryScope) -> Handoff:
        delivered = timestamp(ctx.delivered_customer_date)
        estimated = timestamp(ctx.estimated_delivery_date)
        facts = {
            "delivered_at": ctx.delivered_customer_date,
            "estimated_at": ctx.estimated_delivery_date,
            "delivered_late": bool(delivered and estimated and delivered > estimated),
        }
        return Handoff(self.name, ctx.header.case_id, facts, [f"order:{ctx.header.order_id}"], self.consult(facts))


class PolicyAgent(ModelBackedAgent):
    name = "policy_agent"

    def run(self, ctx: CaseHeader, facts: dict) -> Handoff:
        if ctx.policy_version != "EC_POLICY_V1":
            raise ValueError(f"Unsupported policy: {ctx.policy_version}")
        status, paid = facts["order_status"], facts["payment_total_brl"] > 0
        if status == "canceled" and paid:
            decision = ("canceled_order_paid", "ORDER_CANCELED_AFTER_PAYMENT", "platform", "OLIST_PLATFORM", facts["payment_total_brl"], "issue_full_refund")
        elif status == "unavailable" and paid:
            decision = ("unavailable_order_paid", "ORDER_UNAVAILABLE_AFTER_PAYMENT", "platform", "OLIST_PLATFORM", facts["payment_total_brl"], "issue_full_refund")
        elif facts["delivered_late"] and facts["late_seller_ids"]:
            decision = ("late_delivery_seller", "SELLER_HANDOFF_AFTER_LIMIT", "seller", facts["late_seller_ids"][0], facts["freight_total_brl"], "refund_freight")
        elif facts["delivered_late"]:
            decision = ("late_delivery_logistics", "CARRIER_DELIVERED_AFTER_ESTIMATE", "logistics_provider", "LOGISTICS_PROVIDER", facts["freight_total_brl"], "refund_freight")
        elif facts["payment_count"] >= 2 and facts["payment_reconciled"]:
            decision = ("valid_split_payment", "MULTIPLE_PAYMENTS_RECONCILED", None, None, 0.0, "explain_valid_split_payment")
        elif not facts["delivered_late"] and facts["payment_reconciled"]:
            decision = ("unsupported_late_claim", "DELIVERY_WITHIN_ESTIMATE", None, None, 0.0, "reject_late_refund")
        else:
            raise ValueError(f"Case {ctx.case_id} does not match EC_POLICY_V1")
        issue, cause, party_type, party_id, refund, action = decision
        parties = [] if party_type is None else [{"party_type": party_type, "party_id": party_id}]
        decision_facts = {
            "primary_issue": issue, "cause_code": cause, "responsible_parties": parties,
            "recommended_refund_brl": money(Decimal(str(refund))), "action": action,
        }
        review_payload = {"specialist_facts": facts, "proposed_decision": decision_facts}
        return Handoff(self.name, ctx.case_id, decision_facts, [f"policy:{cause}"], self.consult(review_payload))


class CoordinatorAgent(ModelBackedAgent):
    name = "coordinator_agent"

    DELEGATES = {"order_seller_agent", "payment_agent", "delivery_agent"}

    def plan(self, ctx: CaseHeader) -> dict:
        """Ask the coordinator once for a sequential specialist execution plan."""
        plan = self.consult({
            "case": {"case_id": ctx.case_id, "order_id": ctx.order_id,
                     "policy_version": ctx.policy_version, "order_status": ctx.order_status},
            "available_specialists": sorted(self.DELEGATES),
        })
        if plan is None:
            raise RuntimeError("Coordinator planning requires an enabled OpenRouter client")
        if plan.get("openrouter_error"):
            agents = ["order_seller_agent", "payment_agent"]
            if ctx.order_status not in {"canceled", "unavailable"}:
                agents.append("delivery_agent")
            return {
                "agents": agents,
                "reason": "OpenRouter plan unavailable; used safe status-based fallback",
                "model_error": plan["openrouter_error"],
            }
        agents = plan.get("agents")
        if not isinstance(agents, list) or not agents:
            raise ValueError("Coordinator plan must contain a non-empty 'agents' list")
        if len(agents) != len(set(agents)):
            raise ValueError("Coordinator plan contains duplicate agents")
        unknown = set(agents) - self.DELEGATES
        if unknown:
            raise ValueError(f"Coordinator plan contains unknown agents: {sorted(unknown)}")
        required = {"order_seller_agent", "payment_agent"}
        if ctx.order_status not in {"canceled", "unavailable"}:
            required.add("delivery_agent")
        missing = required - set(agents)
        if missing:
            raise ValueError(f"Coordinator plan misses required agents: {sorted(missing)}")
        return plan

    def build_result(self, ctx: CaseHeader, handoffs: list[Handoff]) -> dict:
        facts = {k: v for handoff in handoffs for k, v in handoff.facts.items()}
        evidence = list(dict.fromkeys(e for h in handoffs for e in h.evidence_ids))[:10]
        refund = facts["recommended_refund_brl"]
        result = {
            "case_id": ctx.case_id,
            "assessment": {"primary_issue": facts["primary_issue"],
                           "case_status": "action_required" if refund > 0 else "no_action",
                           "confidence": 1.0},
            "affected_entities": {"order_ids": [ctx.order_id], "item_ids": facts["item_ids"],
                                  "seller_ids": facts["seller_ids"], "payment_ids": facts["payment_ids"]},
            "root_cause_analysis": {"ranked_causes": [{"cause_code": facts["cause_code"], "rank": 1}],
                                    "responsible_parties": facts["responsible_parties"]},
            "evidence_ids": evidence,
            "financial_resolution": {"currency": "BRL", "item_total_brl": facts["item_total_brl"],
                                     "freight_total_brl": facts["freight_total_brl"],
                                     "payment_total_brl": facts["payment_total_brl"],
                                     "recommended_refund_brl": refund},
            "resolution_actions": [facts["action"]],
        }
        return result


class VerifierAgent(ModelBackedAgent):
    name = "verifier_agent"
    ISSUES = {"canceled_order_paid", "unavailable_order_paid", "late_delivery_seller",
              "late_delivery_logistics", "valid_split_payment", "unsupported_late_claim"}

    def run(self, ctx: VerificationScope, result: dict) -> None:
        self.last_model_analysis = self.consult({"case_id": ctx.header.case_id, "draft": result})
        assert result["case_id"] == ctx.header.case_id
        assert result["assessment"]["primary_issue"] in self.ISSUES
        assert 0 <= result["assessment"]["confidence"] <= 1
        assert len(result["evidence_ids"]) <= 10
        assert all(v >= 0 for k, v in result["financial_resolution"].items() if k.endswith("_brl"))
        assert set(result["evidence_ids"]) <= ctx.valid_evidence_ids
