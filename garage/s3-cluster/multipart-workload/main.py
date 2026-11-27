"""Multipart publication boundaries using the lab's maintained Boto3 oracle."""

import hashlib
import http.client
from pathlib import Path
import json
import os
import socket
import time
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

import workload as w
from traffic import append

PART = 8 * 1024 * 1024


def event(stream, operation, **fields):
    append(stream, {"operation": operation, "time": time.time(), **fields})


def prepare(store, suffix, events, journal=None):
    key = "churn/multipart/" + w.load("case")["id"] + "/" + suffix
    data = w.body(key, 4 * PART)
    expected = w.record(key, data) | {"multipart_part_size": PART}
    if journal is not None:
        append(journal, {"stage": "attempt", "time": time.time(), "object": expected})
    upload = store.s3.create_multipart_upload(
        Bucket=w.BUCKET,
        Key=key,
        Metadata={"sha256": expected["sha256"], "version": "0"},
    )["UploadId"]
    event(events, "create", key=key, upload_id=upload, expected=expected)
    parts = []
    for number in range(1, 5):
        body = data[(number - 1) * PART : number * PART]
        reply = store.s3.upload_part(
            Bucket=w.BUCKET,
            Key=key,
            UploadId=upload,
            PartNumber=number,
            Body=body,
            ContentMD5=w.md5(body),
        )
        parts.append({"PartNumber": number, "ETag": reply["ETag"]})
        event(
            events,
            "part_ack",
            key=key,
            upload_id=upload,
            part=parts[-1],
            size=len(body),
        )
    store.missing(key)
    return expected, upload, parts


def request(store, expected, upload, parts):
    # Sign with the SDK; standard HTTP only controls where transport is interrupted.
    address = urlsplit(
        store.s3.generate_presigned_url(
            "complete_multipart_upload",
            Params={"Bucket": w.BUCKET, "Key": expected["key"], "UploadId": upload},
            ExpiresIn=120,
            HttpMethod="POST",
        )
    )
    if (
        address.scheme != "http"
        or address.hostname != "garage0"
        or address.port != 3900
    ):
        raise RuntimeError("Refusing an unexpected completion endpoint")
    root = ET.Element("CompleteMultipartUpload")
    for part in parts:
        element = ET.SubElement(root, "Part")
        ET.SubElement(element, "PartNumber").text = str(part["PartNumber"])
        ET.SubElement(element, "ETag").text = part["ETag"]
    body = ET.tostring(root)
    connection = http.client.HTTPConnection(address.hostname, address.port, timeout=15)
    connection.putrequest("POST", address.path + "?" + address.query)
    connection.putheader("Content-Length", str(len(body)))
    connection.putheader("Content-Type", "application/xml")
    connection.endheaders()
    return connection, body


def complete_raw(direct, expected, upload, parts, events, *, hold=False, discard=False):
    connection, body = request(direct, expected, upload, parts)
    started = time.monotonic()
    try:
        if hold:
            connection.send(body[: len(body) // 2])
            w.save(
                "request-held",
                {
                    "peer": connection.sock.getpeername(),
                    "bytes_sent": len(body) // 2,
                    "content_length": len(body),
                    "key": expected["key"],
                },
            )
            end = time.monotonic() + 60
            while not (w.ROOT / "release-request").exists():
                if time.monotonic() > end:
                    raise TimeoutError("Controller did not release the held request")
                time.sleep(0.1)
            connection.send(body[len(body) // 2 :])
        else:
            connection.send(body)
        response = connection.getresponse()
        if discard:
            # Headers alone are not an S3 completion ACK; the XML can contain an error.
            event(
                events,
                "response_discarded",
                status=response.status,
                key=expected["key"],
                seconds=time.monotonic() - started,
                application_ack=False,
            )
            if connection.sock:
                connection.sock.shutdown(socket.SHUT_RDWR)
            response.close()
            return
        payload = response.read()
        tag = ET.fromstring(payload).tag.rsplit("}", 1)[-1]
        if response.status != 200 or tag != "CompleteMultipartUploadResult":
            raise RuntimeError(
                "Raw completion rejected: "
                + str(response.status)
                + " "
                + payload[:200].decode(errors="replace")
            )
        event(
            events,
            "complete_ack",
            key=expected["key"],
            seconds=time.monotonic() - started,
        )
    finally:
        connection.close()


def fresh_verify(expected):
    store = w.Store()
    try:
        store.get(expected)
    finally:
        store.s3.close()


def main():
    w.save(
        "multipart-source",
        {"sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
    )
    store = w.Store()
    # Clone the pinned SDK config with an explicit local route, no credential discovery.
    import boto3

    direct = w.Store()
    direct.s3.close()
    direct.s3 = boto3.client(
        "s3",
        endpoint_url="http://garage0:3900",
        region_name="us-east-1",
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        config=store.s3.meta.config,
    )
    published = []
    summary = {}
    with (w.ROOT / "multipart.jsonl").open("x") as journal, (
        w.ROOT / "multipart-events.jsonl"
    ).open("x") as events:
        expected, upload, parts = prepare(direct, "control", events, journal)
        complete_raw(direct, expected, upload, parts, events)
        append(journal, {"stage": "ack", "key": expected["key"], "time": time.time()})
        fresh_verify(expected)
        published.append(expected)
        summary["raw_transport_control"] = "passed"
        expected, upload, parts = prepare(direct, "interrupted", events, journal)
        try:
            complete_raw(direct, expected, upload, parts, events, hold=True)
        except (OSError, http.client.HTTPException) as error:
            event(
                events,
                "completion_transport_error",
                key=expected["key"],
                error=str(error)[:300],
            )
        else:
            raise RuntimeError(
                "Interrupted serving process unexpectedly returned a completion ACK"
            )
        # Wait for the controller's verified native restart, not arbitrary sleep.
        end = time.monotonic() + 60
        while not (w.ROOT / "server-recovered").exists():
            if time.monotonic() > end:
                raise TimeoutError("Serving process did not recover")
            time.sleep(0.1)
        store.s3.close()
        store = w.Store()
        store.missing(expected["key"])
        listed = store.s3.list_parts(
            Bucket=w.BUCKET, Key=expected["key"], UploadId=upload
        )["Parts"]
        if [
            {"PartNumber": p["PartNumber"], "ETag": p["ETag"]} for p in listed
        ] != parts or any(p["Size"] != PART for p in listed):
            raise RuntimeError(
                "Acknowledged parts changed/disappeared during serving-node loss"
            )
        store.s3.complete_multipart_upload(
            Bucket=w.BUCKET,
            Key=expected["key"],
            UploadId=upload,
            MultipartUpload={"Parts": parts},
        )
        append(journal, {"stage": "ack", "key": expected["key"], "time": time.time()})
        fresh_verify(expected)
        published.append(expected)
        direct.s3.close()
        direct.s3 = boto3.client(
            "s3",
            endpoint_url="http://garage0:3900",
            region_name="us-east-1",
            aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
            config=store.s3.meta.config,
        )
        summary["interrupted_request"] = {
            "passed": True,
            "parts_retained": 4,
            "resumed_same_upload_id": True,
        }
        expected, upload, parts = prepare(direct, "lost-response", events, journal)
        complete_raw(direct, expected, upload, parts, events, discard=True)
        append(
            journal,
            {
                "stage": "ambiguous",
                "key": expected["key"],
                "time": time.time(),
                "error": "Completion response deliberately discarded before application ACK",
            },
        )
        fresh_verify(expected)
        published.append(expected)
        summary["lost_response"] = {
            "passed": True,
            "published_without_application_ack": True,
            "mutation_replayed": False,
        }
        expected, upload, parts = prepare(store, "abort", events)
        store.s3.abort_multipart_upload(
            Bucket=w.BUCKET, Key=expected["key"], UploadId=upload
        )
        event(events, "abort_ack", key=expected["key"], upload_id=upload)
        store.missing(expected["key"])
        pending = store.s3.list_multipart_uploads(
            Bucket=w.BUCKET, Prefix=expected["key"]
        ).get("Uploads", [])
        if any(p["UploadId"] == upload for p in pending):
            raise RuntimeError("Aborted upload remains listed")
        summary["abort"] = {
            "passed": True,
            "final_key_absent": True,
            "upload_unlisted": True,
            "physical_reclamation": "unrun",
        }
        w.save("multipart-published", published)
        w.save(
            "multipart-summary",
            summary | {"object_bytes": 4 * PART, "completed_objects": 3},
        )
    store.s3.close()
    direct.s3.close()


if __name__ == "__main__":
    main()
