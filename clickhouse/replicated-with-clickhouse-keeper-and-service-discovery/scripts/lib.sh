# Helpers shared by the scripts in this directory. Hostnames == container names == discovery addresses.
ch() { # NODE [clickhouse-client args...]; SQL on stdin or via -q
  local node=$1; shift
  docker exec -i "$node" clickhouse-client "$@"
}
members() { # current members of cluster_hits: "shard host" per line
  ch clickhouse-server-01 -q "SELECT shard_num, host_name FROM system.clusters WHERE cluster = 'cluster_hits' ORDER BY shard_num, host_name"
}
wait_until() { # SECONDS DESCRIPTION CMD...: poll once a second until CMD succeeds
  local n=$1 what=$2; shift 2
  for _ in $(seq "$n"); do "$@" >/dev/null 2>&1 && return 0; sleep 1; done
  echo "timed out waiting for: $what" >&2; return 1
}
