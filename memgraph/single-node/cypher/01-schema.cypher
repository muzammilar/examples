// Start clean, so `make test` can be re-run. Index and constraint DDL is idempotent in Memgraph
// and runs outside explicit transactions. mgconsole splits statements on semicolons, even in
// comments, and comments must be on their own line, without quotes.
MATCH (n) DETACH DELETE n;
// label index, label+property index, edge-type index
CREATE INDEX ON :Airport;
CREATE INDEX ON :Airport(code);
CREATE EDGE INDEX ON :ROUTE;
CREATE CONSTRAINT ON (a:Airport) ASSERT a.code IS UNIQUE;
CREATE CONSTRAINT ON (a:Airport) ASSERT EXISTS (a.city);
SHOW INDEX INFO;
SHOW CONSTRAINT INFO;
