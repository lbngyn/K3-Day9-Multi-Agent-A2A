from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CaseHeader:
    """Non-sensitive routing metadata visible to Coordinator and Policy."""
    case_id: str
    order_id: str
    policy_version: str
    order_status: str
    customer_language: str
    customer_message: str


@dataclass(frozen=True)
class OrderItemView:
    order_item_id: str
    seller_id: str
    shipping_limit_date: str
    price: str
    freight_value: str


@dataclass(frozen=True)
class OrderSellerScope:
    header: CaseHeader
    order_status: str
    delivered_carrier_date: str | None
    items: tuple[OrderItemView, ...]


@dataclass(frozen=True)
class PaymentView:
    payment_sequential: str
    payment_value: str


@dataclass(frozen=True)
class PaymentScope:
    header: CaseHeader
    payments: tuple[PaymentView, ...]
    # Approved cross-domain aggregate; raw item rows are not exposed.
    expected_order_total_brl: str


@dataclass(frozen=True)
class DeliveryScope:
    header: CaseHeader
    delivered_customer_date: str | None
    estimated_delivery_date: str | None


@dataclass(frozen=True)
class VerificationScope:
    header: CaseHeader
    valid_evidence_ids: frozenset[str]


@dataclass(frozen=True)
class CaseScopes:
    """Owned by the executor only; never passed wholesale to a sub-agent."""
    header: CaseHeader
    order_seller: OrderSellerScope
    payment: PaymentScope
    delivery: DeliveryScope
    verification: VerificationScope


@dataclass(frozen=True)
class Handoff:
    agent: str
    case_id: str
    facts: dict[str, Any]
    evidence_ids: list[str]
    model_analysis: dict[str, Any] | None = None
