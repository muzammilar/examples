import os
import tempfile
from pathlib import Path

import pytest

# Airflow reads its config on first import: point it at a throwaway home before any test imports it.
os.environ.setdefault("AIRFLOW_HOME", tempfile.mkdtemp(prefix="airflow-home-"))
os.environ["AIRFLOW__CORE__LOAD_EXAMPLES"] = "False"
os.environ["AIRFLOW__CORE__UNIT_TEST_MODE"] = "True"

DAGS_FOLDER = Path(__file__).resolve().parent.parent / "dags"


@pytest.fixture(scope="session")
def dagbag():
    from airflow.dag_processing.dagbag import DagBag

    return DagBag(dag_folder=DAGS_FOLDER)
