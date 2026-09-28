# Shared helpers for test.sh and failover.sh (POSIX sh, curl + jq).
N1=http://weaviate-1:8080
N2=http://weaviate-2:8080
N3=http://weaviate-3:8080
ID=00000000-0000-0000-0000-000000000001

rest() { # METHOD URL FILE JQ_FILTER: print the body, send it, show the result
  echo "==> $1 ${2#http://}  < $3"; cat "$3"
  curl -sS --fail-with-body -X "$1" "$2" -H 'Content-Type: application/json' --data @"$3" | jq -c "$4"
  echo
}

nodes() { # NODE_URL: every member as this node sees it
  curl -sS --fail-with-body "$1/v1/nodes" | jq -c '.nodes[] | {name, status, version}'
}

# shard -> nodes holding a replica of it, from each node's verbose status
placement() { # NODE_URL
  curl -sS --fail-with-body "$1/v1/nodes/Landmark?output=verbose" \
    | jq -c '[.nodes[] | .name as $n | .shards[]? | {shard: .name, node: $n}] | group_by(.shard)[]
      | {shard: .[0].shard, replicas: (map(.node) | sort)}'
}

# nearVector at consistency level CL; prints the hits, fails on a GraphQL error
near() { # NODE_URL CL
  echo "==> POST ${1#http://}/v1/graphql  nearVector, consistencyLevel: $2"
  grep -v '^#' requests/06-near-vector.graphql | sed "s/CL/$2/" | jq -Rs '{query: .}' \
    | curl -sS --fail-with-body "$1/v1/graphql" -H 'Content-Type: application/json' --data @- \
    | jq -ce 'if .errors then (.errors | tostring | error) else .data.Get.Landmark[] | {name, city, distance: ._additional.distance} end'
}

get() { # NODE_URL CL [ID]: read one object by id; prints the HTTP status and the name or error
  curl -sS -o /tmp/get.json -w '%{http_code}' "$1/v1/objects/Landmark/${3:-$ID}?consistency_level=$2" > /tmp/get.code || true
  printf 'GET %s consistency_level=%s -> %s %s\n' "${3:-$ID}" "$2" "$(cat /tmp/get.code)" \
    "$(jq -c '.properties.name // .error // .' /tmp/get.json 2>/dev/null)"
  [ "$(cat /tmp/get.code)" = 200 ]
}

count() { # NODE_URL: objects in Landmark (GraphQL Aggregate)
  curl -sS --fail-with-body "$1/v1/graphql" -H 'Content-Type: application/json' \
    -d '{"query": "{Aggregate{Landmark{meta{count}}}}"}' | jq '.data.Aggregate.Landmark[0].meta.count'
}

batch() { # URL FILE: batch insert, print per-object results, return the number of failed objects
  echo "==> POST ${1#http://}  < $2"; cat "$2"
  curl -sS --fail-with-body -X POST "$1" -H 'Content-Type: application/json' --data @"$2" > /tmp/batch.json
  jq -c '.[] | {id, status: (.result.errors.error[0].message // "SUCCESS")}' /tmp/batch.json
  BATCH_ERRORS=$(jq '[.[] | select(.result.errors)] | length' /tmp/batch.json)
}

expect() { # ACTUAL EXPECTED WHAT
  if [ "$1" = "$2" ]; then echo "ok: $3 = $1"; else echo "FAIL: $3 = $1, expected $2"; exit 1; fi
}
