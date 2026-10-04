#!/bin/bash
# Topics, keyed produce/consume, consumer groups: rpk inside the broker container.
set -euo pipefail
set -x
rpk cluster info
rpk topic delete orders >/dev/null 2>&1 || true
rpk topic create orders --partitions 3 --replicas 1 --topic-config retention.ms=86400000
# key:value per line (-f '%k %v\n'); same key -> same partition
printf '%s\n' 'alice {"id":1,"amount":30}' 'bob {"id":2,"amount":12}' 'alice {"id":3,"amount":7}' \
	'carol {"id":4,"amount":99}' 'bob {"id":5,"amount":1}' | rpk topic produce orders -f '%k %v\n'
rpk topic describe orders --print-partitions
rpk topic consume orders --num 5 --offset start --format '%p/%o %k %v\n' | sort
# consumer group: read 3, commit, then the group's lag is 2
rpk topic consume orders --group billing --num 3 --offset start --format '%p/%o %k\n' >/dev/null
rpk group describe billing
lag=$(rpk group describe billing --format json | grep -o '"total_lag":[0-9]*' | cut -d: -f2)
set +x
[ "$lag" = 2 ] || { echo "FAIL: expected total lag 2, got '$lag'"; exit 1; }
echo "OK: 5 records, group billing lag 2"
