#include "log.h"

#include <windows.h>

#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <string>

#include "config.h"

namespace {

FILE*            g_file = nullptr;
CRITICAL_SECTION g_cs;
bool             g_cs_ready = false;
bool             g_quiet = false;
char             g_path[MAX_PATH] = {0};

bool try_open(const std::string& path) {
    FILE* f = std::fopen(path.c_str(), "w");
    if (f == nullptr) {
        return false;
    }
    g_file = f;
    std::snprintf(g_path, sizeof(g_path), "%s", path.c_str());
    return true;
}

std::string local_app_data_path() {
    char buf[MAX_PATH] = {0};
    const DWORD n = GetEnvironmentVariableA("LOCALAPPDATA", buf, sizeof(buf));
    if (n == 0 || n >= sizeof(buf)) {
        return std::string();
    }

    std::string dir(buf);
    dir += "\\NosTalePacketLogger";
    CreateDirectoryA(dir.c_str(), nullptr);

    dir += "\\dll.log";
    return dir;
}

std::string temp_path() {
    char buf[MAX_PATH] = {0};
    const DWORD n = GetTempPathA(sizeof(buf), buf);
    if (n == 0 || n >= sizeof(buf)) {
        return std::string();
    }
    return std::string(buf) + "ntpl-dll.log";
}

void write_prefix_locked(const char* direction) {
    SYSTEMTIME st;
    GetLocalTime(&st);
    std::fprintf(g_file, "%02d:%02d:%02d.%03d [%s] ",
                 st.wHour, st.wMinute, st.wSecond, st.wMilliseconds, direction);
}

}  // namespace

namespace ntpl {

void log_open() {
    g_quiet = config_quiet();

    if (!g_cs_ready) {
        InitializeCriticalSection(&g_cs);
        g_cs_ready = true;
    }

    if (g_quiet) {
        return;
    }

    if (try_open(config_log_path())) {
        return;
    }

    const std::string fallback = local_app_data_path();
    if (!fallback.empty() && try_open(fallback)) {
        return;
    }

    const std::string temp = temp_path();
    if (!temp.empty()) {
        try_open(temp);
    }
}

void log_close() {
    if (g_cs_ready) {
        EnterCriticalSection(&g_cs);
    }
    if (g_file != nullptr) {
        std::fflush(g_file);
        std::fclose(g_file);
        g_file = nullptr;
    }
    if (g_cs_ready) {
        LeaveCriticalSection(&g_cs);
    }
}

void logf(const char* fmt, ...) {
    if (g_file == nullptr || fmt == nullptr) {
        return;
    }

    char message[2048] = {0};
    va_list args;
    va_start(args, fmt);
    std::vsnprintf(message, sizeof(message), fmt, args);
    va_end(args);

    EnterCriticalSection(&g_cs);
    write_prefix_locked("STATUS");
    std::fprintf(g_file, "%s\n", message);
    std::fflush(g_file);
    LeaveCriticalSection(&g_cs);
}

void log_packet(const char* direction, const char* packet) {
    if (g_file == nullptr || direction == nullptr || packet == nullptr) {
        return;
    }

    EnterCriticalSection(&g_cs);
    write_prefix_locked(direction);
    std::fprintf(g_file, "%s\n", packet);
    LeaveCriticalSection(&g_cs);
}

void log_flush() {
    if (g_file == nullptr) {
        return;
    }

    EnterCriticalSection(&g_cs);
    std::fflush(g_file);
    LeaveCriticalSection(&g_cs);
}

}  // namespace ntpl
