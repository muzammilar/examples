#include <duckdb.h>
#include <chrono>
#include <charconv>
#include <csignal>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <memory>
#include <regex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <fcntl.h>
#include <unistd.h>

namespace {
volatile std::sig_atomic_t stop_requested = 0;
void stop(int) { stop_requested = 1; }

struct Free {
    void operator()(char *pointer) const { duckdb_free(pointer); }
};
using Text = std::unique_ptr<char, Free>;

class Result {
    duckdb_result result_{};

  public:
    Result(duckdb_connection connection, const std::string &sql) {
        if (duckdb_query(connection, sql.c_str(), &result_) != DuckDBSuccess) {
            const auto *message = duckdb_result_error(&result_);
            const std::string error = message ? message : "native query failed without a message";
            duckdb_destroy_result(&result_);
            throw std::runtime_error(error);
        }
    }
    ~Result() { duckdb_destroy_result(&result_); }
    Result(const Result &) = delete;
    Result &operator=(const Result &) = delete;
    std::string text() {
        const auto rows = duckdb_row_count(&result_), columns = duckdb_column_count(&result_);
        if (rows > 1024 || columns > 64)
            throw std::runtime_error(
                "control reply exceeds bounds; export large results on the server");
        std::ostringstream output;
        for (idx_t row = 0; row < rows; ++row) {
            if (row)
                output << '\n';
            for (idx_t column = 0; column < columns; ++column) {
                if (column)
                    output << '\t';
                if (duckdb_value_is_null(&result_, column, row))
                    throw std::runtime_error("unexpected NULL in control reply");
                Text value(duckdb_value_varchar(&result_, column, row));
                if (!value)
                    throw std::runtime_error("native reply conversion failed");
                output << value.get();
            }
        }
        return output.str();
    }
};

class Database {
    duckdb_database database_ = nullptr;
    duckdb_connection connection_ = nullptr;

  public:
    Database(const char *path, const char *memory) {
        duckdb_config config = nullptr;
        if (duckdb_create_config(&config) != DuckDBSuccess)
            throw std::runtime_error("native configuration allocation failed");
        try {
            for (const auto &option : {std::pair{"threads", "1"},
                                       {"memory_limit", memory},
                                       {"extension_directory", "/opt/duckdb/extensions"},
                                       {"autoinstall_known_extensions", "false"},
                                       {"autoload_known_extensions", "false"}}) {
                if (duckdb_set_config(config, option.first, option.second) != DuckDBSuccess)
                    throw std::runtime_error(std::string("native configuration rejected: ") +
                                             option.first);
            }
            char *raw_error = nullptr;
            const auto opened = duckdb_open_ext(path, &database_, config, &raw_error);
            Text error(raw_error);
            if (opened != DuckDBSuccess)
                throw std::runtime_error(error ? error.get() : "native database open failed");
            if (duckdb_connect(database_, &connection_) != DuckDBSuccess)
                throw std::runtime_error("native connection failed");
        } catch (...) {
            if (connection_)
                duckdb_disconnect(&connection_);
            if (database_)
                duckdb_close(&database_);
            duckdb_destroy_config(&config);
            throw;
        }
        duckdb_destroy_config(&config);
    }
    ~Database() {
        duckdb_disconnect(&connection_);
        duckdb_close(&database_);
    }
    Database(const Database &) = delete;
    Database &operator=(const Database &) = delete;
    std::string query(const std::string &sql) { return Result(connection_, sql).text(); }
    void extensions() {
        query("LOAD httpfs");
        query("LOAD quack");
    }
};

std::string hex(const std::string &text) {
    std::ostringstream output;
    output << std::hex << std::setfill('0');
    for (const unsigned char byte : text)
        output << std::setw(2) << static_cast<unsigned>(byte);
    return output.str();
}

void admitted_database() {
    const int descriptor =
        ::open("/state/.database-admitted", O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
    if (descriptor < 0)
        throw std::runtime_error("cannot create owned database admission marker");
    const auto synced = ::fsync(descriptor);
    ::close(descriptor);
    if (synced)
        throw std::runtime_error("database admission marker sync failed");
    const int directory = ::open("/state", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (directory < 0)
        throw std::runtime_error("cannot open owned state directory");
    const auto directory_synced = ::fsync(directory);
    ::close(directory);
    if (directory_synced)
        throw std::runtime_error("state directory sync failed");
}

void final_metrics(std::chrono::steady_clock::time_point started) {
    std::ostringstream output;
    output << "__ELAPSED_NS__\n"
           << std::chrono::duration_cast<std::chrono::nanoseconds>(
                  std::chrono::steady_clock::now() - started)
                  .count()
           << '\n';
    for (const auto &[tag, path] : {std::pair{"PROCESS", "/proc/self/status"},
                                    {"CPU", "/sys/fs/cgroup/cpu.stat"},
                                    {"MEMORY", "/sys/fs/cgroup/memory.peak"},
                                    {"MEMORY_EVENTS", "/sys/fs/cgroup/memory.events"},
                                    {"IO", "/sys/fs/cgroup/io.stat"}}) {
        std::ifstream input(path);
        if (!input)
            throw std::runtime_error(std::string("cannot read final resource counter: ") + path);
        output << "__" << tag << "__\n" << input.rdbuf() << '\n';
    }
    std::cerr << "__DUCKDB_FINAL__" << hex(output.str()) << '\n';
}

void server(const std::string &token) {
    const std::filesystem::path file("/state/events.duckdb"), marker("/state/.database-admitted");
    if (std::filesystem::is_symlink(file) || std::filesystem::is_symlink(marker))
        throw std::runtime_error("unexpected native state symlink");
    const bool existing = std::filesystem::exists(marker);
    if (existing != std::filesystem::is_regular_file(file))
        throw std::runtime_error("incomplete or unmarked database state; preserve it for recovery");
    Database database(file.c_str(), "3GB");
    database.extensions();
    database.query("SET checkpoint_threshold='256MB'");
    if (!existing) {
        database.query("CHECKPOINT");
        admitted_database();
    }
    const auto fingerprint =
        database.query("SELECT fingerprint FROM quack_generate_keys(directory='/state/tls')");
    database.query(
        "CALL quack_serve('quack:0.0.0.0:9494', token='" + token +
        "', allow_other_hostname=true, disable_ssl=false, ssl_cert_file='/state/tls/server.pem', "
        "ssl_key_file='/state/tls/private_key.pem')");
    std::cout << "READY\t" << fingerprint << '\n' << std::flush;
    while (!stop_requested)
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    database.query("CALL quack_stop('quack:0.0.0.0:9494')");
}

void client(const std::string &uri, const std::string &token, const std::string &fingerprint,
            unsigned timeout) {
    std::unique_ptr<Database> database;
    std::string sql;
    while (std::getline(std::cin, sql)) {
        const auto started = std::chrono::steady_clock::now();
        std::string status = "error", reply;
        try {
            if (!database) {
                database = std::make_unique<Database>(nullptr, "384MB");
                database->extensions();
                database->query("SET GLOBAL http_timeout=" + std::to_string(timeout));
                database->query("SET GLOBAL http_retries=0");
                database->query("SET GLOBAL quack_enable_reconnects=false");
                database->query("ATTACH '" + uri + "' AS endpoint (TOKEN '" + token +
                                "', DISABLE_SSL false, SSL_FINGERPRINT '" + fingerprint + "')");
                database->query("CONNECT endpoint");
            }
            reply = database->query(sql);
            status = "ok";
        } catch (const std::exception &error) {
            reply = error.what();
            // An error may follow commit. Reconnect for the next operation;
            // never replay this mutation.
            database.reset();
        }
        const auto elapsed = std::chrono::duration_cast<std::chrono::nanoseconds>(
                                 std::chrono::steady_clock::now() - started)
                                 .count();
        std::cout << status << '\t' << elapsed << '\t' << hex(reply) << '\n' << std::flush;
    }
    if (std::cin.bad())
        throw std::runtime_error("controller input failed");
}
} // namespace

int main(int argc, char **argv) {
    const auto started = std::chrono::steady_clock::now();
    try {
        if (std::string(duckdb_library_version()) != "v2.0.0")
            throw std::runtime_error("unexpected native DuckDB version");
        if (argc == 2 && std::string(argv[1]) == "platform") {
            Database database(nullptr, "128MB");
            std::cout << database.query("PRAGMA platform") << '\n';
            return 0;
        }
        if (argc == 2 && std::string(argv[1]) == "capability") {
            Database database(nullptr, "128MB");
            database.extensions();
            std::cout << "ENGINE\t" << duckdb_library_version() << "\nPLATFORM\t"
                      << database.query("PRAGMA platform") << '\n'
                      << database.query(
                             "SELECT extension_name, coalesce(extension_version, '<null>'), loaded "
                             "FROM duckdb_extensions() WHERE extension_name IN ('quack','httpfs') "
                             "ORDER BY extension_name")
                      << '\n';
            return 0;
        }
        if (argc < 3)
            throw std::runtime_error(
                "usage: duckdb_quack server TOKEN | client URI TOKEN FINGERPRINT | platform");
        std::signal(SIGPIPE, SIG_IGN);
        if (std::string(argv[1]) == "server" && argc == 3) {
            if (!std::regex_match(argv[2], std::regex("[A-Za-z0-9_-]{16,128}")))
                throw std::runtime_error("invalid lab token");
            std::signal(SIGTERM, stop);
            std::signal(SIGINT, stop);
            server(argv[2]);
        } else if (std::string(argv[1]) == "client" && (argc == 5 || argc == 6)) {
            if (!std::regex_match(argv[2], std::regex("quack:[A-Za-z0-9._:/-]+")) ||
                !std::regex_match(argv[3], std::regex("[A-Za-z0-9_-]{16,128}")) ||
                !std::regex_match(argv[4], std::regex("[0-9A-Fa-f:]{64,95}")))
                throw std::runtime_error("invalid lab connection arguments");
            unsigned timeout = 3;
            if (argc == 6) {
                const std::string argument = argv[5];
                const auto parsed =
                    std::from_chars(argument.data(), argument.data() + argument.size(), timeout);
                if (parsed.ec != std::errc{} || parsed.ptr != argument.data() + argument.size() ||
                    timeout < 1 || timeout > 180)
                    throw std::runtime_error("invalid native RPC timeout");
            }
            client(argv[2], argv[3], argv[4], timeout);
        } else
            throw std::runtime_error("unknown native role");
        final_metrics(started);
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
