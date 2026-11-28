"""Native signal request and observed completion; a separate DAG verifies a fenced cut."""

from datetime import datetime, timedelta, timezone

from airflow.sdk import DAG
from airflow.providers.standard.operators.bash import BashOperator


def command(task_id, mode):
    return BashOperator(
        task_id=task_id,
        bash_command="exec /usr/local/bin/orders " + mode,
        env={"RUN_ID": "{{ dag_run.run_id }}"},
        append_env=True,
        retries=1,
        retry_delay=timedelta(seconds=5),
        execution_timeout=timedelta(minutes=6),
        do_xcom_push=False,
    )


with DAG(
    "margin_backfill",
    schedule=None,
    start_date=datetime(2026, 11, 27, tzinfo=timezone.utc),
    catchup=False,
    max_active_runs=1,
) as backfill:
    request = command("request_incremental_snapshot", "request-backfill")
    completed = command("observe_native_completion", "wait-backfill")
    request >> completed


with DAG(
    "margin_reconciliation",
    schedule=None,
    start_date=datetime(2026, 11, 27, tzinfo=timezone.utc),
    catchup=False,
    max_active_runs=1,
) as reconciliation:
    command("compare_every_field_on_three_replicas", "verify")
