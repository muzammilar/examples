# Example Mock

A basic example of mocking an HTTP request using Golang's [mock](https://github.com/golang/mock) library. See `docker-compose.yml` for details.

```sh
# build the containers
docker-compose up --build --detach

# delete the containers and their images
docker-compose down --rmi all --volumes
# Remove volumes
docker-compose rm --force --stop -v

```
