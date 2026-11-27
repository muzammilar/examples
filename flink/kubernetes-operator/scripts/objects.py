"""Explicit local-only S3 setup and independent exact-output oracle."""
import csv
import io
import json
import sys
import time
import zlib

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, EndpointConnectionError

BUCKET = "flink-lab"


def main():
    store = boto3.client(
        "s3", endpoint_url="http://seaweed:8333", region_name="us-east-1",
        aws_access_key_id="flink-local", aws_secret_access_key="flink-local-secret",
        config=Config(s3={"addressing_style": "path"}, connect_timeout=3,
                      read_timeout=10, retries={"max_attempts": 2}),
    )
    if sys.argv[1] == "setup":
        deadline = time.monotonic() + 90
        probe = b"flink-local-readiness-body\n"
        while True:
            try:
                try:
                    store.create_bucket(Bucket=BUCKET)
                except ClientError as error:
                    if error.response["Error"]["Code"] not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                        raise
                store.put_object(Bucket=BUCKET, Key="_probe/readiness", Body=probe)
                with store.get_object(Bucket=BUCKET, Key="_probe/readiness")["Body"] as body:
                    if body.read() != probe:
                        raise ValueError("S3 readiness returned the wrong body")
                store.delete_object(Bucket=BUCKET, Key="_probe/readiness")
                print(json.dumps({"s3_write_read_delete_probe": "passed"}))
                return
            except ClientError as error:
                if error.response["ResponseMetadata"]["HTTPStatusCode"] < 500 or time.monotonic() > deadline:
                    raise
            except EndpointConnectionError:
                if time.monotonic() > deadline:
                    raise
            time.sleep(2)
    if sys.argv[1] != "verify":
        raise ValueError("Expected setup or verify")
    seen = bytearray(1_000_000)
    count = total = encoded = compressed = 0
    zipper = zlib.compressobj(wbits=31)
    pages = store.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix="orders/")
    for page in pages:
        for item in page.get("Contents", []):
            if not item["Key"].rsplit("/", 1)[-1].startswith("part-"):
                continue
            with store.get_object(Bucket=BUCKET, Key=item["Key"])["Body"] as body:
                data = body.read()
            encoded += len(data)
            compressed += len(zipper.compress(data))
            for row in csv.reader(io.StringIO(data.decode())):
                order, customer, amount = map(int, row)
                if not 0 <= order < len(seen) or seen[order]:
                    raise ValueError(f"Invalid/duplicate order: {row}")
                if customer != order % 10000 or amount != 100 + order % 1000:
                    raise ValueError(f"Wrong order: {row}")
                seen[order] = 1
                count += 1
                total += amount
    compressed += len(zipper.flush())
    if count != len(seen) or total != 599_500_000:
        raise ValueError(f"Incomplete/wrong data: {count=}, {total=}")
    print(json.dumps({"orders": count, "customers": 10000, "amount_cents": total,
                      "missing": 0, "wrong": 0, "duplicate": 0,
                      "csv_bytes": encoded, "gzip_reference_bytes": compressed,
                      "gzip_reference_ratio": encoded / compressed}))


if __name__ == "__main__":
    main()
