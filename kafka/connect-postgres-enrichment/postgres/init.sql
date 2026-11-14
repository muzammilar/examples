CREATE ROLE debezium WITH LOGIN REPLICATION PASSWORD 'demo-cdc-password';
GRANT CONNECT, CREATE ON DATABASE enrichment TO debezium;

CREATE TABLE public.customers (
    id text PRIMARY KEY,
    display_name text NOT NULL,
    segment text NOT NULL CHECK (segment IN ('standard', 'silver', 'gold'))
);

INSERT INTO public.customers (id, display_name, segment) VALUES
    ('customer-0', 'Ada Lovelace', 'gold'),
    ('customer-1', 'Grace Hopper', 'silver'),
    ('customer-2', 'Katherine Johnson', 'gold'),
    ('customer-3', 'Edsger Dijkstra', 'standard'),
    ('customer-4', 'Barbara Liskov', 'silver'),
    ('customer-5', 'Donald Knuth', 'gold'),
    ('customer-6', 'Margaret Hamilton', 'silver'),
    ('customer-7', 'Alan Turing', 'gold'),
    ('customer-8', 'Frances Allen', 'standard'),
    ('customer-9', 'Leslie Lamport', 'silver');

ALTER TABLE public.customers OWNER TO debezium;
GRANT USAGE ON SCHEMA public TO debezium;
GRANT SELECT ON public.customers TO debezium;
