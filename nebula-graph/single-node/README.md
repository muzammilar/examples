# NebulaGraph — single node

The smallest working NebulaGraph cluster: one `metad` (metadata/schema), one
`storaged` (data), one `graphd` (query engine), plus `nebula-console` as a
`tools` profile service. `make up` registers storaged with metad (`ADD HOSTS`).

```bash
make up       # start, register storaged (ADD HOSTS), wait until it is ONLINE
make test     # run ngql/*.ngql: space, tag/edge/index, inserts, GO, LOOKUP, MATCH, FIND PATH
make status   # SHOW HOSTS
make cli      # interactive nebula-console
make down     # remove containers and volumes
```

- Graph service: `localhost:9669`, user `root`, password `nebula` (auth is off by default; any password works)
- metad / storaged are only reachable inside the compose network (9559 / 9779)

`--heartbeat_interval_secs=2` (default 10) so new spaces and schema propagate quickly;
`ngql/01-schema.ngql` still sleeps after `CREATE SPACE` and after the DDL, because
graphd and storaged only see them after a few heartbeats. `replica_factor = 1`
since there is one storaged. Service logs are files under `/usr/local/nebula/logs`
in each container, not `docker logs`.

The first query touching storage on a fresh cluster can take ~5s while graphd
opens its storage client connections; later ones are milliseconds.
