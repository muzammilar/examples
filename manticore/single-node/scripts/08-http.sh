#!/bin/sh
# The same table over HTTP JSON (port 9308), from inside the container (wget ships with the image).
set -eu
post() { # path, body
	echo; echo "--> POST $1  $2"
	wget -qO- --header='Content-Type: application/json' --post-data="$2" "http://127.0.0.1:9308$1"; echo
}
# /insert: one document (id given), searchable immediately
post /insert '{"table":"products","id":18,"doc":{"title":"Trail running gaiters","description":"Gaiters keep stones out of trail shoes","category":"apparel","brand":"Altra","price":25.0,"rating":4.2,"stock":80,"tags":[2],"added":1791200000,"embedding":[0.8,0.8,0.0,0.0]}}'
# /search: full-text match on two fields, a range filter, highlighting, sorting, a facet (aggs)
post /search '{"table":"products","query":{"bool":{"must":[{"match":{"title,description":"trail"}},{"range":{"price":{"lte":150}}}]}},"_source":["title","price"],"highlight":{"fields":["title"]},"sort":[{"_score":"desc"},{"id":"asc"}],"limit":3,"aggs":{"by_brand":{"terms":{"field":"brand","size":3}}}}'
# /search with a phrase and with KNN
post /search '{"table":"products","query":{"match_phrase":{"description":"trail shoes"}},"_source":["title"]}'
post /search '{"table":"products","knn":{"field":"embedding","query":[0.0,0.1,1.0,0.0],"k":3},"_source":["title"],"limit":3}'
# /update by id, then /sql?mode=raw: the request body is one SQL statement (a form body
# 'query=...' must be URL-encoded, otherwise 400 Bad Request), the result set comes back as JSON
post /update '{"table":"products","id":18,"doc":{"price":19.0}}'
echo; echo "--> POST /sql?mode=raw  SELECT id, title, price FROM products WHERE id = 18"
wget -qO- --post-data='SELECT id, title, price FROM products WHERE id = 18' 'http://127.0.0.1:9308/sql?mode=raw'; echo
# /delete
post /delete '{"table":"products","id":18}'
