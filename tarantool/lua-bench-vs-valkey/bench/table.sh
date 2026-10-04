#!/bin/sh
# Markdown rows comparing two runs of bench/run.sh, op by op:
#   sh bench/table.sh results/single-tarantool-write.txt results/single-valkey-everysec.txt
# op | Tarantool ops/s | p50 ms | p99 ms | Valkey ops/s | p50 ms | p99 ms | Tarantool / Valkey ops/s
set -eu
rows() { awk '$1 ~ /^(put|get|add|update|delete|SET|GET|add\.lua|update\.lua|delete\.lua)$/ && NF == 4 { print $2, $3, $4 }' "$1"; }
rows "$1" >"${TMPDIR:-/tmp}/t.$$"
rows "$2" | paste -d' ' "${TMPDIR:-/tmp}/t.$$" - | awk 'BEGIN { split("put / SET,get / GET,add,update,delete", op, ",") }
	{ printf "| %s | %s | %.2f | %.2f | %s | %.2f | %.2f | %.2fx |\n",
		op[NR], fmt($1), $2, $3, fmt($4), $5, $6, $1 / $4 }
	function fmt(n,   s) { s = sprintf("%d", n); while (s ~ /[0-9]{4}/) sub(/[0-9]{3}($|,)/, ",&", s); sub(/,,/, ",", s); return s }'
rm -f "${TMPDIR:-/tmp}/t.$$"
