#!/bin/bash
# One-time cluster setup, run by `make up` inside ob1 (idempotent: skips finished steps).
# 1. ALTER SYSTEM BOOTSTRAP over the three zones: creates the sys tenant (and the root
#    service) with a full replica on every observer.
# 2. Resource unit + pool (one unit per zone) + MySQL-mode user tenant `test` with a full
#    replica in each zone (locality F@zone1,F@zone2,F@zone3), leaders spread over the zones.
set -euo pipefail

SYS="obclient -h127.1 -P2881 -uroot@sys -A -N -s"
TEST_MEMORY=${OB_TENANT_MEMORY:-1536M}
TEST_LOG_DISK=${OB_TENANT_LOG_DISK:-2G}
TEST_CPU=${OB_TENANT_CPU:-1}
SYS_MEMORY=${OB_SYS_MEMORY:-1G}

wait_for() { # wait_for DESCRIPTION SECONDS COMMAND...
	local what=$1 secs=$2
	shift 2
	for _ in $(seq 1 "$secs"); do "$@" >/dev/null 2>&1 && return 0; sleep 1; done
	echo "bootstrap.sh: timed out waiting for $what" >&2
	return 1
}

# Before bootstrap every observer accepts root@sys logins but has no sys tenant yet.
for ip in 172.28.12.11 172.28.12.12 172.28.12.13; do
	wait_for "observer $ip" 180 obclient -h$ip -P2881 -uroot -A -e 'SELECT 1'
done

if $SYS -e 'SELECT COUNT(*) FROM oceanbase.DBA_OB_SERVERS' >/dev/null 2>&1; then
	echo "bootstrap.sh: cluster already bootstrapped"
else
	echo "bootstrap.sh: ALTER SYSTEM BOOTSTRAP (zone1, zone2, zone3)"
	time obclient -h127.1 -P2881 -uroot -A -e "SET SESSION ob_query_timeout = 1000000000;
		ALTER SYSTEM BOOTSTRAP
			ZONE 'zone1' SERVER '172.28.12.11:2882',
			ZONE 'zone2' SERVER '172.28.12.12:2882',
			ZONE 'zone3' SERVER '172.28.12.13:2882'"
	wait_for "sys tenant" 120 $SYS -e 'SELECT 1'
	# sys gets 1G from __min_full_resource_pool_memory; give it 2G like obd's mini deployment
	$SYS -e "ALTER RESOURCE UNIT sys_unit_config MEMORY_SIZE = '$SYS_MEMORY'"
fi

if [ "$($SYS -e "SELECT COUNT(*) FROM oceanbase.DBA_OB_TENANTS WHERE TENANT_NAME = 'test' AND STATUS = 'NORMAL'")" = 1 ]; then
	echo "bootstrap.sh: tenant test already exists"
else
	echo "bootstrap.sh: creating tenant test ($TEST_CPU CPU, $TEST_MEMORY memory, $TEST_LOG_DISK log disk per zone)"
	time $SYS -e "SET SESSION ob_query_timeout = 1000000000;
		CREATE RESOURCE UNIT IF NOT EXISTS test_unit
			MAX_CPU = $TEST_CPU, MIN_CPU = $TEST_CPU, MEMORY_SIZE = '$TEST_MEMORY', LOG_DISK_SIZE = '$TEST_LOG_DISK';
		CREATE RESOURCE POOL IF NOT EXISTS test_pool
			UNIT = 'test_unit', UNIT_NUM = 1, ZONE_LIST = ('zone1', 'zone2', 'zone3');
		CREATE TENANT IF NOT EXISTS test
			RESOURCE_POOL_LIST = ('test_pool'),
			LOCALITY = 'F@zone1, F@zone2, F@zone3',
			PRIMARY_ZONE = 'zone1;zone2;zone3'
			SET ob_compatibility_mode = 'mysql', ob_tcp_invited_nodes = '%'"
fi
# sys-tenant user obproxy reads the partition locations with
$SYS -e "CREATE USER IF NOT EXISTS proxyro IDENTIFIED BY '${OB_PROXYRO_PASSWORD:-proxyro}'; GRANT SELECT ON oceanbase.* TO proxyro"
wait_for "tenant test" 300 obclient -h127.1 -P2881 -uroot@test -A -e 'SELECT 1'
echo "bootstrap.sh: cluster ready"
