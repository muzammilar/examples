# Example Mock

A basic example of mocking an HTTP request in golang. See `docker-compose.yml` for details.

```sh
# build the containers
docker-compose up --build --detach

# delete the containers and their images
docker-compose down --rmi all --volumes
# Remove volumes
docker-compose rm --force --stop -v

```
