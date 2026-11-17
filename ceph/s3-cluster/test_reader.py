"""An acknowledged key disappearing is not an ordinary availability error."""

import unittest
from unittest.mock import Mock

from botocore.exceptions import ClientError
from traffic import checked_get


class ConsistentRead(unittest.TestCase):
    def test_missing_ack_is_a_contract_failure(self):
        store = Mock()
        store.get.side_effect = ClientError(
            {
                "Error": {"Code": "NoSuchKey"},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            },
            "GetObject",
        )
        with self.assertRaisesRegex(RuntimeError, "acknowledged immutable"):
            checked_get(store, {"key": "ack"})

    def test_503_remains_a_recorded_availability_error(self):
        store = Mock()
        store.get.side_effect = ClientError(
            {
                "Error": {"Code": "ServiceUnavailable"},
                "ResponseMetadata": {"HTTPStatusCode": 503},
            },
            "GetObject",
        )
        with self.assertRaises(ClientError):
            checked_get(store, {"key": "ack"})
