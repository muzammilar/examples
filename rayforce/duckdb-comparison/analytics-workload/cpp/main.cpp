#include "bridge.h"
#include <chrono>
#include <cstdlib>
#include <fstream>
#include <iostream>
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
    explicit Runtime(int threads) {
        if (bench_open(threads)) throw std::runtime_error(bench_error());
    }
    ~Runtime() { bench_close(); }
    Runtime(const Runtime&) = delete;
    Runtime& operator=(const Runtime&) = delete;
};
int main(int argc, char** argv) {
    try {
        if (argc != 3) throw std::runtime_error("usage: workload PLAN THREADS");
        Runtime runtime(std::stoi(argv[2]));
        std::ifstream plan(argv[1]);
        if (!plan) throw std::runtime_error("cannot open plan");
        for (std::string line; std::getline(plan, line);) {
            const auto a = line.find('\t'), b = line.find('\t', a + 1), c = line.find('\t', b + 1);
            if (a == line.npos || b == line.npos || c == line.npos) throw std::runtime_error("invalid plan");
            const auto phase = line.substr(0, a), name = line.substr(a + 1, b - a - 1);
            const auto expected = line.substr(b + 1, c - b - 1), query = line.substr(c + 1);
            const auto cpu = cpu_ns();
            const auto start = std::chrono::steady_clock::now();
            char* raw = bench_eval(query.c_str(), phase != "load");
            const auto ns = std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now() - start).count();
            const auto used_cpu = cpu_ns() - cpu;
            if (!raw) throw std::runtime_error(bench_error());
            const std::string actual(raw);
            bench_free(raw);
            if (actual != expected) throw std::runtime_error("incorrect result: " + name + " got " + actual);
            rusage usage{};
            if (getrusage(RUSAGE_SELF, &usage)) throw std::runtime_error("getrusage failed");
            std::cout << phase << '\t' << name << '\t' << ns << '\t' << used_cpu << '\t' << usage.ru_maxrss << '\n' << std::flush;
        }
        if (!plan.eof()) throw std::runtime_error("plan read failed");
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
