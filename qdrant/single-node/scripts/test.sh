#!/bin/sh
# Runs each request in order: print the body, send it, show the result.
set -euo pipefail
URL=${QDRANT_URL:-http://qdrant:6333}
C=$URL/collections/demo

req() { # METHOD URL FILE JQ_FILTER
  echo "==> $1 ${2#"$URL"}  < $3"; cat "$3"
  curl -sS --fail-with-body -X "$1" "$2" -H 'Content-Type: application/json' --data @"$3" | jq -c "$4"
  echo
}

curl -sS -X DELETE "$C" >/dev/null   # start clean, so the test is repeatable
req PUT  "$C"                        requests/01-create-collection.json '.result'
req PUT  "$C/index?wait=true"        requests/02-payload-index.json     '.result.status'
req PUT  "$C/points?wait=true"       requests/03-upsert-points.json     '.result.status'
req POST "$C/points/query"           requests/04-knn-search.json        '.result.points[] | {id, score, city: .payload.city, name: .payload.name}'
req POST "$C/points/query"           requests/05-filtered-search.json   '.result.points[] | {id, score, city: .payload.city, name: .payload.name}'
req POST "$C/points/query"           requests/06-recommend.json         '.result.points[] | {id, score, city: .payload.city, name: .payload.name}'
echo "==> GET /collections/demo"
curl -sS "$C" | jq -c '.result | {status, points_count, payload_schema}'
