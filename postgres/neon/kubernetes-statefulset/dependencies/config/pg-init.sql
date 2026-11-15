CREATE ROLE replica WITH REPLICATION LOGIN PASSWORD 'local-only-replica';
SELECT pg_create_physical_replication_slot('pg1');
SELECT pg_create_physical_replication_slot('pg2');
