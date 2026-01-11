
-- Check the primary key index of the table
SELECT * FROM mergeTreeIndex('default','users');

-- Check the data skip index of the table
SELECT * FROM system.data_skipping_indices WHERE table = 'users';
