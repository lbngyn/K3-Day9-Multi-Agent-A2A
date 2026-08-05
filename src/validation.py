from __future__ import annotations

from .contracts import VerificationScope

ISSUES = {"canceled_order_paid", "unavailable_order_paid", "late_delivery_seller",
          "late_delivery_logistics", "valid_split_payment", "unsupported_late_claim"}


def validate_result(ctx: VerificationScope, result: dict) -> None:
    """Non-agent hard gate; adds no model call and never silently repairs output."""
    assert result["case_id"] == ctx.header.case_id
    assert result["assessment"]["primary_issue"] in ISSUES
    assert 0 <= result["assessment"]["confidence"] <= 1
    assert len(result["evidence_ids"]) <= 10
    assert all(v >= 0 for key, v in result["financial_resolution"].items()
               if key.endswith("_brl"))
    assert set(result["evidence_ids"]) <= ctx.valid_evidence_ids
