#!/bin/sh
# Redpanda Console's REST API (what the web UI at http://localhost:8080 calls); runs in the
# console container. Shows the topics, schema subjects and groups created by 01-03.
set -eu
api() { wget -qO- "http://127.0.0.1:8080/api/$1"; }
t=$(api topics); s=$(api schema-registry/subjects); g=$(api consumer-groups)
echo "topics:   $(echo "$t" | grep -o '"topicName":"[^"]*"' | cut -d'"' -f4 | tr '\n' ' ')"
echo "subjects: $(echo "$s" | grep -o '"name":"[^"]*"' | cut -d'"' -f4 | tr '\n' ' ')"
echo "groups:   $(echo "$g" | grep -o '"groupId":"[^"]*"' | cut -d'"' -f4 | tr '\n' ' ')"
for want in '"topicName":"orders"' '"topicName":"payments"' '"topicName":"clicks"'; do
	echo "$t" | grep -q "$want" || { echo "FAIL: console does not list $want"; exit 1; }
done
echo "$s" | grep -q '"name":"payments-value"' || { echo "FAIL: console does not list payments-value"; exit 1; }
echo "OK: Console sees the topics, the schema subject and the groups"
