.PHONY: up test status cli down

YDB := docker exec -i ydb /ydb -e grpc://localhost:2136 -d /local

up:
	docker compose up --detach --wait

# YDB runs DDL and DML as separate queries, hence one file each; then show the
# TTL, index and store type the schema set up
test:
	for f in sql/*.sql; do echo; echo "==== $$f"; cat $$f; $(YDB) sql -f - < $$f || exit 1; done
	$(YDB) scheme describe orders | grep -E '^<|GlobalSync|Ttl|Column name|Expire'
	$(YDB) scheme describe events | head -1

status:
	curl -s localhost:8765/viewer/json/cluster; echo

cli:
	docker exec -it ydb /ydb -e grpc://localhost:2136 -d /local sql

down:
	docker compose down --volumes
