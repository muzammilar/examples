#!/bin/sh
# keys.sh write|check N [pod]: SET or GET key:1..key:N through one valkey-cli -c
set -e
n=$2
cli="kubectl --context kind-valkey-operator -n valkey exec -i ${3:-valkey-valkey-0-0-0} -c server -- env -u VALKEYCLI_AUTH valkey-cli -c"
case $1 in
write) out=$(seq "$n" | awk '{print "SET key:" $1 " value-" $1}' | $cli); ok=$(echo "$out" | grep -cx OK) ;;
check) out=$(seq "$n" | awk '{print "GET key:" $1}' | $cli); ok=$(echo "$out" | grep -x 'value-[0-9]*' | sort -u | wc -l) ;;
*) echo "usage: $0 write|check N [pod]"; exit 1 ;;
esac
echo "$1: $((ok))/$n keys"
[ "$ok" -eq "$n" ] || { echo "$out" | grep -v -e '^-> Redirected' -e '^value-' -e '^OK$' | sort | uniq -c | head; exit 1; }
