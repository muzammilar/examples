-- On the access node (an1, database tsdb): attach the three data nodes. add_data_node connects
-- to each one, creates database tsdb there and installs timescaledb in it (bootstrap).
\timing on
SELECT extversion AS timescaledb, current_setting('server_version') AS postgres
FROM pg_extension WHERE extname = 'timescaledb';

SELECT node_name, host, database, node_created, database_created, extension_created
FROM add_data_node('dn1', host => 'dn1', if_not_exists => true);
SELECT node_name, host, database, node_created, database_created, extension_created
FROM add_data_node('dn2', host => 'dn2', if_not_exists => true);
SELECT node_name, host, database, node_created, database_created, extension_created
FROM add_data_node('dn3', host => 'dn3', if_not_exists => true);

-- data nodes are foreign servers (timescaledb_fdw) on the access node
SELECT node_name, owner, options FROM timescaledb_information.data_nodes ORDER BY 1;
