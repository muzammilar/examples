#include <rayforce.h>
#include <chrono>
#include <csignal>
#include <iomanip>
#include <iostream>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>

namespace {
struct Release {
    void operator()(ray_t* value) const {
        if (value && RAY_IS_ERR(value)) ray_error_free(value);
        else if (value) ray_release(value);
    }
};
using Value = std::unique_ptr<ray_t, Release>;

// Hex keeps arbitrary native formatted replies on one controller protocol line.
std::string hex(const std::string& text) {
    std::ostringstream out;
    out << std::hex << std::setfill('0');
    for (unsigned char ch : text) out << std::setw(2) << static_cast<unsigned>(ch);
    return out.str();
}

struct Connection {
    int64_t handle = -1;
    ~Connection() { close(); }
    void close() {
        if (handle >= 0) ray_ipc_close(handle);
        handle = -1;
    }
};
}

int main(int argc, char** argv) {
    try {
        if (argc != 3) throw std::runtime_error("usage: rayforce_ipc HOST PORT");
        const auto port = std::stoul(argv[2]);
        if (!port || port > 65535) throw std::runtime_error("invalid port");
        std::signal(SIGPIPE, SIG_IGN);
        auto* runtime = ray_runtime_create(0, nullptr);
        if (!runtime) throw std::runtime_error("native runtime initialization failed");
        const auto destroy = [](ray_runtime_t* rt) { ray_runtime_destroy(rt); };
        std::unique_ptr<ray_runtime_t, decltype(destroy)> owner(runtime, destroy);
        auto* poll = ray_poll_create();
        if (!poll) throw std::runtime_error("native IPC poll initialization failed");
        const auto destroy_poll = [](ray_poll_t* value) {
            ray_runtime_set_poll(nullptr);
            ray_poll_destroy(value);
        };
        std::unique_ptr<ray_poll_t, decltype(destroy_poll)> poll_owner(poll, destroy_poll);
        ray_runtime_set_poll(poll);
        Connection connection;
        std::string source;
        while (std::getline(std::cin, source)) {
            const auto started = std::chrono::steady_clock::now();
            if (connection.handle < 0)
                connection.handle = ray_ipc_connect(argv[1], static_cast<uint16_t>(port), nullptr, nullptr, 3000);
            std::string status = "error", text = "connect";
            if (connection.handle >= 0) {
                Value message(ray_str(source.data(), source.size()));
                if (!message || RAY_IS_ERR(message.get())) throw std::runtime_error("message allocation failed");
                Value reply(ray_ipc_send_timeout(connection.handle, message.get(), 3000));
                if (!reply || RAY_IS_ERR(reply.get())) {
                    text = reply ? ray_err_code(reply.get()) : "null-reply";
                    // A send error can follow application of the request. Never retry it.
                    connection.close();
                } else {
                    Value formatted(ray_fmt(reply.get(), 0));
                    if (!formatted || RAY_IS_ERR(formatted.get()))
                        throw std::runtime_error("reply formatting failed");
                    text.assign(ray_str_ptr(formatted.get()), ray_str_len(formatted.get()));
                    status = "ok";
                }
            }
            const auto elapsed = std::chrono::duration_cast<std::chrono::nanoseconds>(
                std::chrono::steady_clock::now() - started).count();
            std::cout << status << '\t' << elapsed << '\t' << hex(text) << '\n' << std::flush;
        }
        return std::cin.bad() ? 1 : 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
