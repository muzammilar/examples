# shared helpers (sourced)
CTX=kind-manticore-helm
NS=manticore
W=manticore-manticoresearch-worker
K() { kubectl --context $CTX -n $NS "$@"; }
q() { p=$1; shift; K exec "$W-$p" -c worker -- mysql -h127.0.0.1 -P9306 --skip-table -N -e "$*"; }
qt() { p=$1; shift; K exec "$W-$p" -c worker -- mysql -h127.0.0.1 -P9306 --table -e "$*"; }
cs() { q "$1" "SHOW STATUS LIKE 'cluster_manticore_cluster_$2'" 2>/dev/null | cut -f2; }
now() { perl -MTime::HiRes=time -e 'printf "%.3f", time'; }
T0=${T0:-$(now)}
ev() { printf '>>> [%6.1fs] %s\n' "$(echo "$(now) $T0" | awk '{ print $1 - $2 }')" "$*" | tee -a results/events.txt; }
pod_dns() { echo "$W-$1.$W-replication-svc.$NS.svc.cluster.local:9306"; }
nodes() { n=$1; s=""; for i in $(seq 0 $((n - 1))); do s="$s${s:+,}$(pod_dns $i)"; done; echo "$s"; }
start_load() { # $1 = writer/reader nodes, $2 = verify nodes
	K delete pod load --ignore-not-found --wait >/dev/null
	K run load --image=manticore-helm-load:local --image-pull-policy=Never --restart=Never \
		--env="NODES=$1" --env="VERIFY_NODES=$2" --env="DURATION=${DURATION:-60}" --env="CLUSTER=manticore_cluster" \
		--env="REPLICATED_TABLE=logs" --env="SHARDED_TABLE=-" --env="WRITERS=${WRITERS:-4}" --env="RATE=${RATE:-0}" --env="READERS=${READERS:-4}" >/dev/null
	until [ "$(K get pod load -o jsonpath='{.status.phase}')" = Running ]; do sleep 0.5; done
	T0=$(now); : >results/events.txt
}
wait_load() {
	until phase=$(K get pod load -o jsonpath='{.status.phase}'); [ "$phase" = Succeeded ] || [ "$phase" = Failed ]; do sleep 1; done
	K logs load >results/load.txt 2>&1
	[ "$phase" = Succeeded ]
}
