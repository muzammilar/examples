# shared helpers (sourced)
q() { n=$1; shift; docker exec "manticore-cc-$n" mysql -h127.0.0.1 -P9306 --skip-table -N -e "$*"; }
qt() { n=$1; shift; docker exec "manticore-cc-$n" mysql -h127.0.0.1 -P9306 --table -e "$*"; }
node_state() { q "$1" "SHOW STATUS LIKE 'cluster_c_node_state'" 2>/dev/null | cut -f2; }
now() { perl -MTime::HiRes=time -e 'printf "%.3f", time'; }
T0=${T0:-$(now)}
ev() { # event line with seconds since the load started (same clock as the load's [  12.0s] lines)
	printf '>>> [%6.1fs] %s\n' "$(echo "$(now) $T0" | awk '{ print $1 - $2 }')" "$*" | tee -a results/events.txt
}
# shards per node of table $2, asked on node $1: "172.28.30.11:9312 0,1,3,5 ..." and rf_status counts
shard_map() {
	q "$1" "SHOW SHARDING STATUS $2" | awk -F'\t' '{ s[$3] = s[$3] (s[$3] ? "," : "") $2; st[$8]++ }
		END { for (n in s) printf "%s=[%s] ", "manticore-" (substr(n, 11, 2) - 10), s[n];
			printf "| rf_status:"; for (k in st) printf " %s=%d", k, st[k]; print "" }'
}
start_load() { # DURATION etc. from the environment
	docker rm -f manticore-cc-load >/dev/null 2>&1 || true # left over from an interrupted run
	docker compose --profile load run -d --name manticore-cc-load load >/dev/null
	T0=$(now)
	: >results/events.txt
}
wait_load() {
	docker wait manticore-cc-load >/dev/null
	docker logs manticore-cc-load >results/load.txt 2>&1
	rc=$(docker inspect -f '{{.State.ExitCode}}' manticore-cc-load)
	docker rm manticore-cc-load >/dev/null
	return "$rc"
}
