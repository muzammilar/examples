import pytest

import cockroachdb
pytestmark = pytest.mark.cockroach


def test_basic_sqlite():  # pylint: disable=unused-argument
    with cockroachdb.CockroachClient("root", "mypassword", "crdb_local") as crdbclient:
        crdbclient.create_table()  # Create the table if it doesn't exist
        crdbclient.insert_user("Alice", 30)  # Insert sample data
        crdbclient.insert_user("Bob", 25)
        res = crdbclient.fetch_users()  # Query and display all users
        assert (len(res) >= 2)
