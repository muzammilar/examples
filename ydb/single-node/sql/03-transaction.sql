-- one query is one serializable transaction: find the top customer, discount their
-- orders 10%, add a bonus order, and read the result back, all or nothing
$totals = SELECT customer, SUM(amount_cents) AS total FROM orders GROUP BY customer;
$top = (SELECT customer FROM $totals ORDER BY total DESC LIMIT 1);
UPDATE orders SET amount_cents = amount_cents * 9 / 10 WHERE customer = $top AND id < 100;
UPSERT INTO orders (id, customer, amount_cents, created_at, attrs)
    VALUES (100, $top, 0, CurrentUtcTimestamp(), Json(@@{"channel": "bonus", "items": 1}@@));
SELECT id, customer, amount_cents, JSON_VALUE(attrs, "$.channel") AS channel
FROM orders VIEW idx_customer WHERE customer = $top ORDER BY id;
