"""Same synchronous native ABI as C++/Rust; deliberately measures ctypes overhead."""

import ctypes
import resource
import sys
import time


def main():
    engine, plan, threads = sys.argv[1:]
    lib = ctypes.CDLL(f"/opt/bench/libbench_{engine}.so")
    lib.bench_open.argtypes = [ctypes.c_int]
    lib.bench_open.restype = ctypes.c_int
    lib.bench_error.restype = ctypes.c_char_p
    lib.bench_eval.argtypes = [ctypes.c_char_p, ctypes.c_int]
    lib.bench_eval.restype = ctypes.c_void_p
    lib.bench_free.argtypes = [ctypes.c_void_p]
    lib.bench_append.argtypes = [ctypes.c_int64, ctypes.c_int64]
    lib.bench_append.restype = ctypes.c_int
    lib.bench_append_columnar.argtypes = [ctypes.c_int64, ctypes.c_int64]
    lib.bench_append_columnar.restype = ctypes.c_int
    lib.bench_close.restype = None
    if lib.bench_open(int(threads)):
        raise RuntimeError(lib.bench_error().decode())
    try:
        first_write = last_write = None
        with open(plan) as source:
            for line in source:
                phase, name, expected, query = line.rstrip("\n").split("\t", 3)
                encoded = query.encode()
                append = (
                    tuple(map(int, query.split()))
                    if phase
                    in {"append", "columnar", "reject-append", "reject-columnar"}
                    else None
                )
                if append is not None and (
                    len(append) != 2
                    or any(not -(2**63) <= value < 2**63 for value in append)
                ):
                    raise ValueError("append plan requires two signed 64-bit integers")
                cpu, start = time.process_time_ns(), time.perf_counter_ns()
                if append is not None:
                    function = (
                        lib.bench_append_columnar
                        if phase in {"columnar", "reject-columnar"}
                        else lib.bench_append
                    )
                    failed = bool(function(*append))
                    if failed != (phase in {"reject-append", "reject-columnar"}):
                        raise RuntimeError(
                            lib.bench_error().decode()
                            if failed
                            else "invalid append unexpectedly accepted"
                        )
                    result = None
                else:
                    result = lib.bench_eval(
                        encoded, phase not in {"load", "write", "export"}
                    )
                    if not result:
                        raise RuntimeError(lib.bench_error().decode())
                finished = time.perf_counter_ns()
                elapsed, used_cpu = (
                    finished - start,
                    time.process_time_ns() - cpu,
                )
                if phase in {"write", "append", "columnar"}:
                    if first_write is None:
                        first_write = (start, cpu)
                    last_write = (finished, cpu + used_cpu)
                try:
                    actual = ctypes.string_at(result).decode() if result else ""
                finally:
                    lib.bench_free(result)
                if actual != expected:
                    raise RuntimeError(f"incorrect result: {name}: {actual}")
                rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                print(phase, name, elapsed, used_cpu, rss, sep="\t", flush=True)
        if first_write is not None:
            print(
                "window",
                "ingestion",
                last_write[0] - first_write[0],
                last_write[1] - first_write[1],
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                sep="\t",
                flush=True,
            )
    finally:
        lib.bench_close()


if __name__ == "__main__":
    main()
