---
name: weak_demand_auctions
description: >
  Auctions whose bid-to-cover ratio fell at or below a threshold -- a
  simple weak-demand screen (lower bid-to-cover means less investor
  demand relative to the amount offered).
params:
  max_bid_to_cover:
    type: float
expected_columns: [auction_date, security_term, cusip, bid_to_cover]
---
SELECT
    auction_date,
    security_term,
    cusip,
    bid_to_cover
FROM final_auction_results
WHERE bid_to_cover <= :max_bid_to_cover
  AND _superseded_by_run_id IS NULL
ORDER BY auction_date DESC
