#include "bridge.h"
#include <chrono>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/resource.h>

static long long cpu_ns() {
    rusage usage{};
    if (getrusage(RUSAGE_SELF, &usage)) throw std::runtime_error("getrusage failed");
    return (usage.ru_utime.tv_sec + usage.ru_stime.tv_sec) * 1000000000LL +
           (usage.ru_utime.tv_usec + usage.ru_stime.tv_usec) * 1000LL;
}
struct Runtime {
    explicit Runtime(int threads, const char* database_file = nullptr) {
        if (bench_open_file(threads, database_file)) throw std::runtime_error(bench_error());
    }
    ~Runtime() { bench_close(); }
    Runtime(const Runtime&) = delete;
    Runtime& operator=(const Runtime&) = delete;
};
int main(int argc, char** argv) {
    try {
        if (argc != 3 && argc != 4) throw std::runtime_error("usage: workload PLAN THREADS [DUCKDB_FILE]");
        Runtime runtime(std::stoi(argv[2]), argc == 4 ? argv[3] : nullptr);
        std::ifstream plan(argv[1]);
        if (!plan) throw std::runtime_error("cannot open plan");
        bool wrote = false;
        std::chrono::steady_clock::time_point first_write{}, last_write{};
        long long first_write_cpu = 0, last_write_cpu = 0;
        for (std::string line; std::getline(plan, line);) {
            const auto a = line.find('\t'), b = line.find('\t', a + 1), c = line.find('\t', b + 1);
            if (a == line.npos || b == line.npos || c == line.npos) throw std::runtime_error("invalid plan");
            const auto phase = line.substr(0, a), name = line.substr(a + 1, b - a - 1);
            const auto expected = line.substr(b + 1, c - b - 1), query = line.substr(c + 1);
            int64_t first_id = 0, count = 0;
            const bool append = phase == "append" || phase == "columnar" || phase == "reject-append" || phase == "reject-columnar";
            if (append) {
                std::istringstream values(query);
                if (!(values >> first_id >> count)) throw std::runtime_error("invalid append plan");
                values >> std::ws;
                if (!values.eof()) throw std::runtime_error("invalid append plan");
            }
            const auto cpu = cpu_ns();
            const auto start = std::chrono::steady_clock::now();
            char* raw = nullptr;
            if (append) {
                const bool columnar = phase == "columnar" || phase == "reject-columnar";
                const bool failed = (columnar ? bench_append_columnar : bench_append)(first_id, count) != 0;
                if (failed != (phase == "reject-append" || phase == "reject-columnar")) throw std::runtime_error(failed ? bench_error() : "invalid append unexpectedly accepted");
            } else {
                raw = bench_eval(query.c_str(), phase != "load" && phase != "write" && phase != "export");
                if (!raw) throw std::runtime_error(bench_error());
            }
            const auto finished = std::chrono::steady_clock::now();
            const auto ns = std::chrono::duration_cast<std::chrono::nanoseconds>(finished - start).count();
            const auto used_cpu = cpu_ns() - cpu;
            if (phase == "write" || phase == "append" || phase == "columnar") {
                if (!wrote) { first_write = start; first_write_cpu = cpu; wrote = true; }
                last_write = finished;
                last_write_cpu = cpu + used_cpu;
            }
            const std::string actual = raw ? raw : "";
            bench_free(raw);
            if (actual != expected) throw std::runtime_error("incorrect result: " + name + " got " + actual);
            rusage usage{};
            if (getrusage(RUSAGE_SELF, &usage)) throw std::runtime_error("getrusage failed");
            std::cout << phase << '\t' << name << '\t' << ns << '\t' << used_cpu << '\t' << usage.ru_maxrss << '\n' << std::flush;
        }
        if (!plan.eof()) throw std::runtime_error("plan read failed");
        if (wrote) {
            rusage usage{};
            if (getrusage(RUSAGE_SELF, &usage)) throw std::runtime_error("getrusage failed");
            std::cout << "window\tingestion\t"
                      << std::chrono::duration_cast<std::chrono::nanoseconds>(last_write - first_write).count()
                      << '\t' << last_write_cpu - first_write_cpu << '\t' << usage.ru_maxrss << '\n';
        }
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
