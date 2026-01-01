-- Check if the index is materialized
SELECT parts_to_do, * FROM system.mutations where table='table_name' and is_done=0;

-- Check al mutations on a table
SELECT * FROM system.data_skipping_indices WHERE table= 'table_name';

-- Check if the index is materialized
SHOW CREATE TABLE users;

-- Check if the index is materialized
SELECT * FROM system.data_skipping_indices where table='users';

-- Kill the mutation. Make sure you're precise in killing the mutation and add many filters to them like table name
KILL MUTATION WHERE mutation_id = 'mutation_365130.txt' AND table='processors_profile_log' SYNC;
