-- *.sys.sql run as root@sys: what ob-operator created.
SELECT SVR_IP, ZONE, STATUS, WITH_ROOTSERVER, BUILD_VERSION FROM oceanbase.DBA_OB_SERVERS;

SELECT TENANT_ID, TENANT_NAME, TENANT_TYPE, COMPATIBILITY_MODE, STATUS, LOCALITY
  FROM oceanbase.DBA_OB_TENANTS ORDER BY TENANT_ID;

-- per-observer resources: memory_limit 4G minus system_memory 1G; data file and log
-- disk as the operator sized them from the PVC sizes (20% of data, 95% of redo log)
SELECT SVR_IP, CPU_CAPACITY, CPU_ASSIGNED,
       ROUND(MEM_CAPACITY / 1073741824, 1) AS mem_capacity_gb,
       ROUND(MEM_ASSIGNED / 1073741824, 1) AS mem_assigned_gb,
       ROUND(DATA_DISK_CAPACITY / 1073741824, 1) AS data_disk_gb,
       ROUND(LOG_DISK_CAPACITY / 1073741824, 1) AS log_disk_gb
  FROM oceanbase.GV$OB_SERVERS;

SELECT c.NAME, c.MAX_CPU, ROUND(c.MEMORY_SIZE / 1073741824, 2) AS memory_gb,
       ROUND(c.LOG_DISK_SIZE / 1073741824, 1) AS log_disk_gb
  FROM oceanbase.DBA_OB_UNIT_CONFIGS c ORDER BY c.UNIT_CONFIG_ID;

SELECT NAME, VALUE FROM oceanbase.GV$OB_PARAMETERS
 WHERE NAME IN ('memory_limit', 'system_memory', 'cpu_count', 'datafile_size', 'datafile_maxsize',
                'log_disk_size', '__min_full_resource_pool_memory')
 ORDER BY NAME;
