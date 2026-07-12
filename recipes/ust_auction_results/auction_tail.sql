---
name: auction_tail
description: >
  For a given security term, each auction's high yield alongside the
  change in high yield from the immediately preceding auction of the
  same term -- a naive demand-trend proxy. This is NOT the market's
  usual "tail" (high yield minus the pre-auction when-issued yield):
  final_auction_results has no when-issued column, so this recipe
  reinterprets "tail" honestly as an auction-over-auction yield delta
  rather than silently faking the real metric.
params:
  security:
    type: string
expected_columns: [auction_date, security_term, high_yield, yield_change]
---
SELECT
    auction_date,
    security_term,
    high_yield,
    high_yield - LAG(high_yield) OVER (ORDER BY auction_date) AS yield_change
FROM final_auction_results
WHERE security_term = :security
  AND _superseded_by_run_id IS NULL
ORDER BY auction_date
