CREATE ROLE debezium WITH LOGIN REPLICATION PASSWORD 'demo-cdc-password';
GRANT CONNECT, CREATE ON DATABASE enrichment TO debezium;

CREATE TABLE public.customers (
    id text PRIMARY KEY,
    display_name text NOT NULL,
    segment text NOT NULL CHECK (segment IN ('standard', 'silver', 'gold'))
);

INSERT INTO public.customers (id, display_name, segment)
SELECT 'customer-' || n::text, 'Customer ' || n::text,
       CASE n % 3 WHEN 0 THEN 'gold' WHEN 1 THEN 'silver' ELSE 'standard' END
FROM generate_series(0, (:'customer_count')::integer - 1) AS series(n);

ALTER TABLE public.customers OWNER TO debezium;
GRANT USAGE ON SCHEMA public TO debezium;
GRANT SELECT ON public.customers TO debezium;
