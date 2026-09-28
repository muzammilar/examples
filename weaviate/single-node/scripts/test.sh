#!/bin/sh
# Runs each request in order: print the body, send it, show the result.
set -euo pipefail
URL=${WEAVIATE_URL:-http://weaviate:8080}

rest() { # METHOD PATH FILE JQ_FILTER
  echo "==> $1 $2  < $3"; cat "$3"
  curl -sS --fail-with-body -X "$1" "$URL$2" -H 'Content-Type: application/json' --data @"$3" | jq -c "$4"
  echo
}
gql() { # FILE  (GraphQL: comments stripped, wrapped as {"query": ...})
  echo "==> POST /v1/graphql  < $1"; cat "$1"
  grep -v '^#' "$1" | jq -Rs '{query: .}' \
    | curl -sS --fail-with-body "$URL/v1/graphql" -H 'Content-Type: application/json' --data @- \
    | jq -c 'if .errors then (.errors | tostring | error) else .data.Get.Landmark[] end'
  echo
}

curl -sS -X DELETE "$URL/v1/schema/Landmark" >/dev/null   # start clean, so the test is repeatable
rest POST /v1/schema        requests/01-create-collection.json '{class, vectorizer, distance: .vectorIndexConfig.distance}'
rest POST /v1/batch/objects requests/02-batch-insert.json     '.[] | {id, status: .result.errors // "SUCCESS"}'
# ASYNC_INDEXING is on: vectors are searchable once the background queue has indexed them
echo "==> wait for async indexing (GET /v1/nodes/Landmark?output=verbose)"
until curl -sS "$URL/v1/nodes/Landmark?output=verbose" \
  | jq -e '[.nodes[].shards[]? | .vectorQueueLength == 0 and .vectorIndexingStatus == "READY"] | length > 0 and all' >/dev/null
do sleep 0.2; done
echo
gql requests/03-near-vector.graphql
gql requests/04-near-vector-where.graphql
gql requests/05-bm25.graphql
gql requests/06-hybrid.graphql
