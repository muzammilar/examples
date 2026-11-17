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
    lib.bench_close.restype = None
    if lib.bench_open(int(threads)):
        raise RuntimeError(lib.bench_error().decode())
    try:
        with open(plan) as source:
            for line in source:
                phase, name, expected, query = line.rstrip("\n").split("\t", 3)
                encoded = query.encode()
                cpu, start = time.process_time_ns(), time.perf_counter_ns()
                result = lib.bench_eval(encoded, phase != "load")
                elapsed, used_cpu = (
                    time.perf_counter_ns() - start,
                    time.process_time_ns() - cpu,
                )
                if not result:
                    raise RuntimeError(lib.bench_error().decode())
                try:
                    actual = ctypes.string_at(result).decode()
                finally:
                    lib.bench_free(result)
                if actual != expected:
                    raise RuntimeError(f"incorrect result: {name}: {actual}")
                rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                print(phase, name, elapsed, used_cpu, rss, sep="\t", flush=True)
    finally:
        lib.bench_close()


if __name__ == "__main__":
    main()
