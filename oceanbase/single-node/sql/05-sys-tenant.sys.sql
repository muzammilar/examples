-- *.sys.sql files run as root@sys: cluster-wide views live in the sys tenant.
SELECT TENANT_ID, TENANT_NAME, TENANT_TYPE, COMPATIBILITY_MODE, STATUS, PRIMARY_ZONE
  FROM oceanbase.DBA_OB_TENANTS ORDER BY TENANT_ID;

SELECT SVR_IP, SVR_PORT, ZONE, STATUS, WITH_ROOTSERVER, BUILD_VERSION
  FROM oceanbase.DBA_OB_SERVERS;

-- Per-server resources: memory_limit / CPU the observer runs with.
SELECT SVR_IP, CPU_CAPACITY, CPU_ASSIGNED,
       ROUND(MEM_CAPACITY / 1024 / 1024 / 1024, 1) AS mem_capacity_gb,
       ROUND(MEM_ASSIGNED / 1024 / 1024 / 1024, 1) AS mem_assigned_gb,
       ROUND(DATA_DISK_CAPACITY / 1024 / 1024 / 1024, 1) AS data_disk_gb,
       ROUND(LOG_DISK_CAPACITY / 1024 / 1024 / 1024, 1) AS log_disk_gb
  FROM oceanbase.GV$OB_SERVERS;

-- Resource units given to each tenant.
SELECT t.TENANT_NAME, c.NAME AS unit_config, c.MAX_CPU, c.MIN_CPU,
       ROUND(c.MEMORY_SIZE / 1024 / 1024 / 1024, 1) AS memory_gb,
       ROUND(c.LOG_DISK_SIZE / 1024 / 1024 / 1024, 1) AS log_disk_gb
  FROM oceanbase.DBA_OB_TENANTS t
  JOIN oceanbase.DBA_OB_RESOURCE_POOLS p ON p.TENANT_ID = t.TENANT_ID
  JOIN oceanbase.DBA_OB_UNIT_CONFIGS c ON c.UNIT_CONFIG_ID = p.UNIT_CONFIG_ID
 ORDER BY t.TENANT_ID;

-- The demo tables as seen cluster-wide (CDB_ view = all tenants).
SELECT TENANT_ID, TABLE_NAME, PARTITION_NAME, TABLET_ID, LS_ID, ROLE
  FROM oceanbase.CDB_OB_TABLE_LOCATIONS
 WHERE DATABASE_NAME = 'demo' AND TABLE_TYPE = 'USER TABLE'
 ORDER BY TABLE_NAME, TABLET_ID;
