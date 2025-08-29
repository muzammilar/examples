# Hello Roach

A simple CI script to install and setup cockroachdb on a single node.

## Testing

To test the CockroachDB behaviour, do the following:

```bash
# run the containers
docker compose up --detach --build

# ssh to the container and create tables
# you could also use docker entrypoint or a separate container
docker exec -it roach-single ./cockroach sql --insecure --echo-sql --file /sqlcommands/tables.sql
docker exec -it roach-single ./cockroach sql --insecure --echo-sql --file /sqlcommands/inserts.sql
# get the interactive shell
docker exec -it roach-single ./cockroach sql --insecure

# debug command (to debug configs): `docker-compose run clickhouse-server-01 bash`

```


## Cleanup

```sh
# Exit the container and cleanup
docker compose down --volumes

# Use `docker-compose down --rmi all --volumes` with above to images as well
# Remove everything (and remove volumes). Networks are not removed here.
docker compose rm --force --stop -v

# if the container is already stopped
docker compose rm --force -v

# delete local database
rm -rf cockroach-data/crdb

```
