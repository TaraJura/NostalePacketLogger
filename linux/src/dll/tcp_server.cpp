#include "tcp_server.h"

#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>

#include <cstring>

#include "log.h"

namespace {

SOCKET      g_listen = INVALID_SOCKET;
SOCKET      g_client = INVALID_SOCKET;
bool        g_wsa_ready = false;
std::string g_pending;

constexpr std::size_t kMaxPending = 64 * 1024;

void close_client() {
    if (g_client != INVALID_SOCKET) {
        closesocket(g_client);
        g_client = INVALID_SOCKET;
    }
    g_pending.clear();
}

}  // namespace

namespace tcpserver {

bool start(std::uint16_t port) {
    if (g_listen != INVALID_SOCKET) {
        return true;
    }

    if (!g_wsa_ready) {
        WSADATA wsa = {};
        if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
            ntpl::logf("WSAStartup failed");
            return false;
        }
        g_wsa_ready = true;
    }

    SOCKET listener = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (listener == INVALID_SOCKET) {
        ntpl::logf("socket() failed: %d", WSAGetLastError());
        return false;
    }

    BOOL reuse = TRUE;
    setsockopt(listener, SOL_SOCKET, SO_REUSEADDR,
               reinterpret_cast<const char*>(&reuse), sizeof(reuse));

    sockaddr_in address = {};
    address.sin_family = AF_INET;
    address.sin_port = htons(port);
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);

    if (bind(listener, reinterpret_cast<sockaddr*>(&address),
             sizeof(address)) == SOCKET_ERROR) {
        ntpl::logf("bind 127.0.0.1:%u failed: %d", static_cast<unsigned>(port),
                   WSAGetLastError());
        closesocket(listener);
        return false;
    }

    if (listen(listener, 4) == SOCKET_ERROR) {
        ntpl::logf("listen failed: %d", WSAGetLastError());
        closesocket(listener);
        return false;
    }

    g_listen = listener;
    return true;
}

void stop() {
    close_client();

    if (g_listen != INVALID_SOCKET) {
        closesocket(g_listen);
        g_listen = INVALID_SOCKET;
    }

    if (g_wsa_ready) {
        WSACleanup();
        g_wsa_ready = false;
    }
}

bool accept_client() {
    if (g_listen == INVALID_SOCKET) {
        return false;
    }

    // Never block here. The worker must keep draining the packet queues and
    // writing the file log even when no host is attached; a blocking accept
    // would freeze the log and overflow the rings between host connections.
    fd_set readable;
    FD_ZERO(&readable);
    FD_SET(g_listen, &readable);
    timeval timeout = {0, 0};
    if (select(0, &readable, nullptr, nullptr, &timeout) <= 0) {
        return false;
    }

    sockaddr_in from = {};
    int from_length = sizeof(from);
    SOCKET client =
        accept(g_listen, reinterpret_cast<sockaddr*>(&from), &from_length);
    if (client == INVALID_SOCKET) {
        return false;
    }

    // Never let a stalled host block the game thread for long. Two seconds is
    // far beyond a loopback round trip; a host that cannot drain 64 KiB in that
    // time is broken and gets disconnected instead of freezing the client.
    DWORD timeout_ms = 2000;
    setsockopt(client, SOL_SOCKET, SO_SNDTIMEO,
               reinterpret_cast<const char*>(&timeout_ms), sizeof(timeout_ms));

    int no_delay = 1;
    setsockopt(client, IPPROTO_TCP, TCP_NODELAY,
               reinterpret_cast<const char*>(&no_delay), sizeof(no_delay));

    close_client();
    g_client = client;
    return true;
}

bool is_connected() {
    return g_client != INVALID_SOCKET;
}

void disconnect() {
    close_client();
}

bool poll_command(std::string& out) {
    if (g_client == INVALID_SOCKET) {
        return false;
    }

    // Drain every complete line already buffered before asking the socket.
    std::size_t newline = g_pending.find('\n');
    if (newline != std::string::npos) {
        out = g_pending.substr(0, newline);
        g_pending.erase(0, newline + 1);
        if (!out.empty() && out.back() == '\r') {
            out.pop_back();
        }
        return true;
    }

    fd_set readable;
    FD_ZERO(&readable);
    FD_SET(g_client, &readable);
    timeval timeout = {0, 0};

    const int ready = select(0, &readable, nullptr, nullptr, &timeout);
    if (ready <= 0) {
        return false;
    }

    char buffer[4096];
    const int received = recv(g_client, buffer, sizeof(buffer), 0);
    if (received <= 0) {
        close_client();
        return false;
    }

    g_pending.append(buffer, static_cast<std::size_t>(received));

    if (g_pending.size() > kMaxPending) {
        ntpl::logf("host sent more than %u bytes without a newline; dropping it",
                   static_cast<unsigned>(kMaxPending));
        close_client();
        return false;
    }

    newline = g_pending.find('\n');
    if (newline == std::string::npos) {
        return false;
    }

    out = g_pending.substr(0, newline);
    g_pending.erase(0, newline + 1);
    if (!out.empty() && out.back() == '\r') {
        out.pop_back();
    }
    return true;
}

bool send_line(const std::string& line) {
    if (g_client == INVALID_SOCKET) {
        return false;
    }

    std::string framed = line;
    framed.push_back('\n');

    std::size_t sent = 0;
    while (sent < framed.size()) {
        const int n = send(g_client, framed.data() + sent,
                           static_cast<int>(framed.size() - sent), 0);
        if (n <= 0) {
            ntpl::logf("send failed: %d", WSAGetLastError());
            close_client();
            return false;
        }
        sent += static_cast<std::size_t>(n);
    }
    return true;
}

}  // namespace tcpserver
