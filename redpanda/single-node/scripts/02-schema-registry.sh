#!/bin/bash
# Schema Registry (built into the broker, port 8081): Avro schema, encode on produce, decode on
# consume, compatibility check rejects a breaking change.
set -euo pipefail
cd /scripts/schemas
set -x
rpk registry subject delete payments-value --permanent >/dev/null 2>&1 || true
rpk topic delete payments >/dev/null 2>&1 || true
rpk topic create payments --partitions 1 --replicas 1
rpk registry schema create payments-value --schema payment-v1.avsc
rpk registry compatibility-level set payments-value --level BACKWARD
# --schema-id=topic: rpk looks up the latest payments-value schema and Avro-encodes the JSON
echo '{"id":"p-1","amount_cents":1250,"currency":"EUR"}' | rpk topic produce payments --schema-id=topic
rpk topic consume payments --num 1 --offset start --use-schema-registry=value --format '%v\n'
# v2 adds a field with a default: BACKWARD-compatible
rpk registry schema check-compatibility payments-value --schema payment-v2-ok.avsc --schema-version latest
rpk registry schema create payments-value --schema payment-v2-ok.avsc
# v3 adds a required field (no default): not BACKWARD-compatible, must be rejected
set +e
rpk registry schema check-compatibility payments-value --schema payment-v3-breaking.avsc --schema-version latest
rpk registry schema create payments-value --schema payment-v3-breaking.avsc
rc=$?
set -e
rpk registry schema list payments-value
set +x
[ "$rc" != 0 ] || { echo "FAIL: breaking schema was accepted"; exit 1; }
echo "OK: v1 + v2 registered, breaking v3 rejected"
