"""A small extract -> transform -> load DAG written with the Airflow 3 TaskFlow API."""

from __future__ import annotations

import pendulum
from airflow.sdk import dag, task


@dag(
    schedule=None,  # triggered manually (`make test` or the UI)
    start_date=pendulum.datetime(2025, 1, 1, tz="UTC"),
    catchup=False,
    tags=["example"],
)
def etl_example():
    @task
    def extract() -> dict[str, float]:
        return {"1001": 301.27, "1002": 433.21, "1003": 502.22}

    @task(multiple_outputs=True)
    def transform(orders: dict[str, float]) -> dict[str, float]:
        return {"count": len(orders), "total": round(sum(orders.values()), 2)}

    @task
    def load(count: int, total: float) -> None:
        print(f"orders={count} total={total}")

    summary = transform(extract())
    load(summary["count"], summary["total"])


etl_example()
