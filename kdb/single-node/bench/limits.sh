#!/bin/sh
# Resource budget for `make benchmark`: caps the running database containers with
# `docker update` before the run and puts the old limits back afterwards.
#   sh bench/limits.sh apply CPUS MEM SPEC...  # prints the applied limits as one JSON line
#   sh bench/limits.sh restore                 # undoes the last apply
# SPEC is a compose service that gets an equal share of the budget, or service=CPUS:MEM
# for a fixed amount taken off the top first (small helpers). MEM is like 6g or 512m.
# Memory swap is capped at the same value (no swap). Docker cannot remove a memory limit
# from a running container, so restore sets an unlimited one back to the Docker VM's total
# memory (in effect no limit); `make down && make up` starts from scratch.
set -eu
COMPOSE=${COMPOSE:-docker compose}
state=results/.limits-state

mib() { # 6g -> 6144, 512m -> 512, 2.5g -> 2560
	echo "$1" | awk '/[gG]$/ { printf "%d\n", substr($0, 1, length - 1) * 1024; next }
		/[mM]$/ { printf "%d\n", substr($0, 1, length - 1); next } { exit 1 }' ||
		{ echo "limits.sh: bad size '$1' (use e.g. 6g or 512m)" >&2; exit 1; }
}
cid() {
	id=$($COMPOSE ps -q "$1")
	[ -n "$id" ] || { echo "limits.sh: service $1 is not running" >&2; exit 1; }
	echo "$id"
}

case $1 in
apply)
	cpus=$2 mem=$3
	shift 3
	fixed_cpus=0 fixed_mib=0 shared=0
	for s in "$@"; do
		case $s in
		*=*) v=${s#*=}
			fixed_cpus=$(awk "BEGIN { print $fixed_cpus + ${v%%:*} }")
			fixed_mib=$((fixed_mib + $(mib "${v#*:}"))) ;;
		*) shared=$((shared + 1)) ;;
		esac
	done
	share_cpus=$(awk "BEGIN { if ($shared) printf \"%g\", ($cpus - $fixed_cpus) / $shared }")
	share_mib=$([ "$shared" = 0 ] || echo $((($(mib "$mem") - fixed_mib) / shared)))
	mkdir -p results
	: >"$state"
	json=""
	for s in "$@"; do
		case $s in
		*=*) svc=${s%%=*} v=${s#*=} c=${v%%:*} m=$(mib "${v#*:}") ;;
		*) svc=$s c=$share_cpus m=$share_mib ;;
		esac
		id=$(cid "$svc")
		docker inspect -f '{{.Id}} {{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{.HostConfig.MemorySwap}}' "$id" >>"$state"
		docker update --cpus "$c" --memory "${m}m" --memory-swap "${m}m" "$id" >/dev/null
		name=$(docker inspect -f '{{.Name}}' "$id")
		json="$json${json:+,}\"${name#/}\":{\"cpus\":$c,\"memory_mib\":$m}"
		echo "==> limits: ${name#/} ${c} CPUs, ${m} MiB" >&2
	done
	echo "{\"budget\":{\"cpus\":$cpus,\"memory\":\"$mem\"},\"containers\":{$json},\"bench_client_cpus\":${BENCH_CLIENT_CPUS:-2}${BENCH_NOTE:+,\"note\":\"$BENCH_NOTE\"}}"
	;;
restore)
	[ -f "$state" ] || exit 0
	ncpu=$(docker info --format '{{.NCPU}}')
	vmmem=$(docker info --format '{{.MemTotal}}')
	while read -r id nano mem swap; do
		c=$([ "$nano" = 0 ] && echo "$ncpu" || awk "BEGIN { print $nano / 1e9 }")
		[ "$mem" = 0 ] && mem=$vmmem
		case $swap in 0 | -1) swap=$((mem * 2)) ;; esac # "unlimited" swap is refused here
		docker update --cpus "$c" --memory "$mem" --memory-swap "$swap" "$id" >/dev/null 2>&1 ||
			echo "limits.sh: could not restore ${id%"${id#????????????}"} (gone?)" >&2
	done <"$state"
	rm -f "$state"
	echo "==> limits restored" >&2
	;;
*) echo "usage: $0 apply CPUS MEM SPEC... | restore" >&2; exit 2 ;;
esac
