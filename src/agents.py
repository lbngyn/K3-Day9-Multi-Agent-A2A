from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from .contracts import CaseContext, Handoff
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
        return self.client.complete_json(
            agent_id=self.name,
            system=get_agent_system_prompt(self.name),
            payload=payload,
        )


class OrderSellerAgent(ModelBackedAgent):
    name = "order_seller_agent"

    def run(self, ctx: CaseContext) -> Handoff:
        carrier_at = timestamp(ctx.order.get("order_delivered_carrier_date"))
        late_sellers = sorted({i["seller_id"] for i in ctx.items
                               if carrier_at and carrier_at > timestamp(i["shipping_limit_date"])})
        facts = {
            "order_status": ctx.order["order_status"],
            "late_seller_ids": late_sellers,
            "item_total_brl": money(sum((Decimal(i["price"]) for i in ctx.items), Decimal("0"))),
            "freight_total_brl": money(sum((Decimal(i["freight_value"]) for i in ctx.items), Decimal("0"))),
            "item_ids": [f'{ctx.order_id}:{i["order_item_id"]}' for i in ctx.items][:5],
            "seller_ids": sorted({i["seller_id"] for i in ctx.items})[:5],
        }
        evidence = (
            [f"order:{ctx.order_id}"]
            + [f'item:{ctx.order_id}:{i["order_item_id"]}' for i in ctx.items][:5]
            + [f"seller:{s}" for s in late_sellers][:3]
        )
        return Handoff(self.name, ctx.case_id, facts, evidence, self.consult(facts))


class PaymentAgent(ModelBackedAgent):
    name = "payment_agent"

    def run(self, ctx: CaseContext) -> Handoff:
        total = sum((Decimal(p["payment_value"]) for p in ctx.payments), Decimal("0"))
        expected = sum((Decimal(i["price"]) + Decimal(i["freight_value"]) for i in ctx.items), Decimal("0"))
        facts = {
            "payment_total_brl": money(total), "payment_count": len(ctx.payments),
            "payment_reconciled": abs(total - expected) <= Decimal("0.10"),
            "payment_ids": [f'{ctx.order_id}:{p["payment_sequential"]}' for p in ctx.payments][:5],
        }
        evidence = [f'payment:{ctx.order_id}:{p["payment_sequential"]}' for p in ctx.payments][:5]
        return Handoff(self.name, ctx.case_id, facts, evidence, self.consult(facts))


class DeliveryAgent(ModelBackedAgent):
    name = "delivery_agent"

    def run(self, ctx: CaseContext) -> Handoff:
        delivered = timestamp(ctx.order.get("order_delivered_customer_date"))
        estimated = timestamp(ctx.order.get("order_estimated_delivery_date"))
        facts = {
            "delivered_at": ctx.order.get("order_delivered_customer_date"),
            "estimated_at": ctx.order.get("order_estimated_delivery_date"),
            "delivered_late": bool(delivered and estimated and delivered > estimated),
        }
        return Handoff(self.name, ctx.case_id, facts, [f"order:{ctx.order_id}"], self.consult(facts))


class PolicyAgent(ModelBackedAgent):
    name = "policy_agent"

    def run(self, ctx: CaseContext, facts: dict) -> Handoff:
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

    ACTIONS = {"delegate", "apply_policy", "build_draft", "verify", "finalize"}
    DELEGATES = {"order_seller_agent", "payment_agent", "delivery_agent"}

    def plan(self, ctx: CaseContext, state: dict) -> dict:
        """Ask the coordinator LLM for exactly one next orchestration command."""
        command = self.consult({
            "case": {"case_id": ctx.case_id, "order_id": ctx.order_id,
                     "policy_version": ctx.policy_version},
            "available_agents": sorted(self.DELEGATES | {"policy_agent", "verifier_agent"}),
            "state": state,
        })
        if command is None:
            raise RuntimeError("Coordinator planning requires an enabled OpenRouter client")
        action = command.get("action")
        if action not in self.ACTIONS:
            raise ValueError(f"Coordinator returned invalid action: {action!r}")
        if action == "delegate" and command.get("target_agent") not in self.DELEGATES:
            raise ValueError(f"Coordinator returned invalid delegate: {command.get('target_agent')!r}")
        return command

    def build_result(self, ctx: CaseContext, handoffs: list[Handoff]) -> dict:
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

    def run(self, ctx: CaseContext, result: dict) -> None:
        self.last_model_analysis = self.consult({"case_id": ctx.case_id, "draft": result})
        assert result["case_id"] == ctx.case_id
        assert result["assessment"]["primary_issue"] in self.ISSUES
        assert 0 <= result["assessment"]["confidence"] <= 1
        assert len(result["evidence_ids"]) <= 10
        assert all(v >= 0 for k, v in result["financial_resolution"].items() if k.endswith("_brl"))
        valid = {f"order:{ctx.order_id}", *(f'item:{ctx.order_id}:{i["order_item_id"]}' for i in ctx.items),
                 *(f'payment:{ctx.order_id}:{p["payment_sequential"]}' for p in ctx.payments),
                 *(f'seller:{i["seller_id"]}' for i in ctx.items),
                 *(f'policy:{x}' for x in ["SELLER_HANDOFF_AFTER_LIMIT", "CARRIER_DELIVERED_AFTER_ESTIMATE",
                    "ORDER_CANCELED_AFTER_PAYMENT", "ORDER_UNAVAILABLE_AFTER_PAYMENT",
                    "MULTIPLE_PAYMENTS_RECONCILED", "DELIVERY_WITHIN_ESTIMATE"])}
        assert set(result["evidence_ids"]) <= valid
