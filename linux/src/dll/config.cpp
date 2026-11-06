#include "config.h"

#include <windows.h>

#include <cstdlib>

namespace {

std::string env(const char* name) {
    char buf[1024] = {0};
    const DWORD n = GetEnvironmentVariableA(name, buf, sizeof(buf));
    if (n == 0 || n >= sizeof(buf)) {
        return std::string();
    }
    return std::string(buf, n);
}

}  // namespace

namespace ntpl {

std::uint16_t config_port() {
    const std::string value = env("NTPL_PORT");
    if (value.empty()) {
        return kDefaultPort;
    }

    char* end = nullptr;
    const long parsed = std::strtol(value.c_str(), &end, 10);
    if (end == value.c_str() || parsed < 1 || parsed > 65535) {
        return kDefaultPort;
    }
    return static_cast<std::uint16_t>(parsed);
}

std::string config_log_path() {
    const std::string value = env("NTPL_LOG");
    return value.empty() ? std::string(kDefaultLogPath) : value;
}

bool config_quiet() {
    const std::string value = env("NTPL_QUIET");
    return !value.empty() && value != "0";
}

}  // namespace ntpl
