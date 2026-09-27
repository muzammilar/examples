# SQLC Example

Example of [sqlc](https://sqlc.dev/) with postgresql.


### Using Docker Image

```sh

docker pull sqlc/sqlc

# Run sqlc using docker run:

docker run --rm -v $(pwd)/db:/src -w /src sqlc/sqlc generate

#  Run sqlc using docker run in the Command Prompt on Windows (powershell):

docker run --rm -v ${PWD}/db:/src -w /src sqlc/sqlc generate

# docker compose your program
docker compose up --build

```

### SQL Schema Migration Tools

For schema migration tools, checks your favourite search engine. The following tools are taken from [betterprogramming.pub](https://betterprogramming.pub/searching-for-best-approach-in-go-migrations-c3fa52afadb0):

* [golang-migrate](https://github.com/golang-migrate/migrate)
* [pressly/goose](https://github.com/pressly/goose)
* [sql-migrate](https://github.com/rubenv/sql-migrate)
