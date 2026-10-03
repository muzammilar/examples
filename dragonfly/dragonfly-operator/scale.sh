#!/bin/sh
# ./scale.sh up|down [REPLICAS]: change spec.replicas (pods, including the master)
set -e
k() { kubectl --context "kind-$CLUSTER_NAME" -n "$NAMESPACE" "$@"; }
cur=$(k get dragonfly dragonfly -o jsonpath='{.spec.replicas}')
if [ "$1" = up ]; then n=${2:-$((cur + 1))}; else n=${2:-$((cur - 1))}; fi
[ "$n" -ge 1 ] && [ "$n" != "$cur" ] || { echo "bad REPLICAS=$n (spec.replicas is $cur)"; exit 1; }

echo "spec.replicas $cur -> $n"
start=$(date +%s)
k patch dragonfly dragonfly --type merge -p "{\"spec\":{\"replicas\":$n}}"
k wait sts/dragonfly --for=jsonpath='{.status.replicas}'=$n --timeout=5m
./wait-ready.sh 300
echo "scaled in $(($(date +%s) - start)) s"
k get pods -l app=dragonfly -L role
