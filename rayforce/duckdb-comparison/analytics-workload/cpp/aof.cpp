#include <rayforce.h>
#include <array>
#include <algorithm>
#include <charconv>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iomanip>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>
#include <utility>
#include <fcntl.h>
#include <sys/file.h>
#include <unistd.h>

namespace {
using Clock = std::chrono::steady_clock;
int64_t elapsed(Clock::time_point start) {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now() - start).count();
}

void timing(std::string_view name, std::vector<int64_t> samples) {
    if (samples.empty())
        return;
    std::sort(samples.begin(), samples.end());
    long double total = 0;
    for (const auto value : samples)
        total += value;
    std::cout << "TIMING\t" << name << "\t{\"count\":" << samples.size() << ",\"mean_ns\":"
              << std::setprecision(std::numeric_limits<long double>::max_digits10)
              << total / samples.size() << ",\"min_ns\":" << samples.front()
              << ",\"max_ns\":" << samples.back()
              << ",\"p50_ns\":" << samples[(samples.size() + 1) / 2 - 1]
              << ",\"p99_ns\":" << samples[(samples.size() * 99 + 99) / 100 - 1] << "}\n";
}

int64_t number(std::string_view text, int64_t minimum, int64_t maximum) {
    int64_t value = 0;
    const auto result = std::from_chars(text.data(), text.data() + text.size(), value);
    if (result.ec != std::errc{} || result.ptr != text.data() + text.size() || value < minimum ||
        value > maximum)
        throw std::runtime_error("invalid bounded integer");
    return value;
}

// The native AOF API deliberately has no writer lock. The application owns it.
class WriterLock {
    int descriptor_ = -1;

  public:
    explicit WriterLock(const std::filesystem::path &directory) {
        descriptor_ =
            ::open((directory / ".aof-owner").c_str(), O_CREAT | O_RDWR | O_CLOEXEC, 0600);
        if (descriptor_ < 0)
            throw std::runtime_error("cannot open application owner lock");
        if (::flock(descriptor_, LOCK_EX | LOCK_NB) != 0) {
            ::close(descriptor_);
            descriptor_ = -1;
            throw std::runtime_error("application writer lock rejected");
        }
    }
    ~WriterLock() {
        if (descriptor_ >= 0)
            ::close(descriptor_);
    }
    WriterLock(const WriterLock &) = delete;
    WriterLock &operator=(const WriterLock &) = delete;
};

struct Close {
    void operator()(ray_aof_t *value) const {
        if (value)
            ray_aof_close(value);
    }
};
using Log = std::unique_ptr<ray_aof_t, Close>;

std::string payload(int64_t identity, int64_t bytes) {
    std::string value = std::to_string(identity) + ':';
    value.reserve(static_cast<size_t>(bytes));
    while (value.size() < static_cast<size_t>(bytes))
        value.push_back(static_cast<char>('a' + (identity * 17 + value.size()) % 26));
    return value;
}

struct Export {
    std::ofstream output;
    int64_t records = 0;
    bool failed = false;
};

bool record(int64_t lsn, const void *body, int64_t bytes, void *context) noexcept {
    auto &state = *static_cast<Export *>(context);
    if (lsn != state.records || bytes < 0) {
        state.failed = true;
        return false;
    }
    // No exception crosses the C callback boundary; ofstream exceptions are off.
    std::array<char, 32> prefix{};
    const auto converted = std::to_chars(prefix.data(), prefix.data() + prefix.size(), lsn);
    if (converted.ec != std::errc{}) {
        state.failed = true;
        return false;
    }
    state.output.write(prefix.data(), converted.ptr - prefix.data());
    state.output.put('\t');
    state.output.write(static_cast<const char *>(body), bytes);
    state.output.put('\n');
    state.failed = !state.output.good();
    ++state.records;
    return !state.failed;
}
} // namespace

int main(int argc, char **argv) {
    try {
        if (argc != 6)
            throw std::runtime_error(
                "usage: rayforce_aof seed|open|scan DIR ROWS BYTES SEGMENT_LIMIT");
        const std::string mode = argv[1];
        const std::filesystem::path directory = argv[2];
        const auto rows = number(argv[3], 1, 1000000);
        const auto bytes = number(argv[4], 64, 1024 * 1024);
        const auto segment = number(argv[5], 4096, 256 * 1024 * 1024);
        if (rows * bytes > 512LL * 1024 * 1024)
            throw std::runtime_error("fixture exceeds 512 MiB limit");
        if (mode != "seed" && mode != "scan" && mode != "open")
            throw std::runtime_error("unknown AOF operation");
        if (mode == "scan") {
            Export state{std::ofstream(directory / "aof-export.tsv", std::ios::binary)};
            if (!state.output)
                throw std::runtime_error("cannot create independent scan export");
            ray_err_t error = RAY_OK;
            const auto started = Clock::now();
            const auto count = ray_aof_scan(directory.c_str(), 0, record, &state, &error);
            timing("scan_with_export_callbacks", {elapsed(started)});
            state.output.flush();
            if (state.failed || !state.output)
                throw std::runtime_error("scan export failed");
            std::cout << "SCAN\t" << error << '\t' << count << '\t' << state.records << '\n';
            return error == RAY_OK && count >= 0 ? 0 : 2;
        }
        std::filesystem::create_directories(directory);
        WriterLock owner(directory);
        if (mode == "seed") {
            for (const auto &entry : std::filesystem::directory_iterator(directory))
                if (entry.path().extension() == ".aof")
                    throw std::runtime_error("refusing to seed existing native state");
        }
        ray_err_t error = RAY_OK;
        const auto started = Clock::now();
        Log log(ray_aof_open(directory.c_str(), segment, &error));
        timing("open", {elapsed(started)});
        if (!log) {
            std::cout << "OPEN\t" << error << "\t-1\n";
            return 2;
        }
        if (mode == "seed") {
            std::vector<int64_t> appends, commits;
            appends.reserve(static_cast<size_t>(rows));
            commits.reserve(static_cast<size_t>(rows / 100 + 1));
            for (int64_t i = 0; i < rows; ++i) {
                const auto body = payload(i, bytes);
                const auto append_started = Clock::now();
                const auto lsn = ray_aof_append(log.get(), body.data(), body.size(), &error);
                appends.push_back(elapsed(append_started));
                if (lsn != i || error != RAY_OK)
                    throw std::runtime_error("native append failed");
                if ((i + 1) % 100 == 0 || i + 1 == rows) {
                    const auto commit_started = Clock::now();
                    const auto committed = ray_aof_commit(log.get());
                    commits.push_back(elapsed(commit_started));
                    if (committed != RAY_OK)
                        throw std::runtime_error("native durability barrier failed");
                    std::cout << "ACK\t" << i + 1 << '\n' << std::flush;
                }
            }
            timing("buffered_append", std::move(appends));
            timing("commit_barrier", std::move(commits));
        }
        std::cout << "OPEN\t0\t" << ray_aof_next_lsn(log.get()) << '\n';
        const auto close_started = Clock::now();
        const auto closed = ray_aof_close(log.release());
        timing("close", {elapsed(close_started)});
        if (closed != RAY_OK)
            throw std::runtime_error("native close failed");
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
