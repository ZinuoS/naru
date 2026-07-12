---
name: positions_by_counterparty
description: All mirrored positions for a given counterparty, most recent first.
params:
  counterparty:
    type: string
expected_columns: [deal_id, coupon_rate, as_of, counterparty]
---
SELECT
    deal_id,
    coupon_rate,
    as_of,
    counterparty
FROM warehouse_positions
WHERE counterparty = :counterparty
ORDER BY as_of DESC
