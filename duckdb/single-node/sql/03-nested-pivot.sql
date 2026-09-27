-- lists and structs straight from JSON: UNNEST a list, dot into a struct
.timer on
SELECT tag, count(*) AS users, round(avg(prefs.emails::INT) * 100, 1) AS pct_emails
FROM (SELECT unnest(tags) AS tag, prefs FROM users)
GROUP BY ALL
ORDER BY users DESC;

-- list functions and lambdas without unnesting
SELECT user_id, tags, len(tags) AS n,
       list_transform(tags, lambda t: upper(t)) AS upper_tags,
       list_contains(tags, 'vip') AS is_vip
FROM users
WHERE len(tags) = 3
LIMIT 3;

-- PIVOT: one row per country, one column per event type (columns found from the data)
PIVOT (SELECT country, event_type FROM events WHERE country IN ('US', 'IN', 'PK'))
ON event_type
USING count(*)
ORDER BY country;
