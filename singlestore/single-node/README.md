# SingleStore — single node

The official [SingleStore Dev Image](https://github.com/singlestore-labs/singlestoredb-dev-image)
(`ghcr.io/singlestore-labs/singlestoredb-dev:0.2.85`, engine 9.1.1 from the RC channel) on
Docker Compose. One container runs a two-process cluster: a master aggregator (port 3306) and
one leaf (port 3307), plus SingleStore Studio and the Data API.

```bash
make up       # start, wait for the healthcheck, print version, SHOW AGGREGATORS / SHOW LEAVES
make test     # run sql/*.sql: reference / rowstore / columnstore tables with SHARD KEY + SORT KEY,
              # 50k customers + 600k orders generated server-side, colocated vs broadcast join
              # (EXPLAIN), PROFILE, window function, segment compression + block elimination,
              # hash-index point lookup, UPDATE/DELETE on columnstore, rowstore transaction,
              # JSON (::$ / ::%), VECTOR with <*> (DOT_PRODUCT) and <-> (EUCLIDEAN_DISTANCE),
              # partitions per leaf
make status   # container state, leaves, databases, rows/memory per table
make cli      # interactive client
make down     # remove the container and its volume
```

- MySQL protocol: `localhost:3336` (`mysql -h 127.0.0.1 -P 3336 -u root -pSingleStore-Demo-1`)
- Studio (web UI): http://localhost:18080, [Data API](https://docs.singlestore.com/db/v9.0/reference/data-api/) (SQL over HTTP): http://localhost:19000
- Host ports default to 3336 / 18080 / 19000 to stay clear of a local MySQL and other
  services; override with `SINGLESTORE_PORT`, `SINGLESTORE_STUDIO_PORT`, `SINGLESTORE_HTTP_PORT`.
- Root password: `SINGLESTORE_PASSWORD` (default `SingleStore-Demo-1`), passed to both
  `docker compose` and the Makefile.

Licensing: no license key needed. Without `SINGLESTORE_LICENSE` the image applies a free
license that is built into it (see its [`start.sh`](https://github.com/singlestore-labs/singlestoredb-dev-image/blob/main/scripts/start.sh)),
valid for "development, prototyping, and functional testing" on up to 8 cores / 64 GB
(hence `cpus: 8` in the compose file), and since 0.2.40 each database has at most two
partitions. Set `SINGLESTORE_LICENSE` to use your own key.

The image is amd64 only (`platform: linux/amd64`). On Apple silicon it runs under Rosetta and
needs x86-64-v3 support, i.e. macOS 26 or newer (tested on macOS 26.5, Docker Desktop).
