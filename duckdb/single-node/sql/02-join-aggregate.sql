-- join CSV events to NDJSON users and a CSV dimension; GROUP BY ALL groups by every
-- non-aggregate column in the SELECT list
.timer on
SELECT c.region, u.plan, count(*) AS purchases, round(sum(e.amount), 2) AS revenue
FROM events e
JOIN users u USING (user_id)
JOIN countries c ON c.code = e.country
WHERE e.event_type = 'purchase'
GROUP BY ALL
ORDER BY revenue DESC
LIMIT 6;

-- top 2 spenders per country: QUALIFY filters on a window function without a subquery
SELECT e.country, u.name, u.plan, round(sum(e.amount), 2) AS spent,
       rank() OVER (PARTITION BY e.country ORDER BY sum(e.amount) DESC) AS rnk
FROM events e JOIN users u USING (user_id)
WHERE e.event_type = 'purchase' AND e.country IN ('US', 'DE', 'PK')
GROUP BY e.country, u.name, u.plan   -- GROUP BY ALL + QUALIFY is not supported (yet)
QUALIFY rnk <= 2
ORDER BY e.country, rnk;

-- monthly net revenue with a running total (a window over an aggregate)
SELECT date_trunc('month', ts)::DATE AS month,
       round(sum(if(event_type = 'refund', -amount, amount)), 2) AS net,
       round(sum(sum(if(event_type = 'refund', -amount, amount))) OVER (ORDER BY month), 2) AS running_total
FROM events
WHERE event_type IN ('purchase', 'refund')
GROUP BY month
ORDER BY month;
