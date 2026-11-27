"""Verify visible closed parts while Spark writes; require the full final dataset."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import time

import pyarrow as pa
import pyarrow.parquet as pq
import verify

REMOTE = "/lab/orders"


def hdfs(*args):
    started = time.monotonic()
    result = subprocess.run(["hdfs", "dfs", *args], capture_output=True, text=True, timeout=30)
    emit({"event": "read_operation", "operation": args[0], "path": args[-1],
          "code": result.returncode, "duration_ms": (time.monotonic() - started) * 1000,
          "stderr": result.stderr})
    return result


def emit(record):
    print(json.dumps({"elapsed_s": time.monotonic() - STARTED, **record}), flush=True)


STARTED = time.monotonic()


def main():
    emit({"event": "reader_started", "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    local = Path("/data/live-parts")
    local.mkdir()
    observed = set()
    while time.monotonic() - STARTED < 300:
        exists = hdfs("-test", "-d", REMOTE)
        if exists.returncode:
            if exists.stderr.strip():
                emit({"event": "read_failure", "error": exists.stderr})
            time.sleep(1)
            continue
        listing = hdfs("-ls", "-C", REMOTE + "/part-*.parquet")
        if listing.returncode and "No such file or directory" not in listing.stderr:
            raise RuntimeError(listing.stderr)
        for remote in listing.stdout.splitlines():
            if remote in observed:
                continue
            path = local / Path(remote).name
            copied = hdfs("-get", remote, str(path))
            if copied.returncode:
                if "No such file or directory" in copied.stderr:
                    emit({"event": "file_disappeared_before_read", "path": remote})
                    continue
                raise RuntimeError(copied.stderr)
            count = 0
            parquet = pq.ParquetFile(path)
            if any(field.type != pa.int64() for field in parquet.schema_arrow):
                raise ValueError("Wrong field type in visible part")
            for batch in parquet.iter_batches(batch_size=65536):
                values = batch.to_pydict()
                for order, customer, amount, region in zip(
                    values["order_id"], values["customer_id"], values["amount_cents"], values["region"], strict=True,
                ):
                    if order is None or not 0 <= order < 1000000 or customer != order % 10000 or amount != 100 + order % 1000 or region != customer % 10:
                        raise ValueError(f"Wrong visible row: {order}, {customer}, {amount}, {region}")
                    count += 1
            observed.add(remote)
            emit({"event": "closed_part_verified", "path": remote, "rows": count,
                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        if hdfs("-test", "-e", REMOTE + "/_SUCCESS").returncode == 0:
            subprocess.run(["hdfs", "dfs", "-get", REMOTE, "/data/orders"], check=True, timeout=60)
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                verify.main()
            emit({"event": "final_dataset_verified", "observed_closed_parts": len(observed),
                  "result": json.loads(captured.getvalue())})
            return
        time.sleep(1)
    raise TimeoutError("No complete dataset within 300 seconds; preserve the writer and reader evidence")


if __name__ == "__main__":
    main()
