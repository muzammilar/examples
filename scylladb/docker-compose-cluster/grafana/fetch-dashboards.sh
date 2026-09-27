#!/bin/sh
# usage: fetch-dashboards.sh <out-dir> <base-url> <path>...
# Downloads <base-url>/<path> into <out-dir>/<basename>, skipping files that exist.
# Classic (v1) dashboard JSON is rewritten to use the provisioned datasource
# (uid "prometheus"): datasource inputs/variables are dropped and every
# Prometheus datasource reference is pinned to that uid. A missing dashboard
# uid is taken from the file name so links stay stable.
set -eu
out=$1 base=$2
shift 2
mkdir -p "$out"
for path in "$@"; do
	dst=$out/$(basename "$path")
	[ -f "$dst" ] && continue
	echo "fetching $base/$path"
	curl -fsSL "$base/$path" -o "$dst.tmp"
	case $dst in *.json)
		jq --arg uid "$(basename "$dst" .json | tr . -)" 'if .apiVersion then . else
			del(.__inputs, .__requires, .__elements) | .id = null | .uid = (.uid // $uid)
			| .templating.list |= map(select(.type != "datasource"))?
			| walk(if type == "object" and has("datasource") and (
				(.datasource | type) == "string" and (.datasource | startswith("-- ") | not)
				or (.datasource | type) == "object" and ((.datasource.type == "prometheus") or (.datasource.uid // "" | startswith("$")))
			) then .datasource = {type: "prometheus", uid: "prometheus"} else . end)
		end' "$dst.tmp" >"$dst.json.tmp"
		mv "$dst.json.tmp" "$dst.tmp" ;;
	esac
	mv "$dst.tmp" "$dst"
done
