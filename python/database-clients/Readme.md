# Database Query Example

---

```sh
docker compose up --build --force-recreate --detach
```


##### CockroachDB
```sh
# Connect to running cockroach container and then execute this
`cockroach sql --url postgres://root:mypassword@localhost:26257/defaultdb?sslmode=disable`
```
