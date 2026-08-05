from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

from .contracts import CaseContext


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

    def context(self, case_path: Path) -> CaseContext:
        case = json.loads(case_path.read_text(encoding="utf-8-sig"))
        order_id = case["customer_request"]["claimed_order_id"]
        if order_id not in self.orders:
            raise ValueError(f"Unknown claimed_order_id: {order_id}")
        return CaseContext(
            case_id=case["case_id"], order_id=order_id,
            policy_version=case["policy_version"], order=self.orders[order_id],
            items=self.items.get(order_id, []), payments=self.payments.get(order_id, []),
        )

