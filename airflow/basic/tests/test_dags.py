"""Checks that apply to every DAG in dags/."""

from pathlib import Path

import pytest

DAG_FILES = sorted(Path(__file__).resolve().parent.parent.joinpath("dags").glob("*.py"))


def test_no_import_errors(dagbag):
    assert dagbag.import_errors == {}


def test_every_file_defines_a_dag(dagbag):
    files_with_dags = {Path(dag.fileloc).name for dag in dagbag.dags.values()}
    assert files_with_dags == {f.name for f in DAG_FILES}


@pytest.mark.parametrize("dag_id", ["etl_example"])
def test_dag_conventions(dagbag, dag_id):
    dag = dagbag.dags.get(dag_id)
    assert dag is not None
    assert dag.tags, "tag DAGs so they can be filtered in the UI"
    assert not dag.catchup
