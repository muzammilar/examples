# Full-text, numeric, tag and vector search over JSON documents (RediSearch-compatible FT.*)
c() { valkey-cli -h dragonfly "$@"; }

c FT.DROPINDEX products > /dev/null
c FT.CREATE products ON JSON PREFIX 1 product: SCHEMA \
	'$.name' AS name TEXT '$.price' AS price NUMERIC SORTABLE '$.category' AS category TAG \
	'$.embedding' AS embedding VECTOR FLAT 6 TYPE FLOAT32 DIM 4 DISTANCE_METRIC L2
c JSON.SET product:1 '$' '{"name":"red running shoe","price":80,"category":"shoes","embedding":[1,0,0,0]}' > /dev/null
c JSON.SET product:2 '$' '{"name":"blue running shirt","price":25,"category":"shirts","embedding":[0,1,0,0]}' > /dev/null
c JSON.SET product:3 '$' '{"name":"trail shoe","price":120,"category":"shoes","embedding":[0.9,0.1,0,0]}' > /dev/null
c JSON.SET product:4 '$' '{"name":"wool hat","price":15,"category":"hats","embedding":[0,0,1,0]}' > /dev/null

echo "== FT.SEARCH '@name:running @price:[0 50]'"
out=$(c FT.SEARCH products '@name:running @price:[0 50]' RETURN 1 name)
echo "$out"
[ "$(echo "$out" | head -2 | tr '\n' ' ')" = "1 product:2 " ] || { echo "FAIL: expected product:2 only"; exit 1; }

echo "== FT.SEARCH '@category:{shoes}' SORTBY price DESC"
out=$(c FT.SEARCH products '@category:{shoes}' SORTBY price DESC RETURN 1 name)
echo "$out"
[ "$(echo "$out" | grep '^product:' | tr '\n' ' ')" = "product:3 product:1 " ] || { echo "FAIL: expected product:3, product:1"; exit 1; }

echo "== FT.AGGREGATE: count and average price per category"
# one row per group; the first line of the reply is the number of groups
c FT.AGGREGATE products '*' GROUPBY 1 @category REDUCE COUNT 0 AS n REDUCE AVG 1 @price AS avg_price SORTBY 2 @n DESC |
	tail -n +2 | paste -d ' ' - - - - - - | tee /tmp/agg
shoes=$(grep -w shoes /tmp/agg)
echo "$shoes" | grep -qw 'n 2' && echo "$shoes" | grep -qw 'avg_price 100' ||
	{ echo "FAIL: expected 2 shoes, avg price 100"; exit 1; }

# the query vector goes in as raw little-endian float32 bytes; -x reads the last argument from stdin
# because the shell can't hold NUL bytes in a variable. [1,0,0,0]:
q='\000\000\200\077\000\000\000\000\000\000\000\000\000\000\000\000'
echo "== KNN 2 nearest to [1,0,0,0]"
out=$(printf "$q" | c -x FT.SEARCH products '*=>[KNN 2 @embedding $v AS dist]' SORTBY dist RETURN 2 name dist PARAMS 2 v)
echo "$out"
[ "$(echo "$out" | grep '^product:' | tr '\n' ' ')" = "product:1 product:3 " ] || { echo "FAIL: expected product:1, product:3"; exit 1; }

echo "== KNN 2 nearest to [1,0,0,0] with price <= 100"
out=$(printf "$q" | c -x FT.SEARCH products '@price:[0 100] =>[KNN 2 @embedding $v AS dist]' SORTBY dist RETURN 2 name dist PARAMS 2 v)
echo "$out"
[ "$(echo "$out" | grep '^product:' | tr '\n' ' ')" = "product:1 product:2 " ] || { echo "FAIL: expected product:1, product:2"; exit 1; }
