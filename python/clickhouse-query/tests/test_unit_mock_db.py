import unittest.mock

import pytest
import urllib3.exceptions
from box import Box

from xmpl import ClickHouseClient

pytestmark = pytest.mark.unit_test


# mock the clickhouse client
@unittest.mock.patch('clickhouse_connect.get_client', return_value=None)
@unittest.mock.patch.object(ClickHouseClient, '__exit__')
@unittest.mock.patch.object(ClickHouseClient, '__enter__')
def test_context_manager_success(chclient_entr_mock, chclient_exit_mock, ch_client_mock):  # pylint: disable=unused-argument
    """ Test `with` statement of the ClickHouseClient"""

    # basic test configurations
    conf = Box({
        "host": 'fake.com',
        "port": 123,
        "username": 'default',
        "password": ''
    })

    # no error is raised
    with ClickHouseClient(config=conf) as _:
        pass

    chclient_entr_mock.assert_called_once()
    chclient_exit_mock.assert_called_once()
    ch_client_mock.assert_called_with(
        host='fake.com', port=123, user='user', password='pass', secure=False)


# Test side effects and exceptions
@unittest.mock.patch('clickhouse_connect.get_client', side_effect=urllib3.exceptions.ConnectionError('test exception is raised by connection'))
@unittest.mock.patch.object(ClickHouseClient, '__enter__')
@unittest.mock.patch.object(ClickHouseClient, '__exit__')
def test_context_manager_failure(chclient_exit_mock, chclient_entr_mock, ch_client_mock):  # pylint: disable=unused-argument
    """ Test `with` statement of the ClickHouseClient """

    # basic test configurations
    conf = Box({
        "host": 'fake.com',
        "port": 8123,
        "username": 'default',
        "password": ''
    })


    # error is raised
    with pytest.raises(urllib3.exceptions.ConnectionError):
        with ClickHouseClient(config=conf) as _:
            pass
