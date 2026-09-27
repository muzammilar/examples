# ClickHouse Bulk Ingestor Example

Bulk ingestor example using `clickhouse-go` in docker.

**NOTE:** `clickhouse-go` (as of Jan 2022) is expensive (in terms of time) due to its use of Golang's reflection module. It should not be used in production settings.

```sh
docker-compose up --detach --build

# ssh to the app container
docker exec -it chmtbulkingest-1 bash  # if it's running
docker run -it chmtbulkingest bash  # if it's not running

```
