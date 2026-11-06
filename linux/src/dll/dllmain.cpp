#include <windows.h>

#include <cstdio>
#include <string>

#include "config.h"
#include "log.h"
#include "packetlogger.h"
#include "tcp_server.h"

namespace {

// Two independent queues: a slow host must not be able to drop recv packets
// just because the game is sending a burst. 512 * 2048 = 1 MiB each.
packetlogger::Queue g_recv_queue;
packetlogger::Queue g_send_queue;

volatile bool g_quit = false;

const char* hook_state(bool found, bool hooked) {
    if (hooked) {
        return "ACTIVE";
    }
    return found ? "FAILED" : "NOT-FOUND";
}

std::string status_line() {
    const packetlogger::Status& st = packetlogger::status();

    char buffer[512] = {0};
    std::snprintf(
        buffer, sizeof(buffer),
        "STATUS|ntpl %s | recv=0x%08lX send=0x%08lX tnt=0x%08lX | "
        "hooks recv=%s send=%s | drops recv=%lu send=%lu",
        ntpl::kVersion,
        static_cast<unsigned long>(st.recv_addr),
        static_cast<unsigned long>(st.send_addr),
        static_cast<unsigned long>(st.tnt_client),
        hook_state(st.recv_found, st.recv_hooked),
        hook_state(st.send_found, st.send_hooked),
        static_cast<unsigned long>(packetlogger::recv_dropped()),
        static_cast<unsigned long>(packetlogger::send_dropped()));

    return std::string(buffer);
}

// Moves everything captured since the last pass to the file log and, when a
// host is attached, to the TCP stream. Runs even without a host so the file
// log stays a complete record.
void drain(packetlogger::Queue& queue, const char* direction) {
    std::string packet;
    while (queue.pop(packet)) {
        ntpl::log_packet(direction, packet.c_str());
        if (tcpserver::is_connected()) {
            tcpserver::send_line(std::string(direction) + "|" + packet);
        }
    }
}

void handle_command(const std::string& command) {
    if (command.compare(0, 5, "SEND|") == 0) {
        const std::string packet = command.substr(5);
        if (packetlogger::send_packet(packet.c_str())) {
            ntpl::log_packet("MANUAL_SEND", packet.c_str());
            tcpserver::send_line("SEND|" + packet);
            tcpserver::send_line("STATUS|Sent: " + packet);
        } else {
            tcpserver::send_line(
                "STATUS|send failed: hooks are not active in this client");
        }
        return;
    }

    if (command.compare(0, 4, "QUIT") == 0) {
        g_quit = true;
        return;
    }

    if (command.compare(0, 6, "STATUS") == 0) {
        tcpserver::send_line(status_line());
        return;
    }

    tcpserver::send_line("STATUS|unknown command: " + command);
}

DWORD WINAPI main_thread(LPVOID module) {
    ntpl::log_open();
    ntpl::logf("NosTale Packet Logger %s starting", ntpl::kVersion);

    packetlogger::Status status;
    const bool signatures_ok =
        packetlogger::initialize(&g_recv_queue, &g_send_queue, status);

    ntpl::logf("signatures: recv=0x%08lX send=0x%08lX tnt=0x%08lX",
               static_cast<unsigned long>(status.recv_addr),
               static_cast<unsigned long>(status.send_addr),
               static_cast<unsigned long>(status.tnt_client));

    if (signatures_ok) {
        packetlogger::install_hooks(status);
        ntpl::logf("hooks: recv=%s send=%s",
                   hook_state(status.recv_found, status.recv_hooked),
                   hook_state(status.send_found, status.send_hooked));
    } else {
        ntpl::logf(
            "hooks not installed: this client build does not match the "
            "signatures (see docs/hooking.md)");
    }

    const std::uint16_t port = ntpl::config_port();
    bool server_up = false;
    for (int attempt = 0; attempt < 6 && !server_up; ++attempt) {
        server_up = tcpserver::start(port);
        if (!server_up) {
            ntpl::logf("cannot listen on 127.0.0.1:%u, retrying in 5 s",
                       static_cast<unsigned>(port));
            Sleep(5000);
        }
    }

    if (server_up) {
        ntpl::logf("listening on 127.0.0.1:%u", static_cast<unsigned>(port));
    } else {
        ntpl::logf("TCP server could not start; packet capture still works "
                   "and goes to the file log only");
    }

    DWORD last_status_tick = GetTickCount();

    while (!g_quit) {
        if (server_up && !tcpserver::is_connected()) {
            if (tcpserver::accept_client()) {
                ntpl::logf("host connected");
                tcpserver::send_line(status_line());
            }
        }

        if (tcpserver::is_connected()) {
            std::string command;
            while (tcpserver::poll_command(command)) {
                handle_command(command);
            }
        }

        drain(g_recv_queue, "RECV");
        drain(g_send_queue, "SEND");

        const DWORD now = GetTickCount();
        if (now - last_status_tick >= 5000) {
            last_status_tick = now;
            if (tcpserver::is_connected() &&
                (packetlogger::recv_dropped() != 0 ||
                 packetlogger::send_dropped() != 0)) {
                tcpserver::send_line(status_line());
            }
        }

        Sleep(5);
    }

    ntpl::logf("shutting down");
    if (tcpserver::is_connected()) {
        tcpserver::send_line("STATUS|Disconnecting...");
    }

    // Stop enqueueing first, then give a packet that is already inside a hook
    // time to leave it before the original bytes are restored.
    packetlogger::set_enabled(false);
    Sleep(100);
    packetlogger::remove_hooks();

    tcpserver::stop();
    ntpl::logf("stopped");
    ntpl::log_close();

    FreeLibraryAndExitThread(static_cast<HMODULE>(module), 0);
    return 0;
}

}  // namespace

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID reserved) {
    (void)reserved;

    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        HANDLE thread =
            CreateThread(nullptr, 0, main_thread, module, 0, nullptr);
        if (thread != nullptr) {
            CloseHandle(thread);
        }
    }

    return TRUE;
}
