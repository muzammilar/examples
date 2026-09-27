import pytest

import sqlite
pytestmark = pytest.mark.sqlite


def test_basic_sqlite():  # pylint: disable=unused-argument
    with sqlite.SQLiteClient("myfile.db") as sqlclient:
        sqlclient.create_table()
        sqlclient.insert_data()
        assert (len(sqlclient.query_data()) >= 2)
