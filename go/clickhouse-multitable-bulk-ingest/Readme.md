# ClickHouse Bulk Ingestor Example

Bulk ingestor example using `clickhouse-go` in docker

```sh
docker-compose up --detach --build

# ssh to the app container
docker exec -it chmtbulkingest-1 bash  # if it's running
docker run -it chmtbulkingest bash  # if it's not running

```
