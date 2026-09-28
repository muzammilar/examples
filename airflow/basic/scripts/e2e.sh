#!/usr/bin/env bash
# Unpause and trigger the etl_example DAG, then wait for the run to finish on the Celery worker.
set -euo pipefail
DAG=etl_example
af() { docker compose exec -T airflow-scheduler airflow "$@"; }

# the dag-processor parses dags/ asynchronously; wait until the DAG is registered
for _ in $(seq 60); do af dags details "$DAG" >/dev/null 2>&1 && break; sleep 2; done
af dags unpause "$DAG" >/dev/null
RUN=manual__$(date -u +%Y%m%dT%H%M%S)
af dags trigger "$DAG" --run-id "$RUN" >/dev/null
echo "==> triggered $DAG run $RUN"

for _ in $(seq 90); do
  STATE=$(af dags list-runs "$DAG" -o json | jq -r --arg r "$RUN" '.[] | select(.run_id == $r) | .state')
  case $STATE in
    success) break ;;
    failed) echo "run failed"; af tasks states-for-dag-run "$DAG" "$RUN"; exit 1 ;;
  esac
  sleep 2
done
echo "==> run state: $STATE"
af tasks states-for-dag-run "$DAG" "$RUN"
[ "$STATE" = success ]
