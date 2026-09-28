"""Structure and task logic of dags/etl_example.py."""

import pytest


@pytest.fixture(scope="module")
def dag(dagbag):
    return dagbag.dags["etl_example"]


def test_tasks_and_dependencies(dag):
    assert {t.task_id: t.downstream_task_ids for t in dag.tasks} == {
        "extract": {"transform"},
        "transform": {"load"},
        "load": set(),
    }


def test_extract_returns_orders(dag):
    orders = dag.get_task("extract").python_callable()
    assert orders and all(isinstance(v, float) for v in orders.values())


def test_transform_summarizes_orders(dag):
    transform = dag.get_task("transform").python_callable
    assert transform({"a": 1.25, "b": 2.5}) == {"count": 2, "total": 3.75}
    assert transform({}) == {"count": 0, "total": 0}


def test_load_prints_summary(dag, capsys):
    dag.get_task("load").python_callable(count=2, total=3.75)
    assert capsys.readouterr().out.strip() == "orders=2 total=3.75"
