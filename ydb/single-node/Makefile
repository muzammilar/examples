.PHONY: up test status cli down

YDB := docker exec -i ydb /ydb -e grpc://localhost:2136 -d /local

up:
	docker compose up --detach --wait

# YDB runs DDL and DML as separate queries, hence one file each
test:
	for f in sql/*.sql; do echo "-- $$f"; cat $$f; $(YDB) sql -f - < $$f || exit 1; done

status:
	curl -s localhost:8765/viewer/json/cluster; echo

cli:
	docker exec -it ydb /ydb -e grpc://localhost:2136 -d /local sql

down:
	docker compose down --volumes
