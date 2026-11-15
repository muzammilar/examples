"""Shared S3 byte-integrity, compatibility, timing and external-ACK contract."""

import base64
import concurrent.futures
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

ROOT = Path("/results")
BUCKET = os.environ.get("BUCKET", "lab-objects")
SEED = "20261115"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def number(name, default, low=1, high=1000000):
    value = int(os.environ.get(name, default))
    if not low <= value <= high:
        raise ValueError(f"{name} must be {low}..{high}")
    return value


def save(name, value):
    ROOT.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=ROOT, delete=False) as f:
        json.dump(value, f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
        tmp = f.name
    os.replace(tmp, ROOT / (name + ".json"))


def load(name, default=None):
    p = ROOT / (name + ".json")
    return json.loads(p.read_text()) if p.exists() else default


def body(key, size, version=0):
    return hashlib.shake_256(f"{SEED}:{key}:{version}".encode()).digest(size)


def record(key, data, version=0):
    return {
        "key": key,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "version": version,
    }


def md5(data):
    return base64.b64encode(hashlib.md5(data, usedforsecurity=False).digest()).decode()


def timing(values):
    ordered = sorted(values)
    return {
        "samples": len(values),
        **{
            f"p{p}_ms": 1000 * ordered[max(0, math.ceil(len(ordered) * p / 100) - 1)]
            for p in [50, 95, 99]
        },
        "max_ms": 1000 * max(ordered),
    }


class Store:
    def __init__(self):
        self.s3 = boto3.client(
            "s3",
            endpoint_url=os.environ["S3_ENDPOINT"],
            region_name="us-east-1",
            aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path", "payload_signing_enabled": True},
                retries={"total_max_attempts": 1, "mode": "standard"},
                connect_timeout=3,
                read_timeout=15,
                max_pool_connections=32,
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )

    def put(self, key, data, version=0):
        value = record(key, data, version)
        self.s3.put_object(
            Bucket=BUCKET,
            Key=key,
            Body=data,
            ContentMD5=md5(data),
            Metadata={"sha256": value["sha256"], "version": str(version)},
        )
        return value

    def get(self, expected):
        r = self.s3.get_object(Bucket=BUCKET, Key=expected["key"])
        digest = hashlib.sha256()
        size = 0
        with closing(r["Body"]) as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        if (
            size != expected["size"]
            or r["ContentLength"] != size
            or digest.hexdigest() != expected["sha256"]
        ):
            raise RuntimeError("body size/checksum mismatch: " + expected["key"])
        if r.get("Metadata", {}).get("sha256") != expected["sha256"] or r.get(
            "Metadata", {}
        ).get("version") != str(expected["version"]):
            raise RuntimeError("metadata/version mismatch: " + expected["key"])
        return size

    def missing(self, key):
        try:
            self.s3.head_object(Bucket=BUCKET, Key=key)
        except ClientError as e:
            if e.response["ResponseMetadata"]["HTTPStatusCode"] == 404:
                return
            raise
        raise RuntimeError("object should be absent: " + key)

    def keys(self, prefix):
        token = None
        seen = set()
        while True:
            args = {"Bucket": BUCKET, "Prefix": prefix, "MaxKeys": 137}
            if token:
                args["ContinuationToken"] = token
            result = self.s3.list_objects_v2(**args)
            for value in result.get("Contents", []):
                yield value["Key"]
            if not result.get("IsTruncated"):
                break
            token = result.get("NextContinuationToken")
            if not token or token in seen:
                raise RuntimeError("missing/repeated listing continuation token")
            seen.add(token)

    def seed(self):
        self.s3.head_bucket(Bucket=BUCKET)
        require(
            next(self.keys(""), None) is None,
            "example namespace already contains data; use the full clean lifecycle",
        )
        from run_state import archive_oracles

        archive_oracles(ROOT)
        entries = [
            ("data/small/" + f"{i:08d}", 4096)
            for i in range(number("OBJECTS", "10000"))
        ]
        entries += [
            ("data/large/" + f"{i:08d}", 1024 * 1024)
            for i in range(number("LARGE_OBJECTS", "128", 1, 1024))
        ]
        start = time.monotonic()

        def one(item):
            key, size = item
            return self.put(key, body(key, size))

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=number("CONCURRENCY", "16", 1, 32)
        ) as pool:
            values = list(pool.map(one, entries))
        save("manifest", values)
        save(
            "seed",
            {
                "objects": len(values),
                "bytes": sum(v["size"] for v in values),
                "seconds": time.monotonic() - start,
            },
        )
        print("Seeded", len(values), "objects", flush=True)

    def cases(self):
        values = []
        for key, size in [("cases/unicode/café + %.bin", 4096), ("cases/empty", 0)]:
            value = self.put(key, body(key, size))
            self.get(value)
            values.append(value)
        key = "cases/overwrite"
        for version in range(4):
            value = self.put(key, body(key, 1024 * 1024, version), version)
            for _ in range(3):
                self.get(value)
        values.append(value)
        head = self.s3.head_object(Bucket=BUCKET, Key=key)
        require(head["ContentLength"] == value["size"], "HEAD size mismatch")
        r = self.s3.get_object(Bucket=BUCKET, Key=key, Range="bytes=100-199")
        with closing(r["Body"]) as stream:
            require(
                stream.read() == body(key, value["size"], value["version"])[100:200],
                "range body mismatch",
            )
        require(r["ResponseMetadata"]["HTTPStatusCode"] == 206, "range status mismatch")
        copy_key = "cases/copy"
        self.s3.copy_object(
            Bucket=BUCKET, Key=copy_key, CopySource={"Bucket": BUCKET, "Key": key}
        )
        copy = value | {"key": copy_key}
        self.get(copy)
        values.append(copy)
        key = "cases/multipart"
        data = body(key, 32 * 1024 * 1024)
        value = record(key, data)
        upload = self.s3.create_multipart_upload(
            Bucket=BUCKET, Key=key, Metadata={"sha256": value["sha256"], "version": "0"}
        )["UploadId"]
        parts = []
        for index, offset in enumerate(range(0, len(data), 8 * 1024 * 1024), 1):
            part = data[offset : offset + 8 * 1024 * 1024]
            response = self.s3.upload_part(
                Bucket=BUCKET,
                Key=key,
                UploadId=upload,
                PartNumber=index,
                Body=part,
                ContentMD5=md5(part),
            )
            parts.append({"PartNumber": index, "ETag": response["ETag"]})
        require(
            [
                p["PartNumber"]
                for p in self.s3.list_parts(Bucket=BUCKET, Key=key, UploadId=upload)[
                    "Parts"
                ]
            ]
            == [1, 2, 3, 4],
            "multipart listing mismatch",
        )
        self.s3.complete_multipart_upload(
            Bucket=BUCKET, Key=key, UploadId=upload, MultipartUpload={"Parts": parts}
        )
        self.get(value)
        values.append(value)
        key = "cases/abort"
        upload = self.s3.create_multipart_upload(Bucket=BUCKET, Key=key)["UploadId"]
        self.s3.upload_part(
            Bucket=BUCKET,
            Key=key,
            UploadId=upload,
            PartNumber=1,
            Body=data[: 8 * 1024 * 1024],
        )
        self.s3.abort_multipart_upload(Bucket=BUCKET, Key=key, UploadId=upload)
        self.missing(key)
        delete_key = "cases/deleted"
        self.put(delete_key, b"delete-me")
        self.s3.delete_object(Bucket=BUCKET, Key=delete_key)
        self.missing(delete_key)
        try:
            self.s3.put_object(
                Bucket=BUCKET,
                Key="cases/bad-md5",
                Body=b"check",
                ContentMD5=md5(b"wrong"),
            )
        except ClientError as e:
            if e.response["ResponseMetadata"]["HTTPStatusCode"] != 400:
                raise
        else:
            raise RuntimeError("server accepted an invalid Content-MD5")
        self.missing("cases/bad-md5")
        save("cases-manifest", values)
        return {
            "overwrite_versions": 4,
            "read_after_write_checks": 12,
            "multipart_bytes": len(data),
            "multipart_parts": 4,
            "abort": True,
            "range_bytes": 100,
            "bad_md5_rejected": True,
            "unicode_key": True,
            "copy": True,
            "delete": True,
        }

    def expected(self, fault=False):
        entries = {
            v["key"]: v
            for name in ["manifest", "cases-manifest", "benchmark-manifest"]
            for v in load(name, [])
        }
        required = set(entries)
        for entry in load("benchmark-attempts", []):
            entries[entry["key"]] = entry
        for value in load("fault-history", []):
            entry = value["object"]
            entries[entry["key"]] = entry
            if value["acked"]:
                required.add(entry["key"])
        if fault:
            with (ROOT / "churn.jsonl").open() as stream:
                records = [json.loads(line) for line in stream]
            for value in records:
                if value["stage"] == "attempt":
                    entries[value["object"]["key"]] = value["object"]
                elif value["stage"] == "ack":
                    required.add(value["key"])
        return entries, required

    def verify(self, fault=False):
        expected, required = self.expected(fault)
        seen = set()
        for prefix in ["data/", "cases/", "bench/"] + (
            ["churn/"] if fault or any(k.startswith("churn/") for k in expected) else []
        ):
            for key in self.keys(prefix):
                if key in seen or key not in expected:
                    raise RuntimeError("unexpected/duplicate key: " + key)
                seen.add(key)
        if required - seen:
            raise RuntimeError(f"missing {len(required - seen)} acknowledged keys")
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            sizes = list(pool.map(lambda key: self.get(expected[key]), sorted(seen)))
        return {
            "objects_verified": len(seen),
            "bytes_verified": sum(sizes),
            "missing_acknowledged_keys": 0,
            "corrupt_objects": 0,
            "stale_versions": 0,
            "extra_or_duplicate_keys": 0,
        }

    def benchmark(self):
        if next(self.keys("bench/"), None) is not None:
            raise RuntimeError(
                "benchmark keys already exist; use the full clean lifecycle to avoid dedup/cache-biased rewrites"
            )
        entries = load("benchmark-manifest", [])
        attempts = load("benchmark-attempts", [])
        results = {}
        for size, count in [
            (4096, number("BENCH_SMALL_REQUESTS", "10000")),
            (1024 * 1024, number("BENCH_REQUESTS", "256")),
            (4 * 1024 * 1024, number("BENCH_LARGE_REQUESTS", "128")),
        ]:
            for clients in [1, 16]:
                prefix = f"bench/{size}/clients-{clients}/"
                require(
                    count * size <= 768 * 1024 * 1024,
                    "benchmark preparation exceeds the bounded client memory budget",
                )
                prepared = [
                    (prefix + str(i), body(prefix + str(i), size)) for i in range(count)
                ]

                attempts.extend(record(key, data) for key, data in prepared)
                save("benchmark-attempts", attempts)

                # Preparation is outside the timer. Every key has distinct content,
                # so content-addressed stores cannot turn this into a dedup test.
                def put(item):
                    key, data = item
                    start = time.perf_counter()
                    value = self.put(key, data)
                    return time.perf_counter() - start, value

                start = time.perf_counter()
                with concurrent.futures.ThreadPoolExecutor(max_workers=clients) as pool:
                    writes = []
                    failures = []
                    futures = [pool.submit(put, item) for item in prepared]
                    for future in concurrent.futures.as_completed(futures):
                        try:
                            writes.append(future.result())
                        except Exception as error:
                            failures.append(str(error))
                wall = time.perf_counter() - start
                values = [v for _, v in writes]
                entries.extend(values)
                save("benchmark-manifest", entries)
                if failures:
                    save("benchmark-failures", failures)
                    raise RuntimeError(
                        f"{len(failures)} benchmark writes failed; acknowledged results preserved; clean lifecycle required"
                    )
                results[f"put/{size}/clients-{clients}"] = timing(
                    [t for t, _ in writes]
                ) | {
                    "seconds": wall,
                    "ops_per_second": count / wall,
                    "mib_per_second": count * size / wall / 1048576,
                    "failures": 0,
                }
                del prepared

                def get(value):
                    start = time.perf_counter()
                    self.get(value)
                    return time.perf_counter() - start

                start = time.perf_counter()
                with concurrent.futures.ThreadPoolExecutor(max_workers=clients) as pool:
                    reads = list(pool.map(get, values))
                wall = time.perf_counter() - start
                results[f"get/{size}/clients-{clients}"] = timing(reads) | {
                    "seconds": wall,
                    "ops_per_second": count / wall,
                    "mib_per_second": count * size / wall / 1048576,
                    "failures": 0,
                }
                save(
                    "benchmark",
                    {
                        "results": results,
                        "scope": "warm GET immediately after PUT; unique incompressible SHAKE-256-generated bodies; generation excluded, signing/transfer/integrity verification included; one run; no mutation retry",
                    },
                )
                print("Benchmarked", size, clients, flush=True)

    def drop(self):
        # This namespace is created and owned by the example, never an arbitrary
        # endpoint/bucket supplied to a general cleanup utility.
        require(BUCKET == "lab-objects", "refusing to drop a non-example bucket")
        try:
            self.s3.head_bucket(Bucket=BUCKET)
        except ClientError as error:
            if error.response["ResponseMetadata"]["HTTPStatusCode"] == 404:
                print("Example bucket already absent", flush=True)
                return
            raise
        uploads = list(
            self.s3.get_paginator("list_multipart_uploads").paginate(Bucket=BUCKET)
        )
        for page in uploads:
            for upload in page.get("Uploads", []):
                self.s3.abort_multipart_upload(
                    Bucket=BUCKET, Key=upload["Key"], UploadId=upload["UploadId"]
                )
        keys = list(self.keys(""))
        batches = [keys[i : i + 100] for i in range(0, len(keys), 100)]

        def delete(batch):
            response = self.s3.delete_objects(
                Bucket=BUCKET,
                Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
            )
            require(
                not response.get("Errors"),
                "partial batch deletion failure: " + str(response.get("Errors")),
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(delete, batches))
        require(next(self.keys(""), None) is None, "bucket still contains objects")
        self.s3.delete_bucket(Bucket=BUCKET)
        save(
            "cleanup",
            {
                "deleted_objects": len(keys),
                "aborted_uploads": sum(
                    len(page.get("Uploads", [])) for page in uploads
                ),
                "bucket_deleted": True,
            },
        )
        print("Removed", len(keys), "owned objects and example bucket", flush=True)

    def churn(self):
        fixture = load("manifest")
        end = time.monotonic() + number("DURATION", "120", 30, 1800)
        count = 0
        run_id = uuid.uuid4().hex
        save("churn-run", {"run_id": run_id})
        with (ROOT / "churn.jsonl").open("w") as journal:

            def append(value):
                journal.write(json.dumps(value) + "\n")
                journal.flush()
                os.fsync(journal.fileno())

            while time.monotonic() < end and not (ROOT / "stop-churn").exists():
                key = f"churn/{run_id}/{count:08d}"
                data = body(key, 32768, count + 1)
                value = record(key, data, count + 1)
                append({"stage": "attempt", "time": time.monotonic(), "object": value})
                try:
                    self.put(key, data, count + 1)
                    append({"stage": "ack", "time": time.monotonic(), "key": key})
                except Exception as e:
                    append(
                        {
                            "stage": "ambiguous",
                            "time": time.monotonic(),
                            "error": str(e)[:500],
                        }
                    )
                try:
                    self.get(fixture[count % len(fixture)])
                    append({"stage": "get", "time": time.monotonic()})
                except Exception as e:
                    append(
                        {
                            "stage": "get_error",
                            "time": time.monotonic(),
                            "error": str(e)[:500],
                        }
                    )
                count += 1
                time.sleep(0.05)
        save("churn", {"attempted_objects": count})


def main():
    action = sys.argv[1]
    if action == "unit":
        import unittest
        import test_workload

        result = unittest.TextTestRunner(verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromModule(test_workload)
        )
        if not result.wasSuccessful():
            raise SystemExit(1)
        return
    store = Store()
    if action == "create":
        try:
            store.s3.head_bucket(Bucket=BUCKET)
        except ClientError as e:
            if e.response["ResponseMetadata"]["HTTPStatusCode"] != 404:
                raise
            store.s3.create_bucket(Bucket=BUCKET)
    elif action == "probe":
        store.s3.head_bucket(Bucket=BUCKET)
        store.get(load("manifest")[0])
    elif action == "seed":
        store.seed()
    elif action == "test":
        cases = store.cases()
        report = store.verify()
        save("correctness", report | {"cases": cases})
        print(report, flush=True)
    elif action == "verify":
        report = store.verify(fault=True)
        save("reconciliation", report)
        print(report, flush=True)
    elif action == "benchmark":
        store.benchmark()
    elif action == "churn":
        store.churn()
    elif action == "drop":
        store.drop()
    else:
        raise ValueError("unknown action " + action)


if __name__ == "__main__":
    main()
