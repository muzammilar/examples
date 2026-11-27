"""Reject false completion ACKs and accidental public endpoints."""

import http.client
import unittest
from unittest.mock import Mock, patch

import multipart_workload as m


class CompletionProtocol(unittest.TestCase):
    def test_http_200_error_xml_is_not_an_ack(self):
        connection = Mock()
        connection.getresponse.return_value.status = 200
        connection.getresponse.return_value.read.return_value = (
            b"<Error><Code>InternalError</Code></Error>"
        )
        with patch.object(
            m, "request", return_value=(connection, b"xml")
        ), patch.object(m, "event") as event:
            with self.assertRaisesRegex(RuntimeError, "Raw completion rejected"):
                m.complete_raw(Mock(), {"key": "test"}, "upload", [], Mock())
            event.assert_not_called()
        connection.close.assert_called_once()

    def test_complete_xml_is_required_for_ack(self):
        connection = Mock()
        connection.getresponse.return_value.status = 200
        connection.getresponse.return_value.read.return_value = b'<CompleteMultipartUploadResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><ETag>etag</ETag></CompleteMultipartUploadResult>'
        with patch.object(
            m, "request", return_value=(connection, b"xml")
        ), patch.object(m, "event") as event:
            m.complete_raw(Mock(), {"key": "test"}, "upload", [], Mock())
            self.assertEqual(event.call_args.args[1], "complete_ack")
        connection.close.assert_called_once()

    def test_discarded_response_never_reads_xml_or_acks(self):
        connection = Mock()
        connection.getresponse.return_value.status = 200
        with patch.object(
            m, "request", return_value=(connection, b"xml")
        ), patch.object(m, "event") as event:
            m.complete_raw(Mock(), {"key": "test"}, "upload", [], Mock(), discard=True)
            self.assertFalse(event.call_args.kwargs["application_ack"])
        connection.getresponse.return_value.read.assert_not_called()
        connection.close.assert_called_once()

    def test_selected_seaweed_route_uses_only_owned_endpoint(self):
        store = Mock()
        store.s3.generate_presigned_url.return_value = (
            "http://s3api0:8333/bucket/key?uploadId=test"
        )
        with patch.dict(
            m.os.environ, {"MULTIPART_ENDPOINT": "http://s3api0:8333"}
        ), patch.object(m.http.client, "HTTPConnection") as connect:
            connection, body = m.request(store, {"key": "test"}, "upload", [])
            connect.assert_called_once_with("s3api0", 8333, timeout=15)
            self.assertIs(connection, connect.return_value)
            self.assertIn(b"CompleteMultipartUpload", body)

    def test_unknown_route_rejected_without_network_io(self):
        with patch.dict(
            m.os.environ, {"MULTIPART_ENDPOINT": "http://unowned:9000"}
        ), patch.object(m.http.client, "HTTPConnection") as connect:
            with self.assertRaisesRegex(RuntimeError, "unexpected multipart endpoint"):
                m.local_endpoint()
            connect.assert_not_called()

    def test_unexpected_host_rejected_before_network_io(self):
        store = Mock()
        store.s3.generate_presigned_url.return_value = "http://unowned:3900/bucket/key"
        with patch.object(m.http.client, "HTTPConnection") as connect:
            with self.assertRaisesRegex(RuntimeError, "unexpected completion endpoint"):
                m.request(store, {"key": "test"}, "upload", [])
            connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
