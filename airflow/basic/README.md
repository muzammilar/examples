# Apache Airflow — basic (Docker Compose)

The [official Airflow 3.3 Docker Compose setup](https://airflow.apache.org/docs/apache-airflow/stable/howto/docker-compose/index.html):
CeleryExecutor with Redis as the broker and PostgreSQL as the metadata DB. Services: `airflow-apiserver`
(UI + REST API on `localhost:8080`), `airflow-scheduler`, `airflow-dag-processor`, `airflow-worker`,
`airflow-triggerer`, plus a one-shot `airflow-init` that migrates the DB and creates the admin user.

Changes from upstream:

- `AIRFLOW__CORE__LOAD_EXAMPLES: 'false'`, so the UI shows just [`dags/etl_example.py`](dags/etl_example.py),
  a TaskFlow extract → transform → load DAG.
- StatsD metrics are on and go to `statsd-exporter` → Prometheus → Grafana. Grafana starts with a provisioned
  Prometheus datasource and the **Airflow** dashboard ([`metrics/grafana/dashboards/airflow.json`](metrics/grafana/dashboards/airflow.json))
  as its home page: scheduler health, task/DAG run outcomes and durations, executor and pool slots, DAG parsing;
  `metrics/statsd-mappings.yml` turns Airflow's dotted names into labelled Prometheus metrics.

```bash
make up       # start and wait for every service to be healthy (first start takes a minute or two)
make e2e      # scripts/e2e.sh: unpause + trigger etl_example, wait for the run to succeed on the worker
make status   # API health and the list of DAGs
make cli      # shell in a container with the airflow CLI (profile `debug`)
make flower   # optional Celery Flower UI on localhost:5555
make down     # remove containers and the Postgres volume
```

- Airflow UI / REST API: http://localhost:8080 (login `airflow` / `airflow`)
- Grafana: http://localhost:13001 (anonymous admin) — override with `GRAFANA_PORT`
- Prometheus: http://localhost:19091 — override with `PROMETHEUS_PORT`
- Task logs are written to `logs/`; `config/airflow.cfg` is generated on first start.

## Developing DAGs

DAGs are a [uv](https://docs.astral.sh/uv/) project pinned to the same `apache-airflow` as the image, so they
can be linted and tested without containers:

```bash
uv sync       # .venv with apache-airflow, pytest, ruff
make lint     # ruff check + ruff format --check
make test     # pytest: every DAG imports cleanly, etl_example structure and task logic
```

`tests/conftest.py` points `AIRFLOW_HOME` at a temp dir and loads `dags/` with a `DagBag`; no metadata DB is
needed. Task callables are tested directly via `dag.get_task(...).python_callable`.

Needs ~4 GB of memory for Docker. `.env` sets `AIRFLOW_UID`; on Linux set it to `$(id -u)` so files
created under `dags/`, `logs/`, `config/` and `plugins/` are owned by you. `make e2e` needs `jq` on the host.
This setup is for local development only.
