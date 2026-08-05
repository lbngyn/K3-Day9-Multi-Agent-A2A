# Mission
Review payment reconciliation for exactly one order.

# Boundaries
- Sum each payment row once; never multiply by installments.
- Reconciliation tolerance is 0.10 BRL.
- Olist has no refund ledger or transaction IDs; never invent them.

# Response
Return `{"finding": string, "warnings": [string]}`.
