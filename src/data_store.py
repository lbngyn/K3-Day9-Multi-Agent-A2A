from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

from decimal import Decimal

from .contracts import (CaseHeader, CaseScopes, DeliveryScope, OrderItemView,
                        OrderSellerScope, PaymentScope, PaymentView, VerificationScope)


class OlistStore:
    """Read-only, in-memory index over the only three datasets needed by policy."""

    def __init__(self, data_dir: Path):
        self.orders = self._index_one(data_dir / "olist_orders_dataset.csv", "order_id")
        self.items = self._index_many(data_dir / "olist_order_items_dataset.csv", "order_id")
        self.payments = self._index_many(data_dir / "olist_order_payments_dataset.csv", "order_id")

    @staticmethod
    def _rows(path: Path):
        with path.open(encoding="utf-8-sig", newline="") as fh:
            yield from csv.DictReader(fh)

    def _index_one(self, path: Path, key: str):
        return {row[key]: row for row in self._rows(path)}

    def _index_many(self, path: Path, key: str):
        result = defaultdict(list)
        for row in self._rows(path):
            result[row[key]].append(row)
        return dict(result)

    def context(self, case_path: Path) -> CaseScopes:
        case = json.loads(case_path.read_text(encoding="utf-8-sig"))
        order_id = case["customer_request"]["claimed_order_id"]
        if order_id not in self.orders:
            raise ValueError(f"Unknown claimed_order_id: {order_id}")
        order = self.orders[order_id]
        item_rows = self.items.get(order_id, [])
        payment_rows = self.payments.get(order_id, [])
        header = CaseHeader(
            case["case_id"], order_id, case["policy_version"], order["order_status"],
            case["customer_request"].get("language", "unknown"),
            case["customer_request"].get("message", ""),
        )
        items = tuple(OrderItemView(
            row["order_item_id"], row["seller_id"], row["shipping_limit_date"],
            row["price"], row["freight_value"],
        ) for row in item_rows)
        payments = tuple(PaymentView(
            row["payment_sequential"], row["payment_value"],
        ) for row in payment_rows)
        expected = sum(
            (Decimal(row["price"]) + Decimal(row["freight_value"]) for row in item_rows),
            Decimal("0"),
        )
        policy_codes = {
            "SELLER_HANDOFF_AFTER_LIMIT", "CARRIER_DELIVERED_AFTER_ESTIMATE",
            "ORDER_CANCELED_AFTER_PAYMENT", "ORDER_UNAVAILABLE_AFTER_PAYMENT",
            "MULTIPLE_PAYMENTS_RECONCILED", "DELIVERY_WITHIN_ESTIMATE",
        }
        valid_evidence = {
            f"order:{order_id}",
            *(f'item:{order_id}:{row["order_item_id"]}' for row in item_rows),
            *(f'payment:{order_id}:{row["payment_sequential"]}' for row in payment_rows),
            *(f'seller:{row["seller_id"]}' for row in item_rows),
            *(f"policy:{code}" for code in policy_codes),
        }
        return CaseScopes(
            header=header,
            order_seller=OrderSellerScope(
                header, order["order_status"], order.get("order_delivered_carrier_date"), items,
            ),
            payment=PaymentScope(header, payments, str(expected)),
            delivery=DeliveryScope(
                header, order.get("order_delivered_customer_date"),
                order.get("order_estimated_delivery_date"),
            ),
            verification=VerificationScope(header, frozenset(valid_evidence)),
        )
