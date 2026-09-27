# ClickHouse Struct Ingestor Example

Struct ingestor example using `clickhouse-go` in docker. Please see the `printFields` function that facilitates slice creation.

```sh
docker-compose up --detach --build

# ssh to the app container
docker exec -it chstructingest-1 bash  # if it's running
docker run -it chstructingest bash  # if it's not running

```
