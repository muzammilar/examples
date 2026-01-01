-- Get a list of partitions for a system table
SELECT DISTINCT partition
FROM system.parts
WHERE database = 'system'
  AND table = 'processors_profile_log'
  AND active;

-- Get partitions for a specific table
SELECT *
FROM system.parts
WHERE database = 'system'
  AND table = 'processors_profile_log'
  AND max_date < now()-toIntervalDay(7);

--  This returns say 202511
-- Drop the partition
ALTER TABLE system.processors_profile_log DROP PARTITION 202511;

-------------------------------------------



;;
