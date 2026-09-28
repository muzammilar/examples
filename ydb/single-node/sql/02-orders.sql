-- UPSERT is a blind insert-or-replace (it does not read the existing row first)
UPSERT INTO orders (id, customer, amount_cents, created_at, attrs) VALUES
    (1, "alice", 3000, CurrentUtcTimestamp(), Json(@@{"channel": "web", "items": 2}@@)),
    (2, "bob",   1250, CurrentUtcTimestamp(), Json(@@{"channel": "app", "items": 1}@@)),
    (3, "alice", 4500, CurrentUtcTimestamp(), Json(@@{"channel": "app", "items": 3}@@)),
    (4, "carol", 5000, CurrentUtcTimestamp(), Json(@@{"channel": "web", "items": 9}@@));

-- read through the secondary index with VIEW, and pull typed fields out of the Json column
SELECT id, amount_cents, JSON_VALUE(attrs, "$.channel") AS channel,
       JSON_VALUE(attrs, "$.items" RETURNING Int64) AS items
FROM orders VIEW idx_customer WHERE customer = "alice" ORDER BY id;

-- aggregate per customer plus a window function over the aggregates
SELECT customer, COUNT(*) AS orders, SUM(amount_cents) AS total,
       RANK() OVER (ORDER BY SUM(amount_cents) DESC) AS rnk
FROM orders GROUP BY customer ORDER BY rnk;
