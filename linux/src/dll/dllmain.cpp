#include <windows.h>

#include <cstdio>
#include <string>

#include "clientmove.h"
#include "config.h"
#include "log.h"
#include "memory.h"
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
        "hooks recv=%s send=%s skill=%s | drops recv=%lu send=%lu",
        ntpl::kVersion,
        static_cast<unsigned long>(st.recv_addr),
        static_cast<unsigned long>(st.send_addr),
        static_cast<unsigned long>(st.tnt_client),
        hook_state(st.recv_found, st.recv_hooked),
        hook_state(st.send_found, st.send_hooked),
        hook_state(st.skill_found, st.skill_hooked),
        static_cast<unsigned long>(packetlogger::recv_dropped()),
        static_cast<unsigned long>(packetlogger::send_dropped()));

    return std::string(buffer);
}

// Moves everything captured since the last pass to the file log and, when a
// host is attached, to the TCP stream. Runs even without a host so the file
// log stays a complete record.
void drain(packetlogger::Queue& queue, const char* direction) {
    std::string packet;
    bool wrote = false;
    while (queue.pop(packet)) {
        ntpl::log_packet(direction, packet.c_str());
        wrote = true;
        if (tcpserver::is_connected()) {
            tcpserver::send_line(std::string(direction) + "|" + packet);
        }
    }
    if (wrote) {
        ntpl::log_flush();
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

    // MOVE|x y -- walk through the game's own movement controller. This gives
    // the client a real path and its normal walking animation; the client
    // sends its own walk packets, which the server accepts.
    if (command.compare(0, 5, "MOVE|") == 0) {
        int x = 0;
        int y = 0;
        if (std::sscanf(command.c_str() + 5, "%d %d", &x, &y) != 2) {
            tcpserver::send_line("STATUS|usage: MOVE|x y");
            return;
        }
        std::string error;
        if (!client_move(x, y, error)) {
            tcpserver::send_line("STATUS|" + error);
            return;
        }
        char reply[64] = {0};
        std::snprintf(reply, sizeof(reply), "STATUS|MOVE %d %d", x, y);
        tcpserver::send_line(reply);
        return;
    }

    // POS -- return the client's own local position and char id.
    if (command.compare(0, 3, "POS") == 0) {
        int x = 0;
        int y = 0;
        unsigned long char_id = 0;
        std::string error;
        if (!client_position(x, y, char_id, error)) {
            tcpserver::send_line("STATUS|" + error);
            return;
        }
        char reply[80] = {0};
        std::snprintf(reply, sizeof(reply), "STATUS|POS %d %d %lu", x, y,
                      char_id);
        tcpserver::send_line(reply);
        return;
    }

    if (command.compare(0, 4, "QUIT") == 0) {
        g_quit = true;
        return;
    }

    // READ|0xADDR len -- dump up to 256 bytes. Used to inspect a structure
    // found by SCAN without attaching a debugger to the game.
    if (command.compare(0, 5, "READ|") == 0) {
        unsigned long address = 0;
        int length = 0;
        if (std::sscanf(command.c_str() + 5, "%lx %d", &address, &length) != 2 ||
            length <= 0 || length > 256) {
            tcpserver::send_line("STATUS|usage: READ|0xADDR len(1-256)");
            return;
        }

        unsigned char data[256] = {0};
        SIZE_T got = 0;
        if (!ReadProcessMemory(GetCurrentProcess(),
                               reinterpret_cast<LPCVOID>(address), data,
                               static_cast<SIZE_T>(length), &got) ||
            got == 0) {
            tcpserver::send_line("STATUS|READ failed");
            return;
        }

        char hex[16] = {0};
        std::snprintf(hex, sizeof(hex), "%08lX", address);
        std::string line = "STATUS|READ 0x";
        line += hex;
        line += " ";
        for (SIZE_T i = 0; i < got; ++i) {
            std::snprintf(hex, sizeof(hex), "%02X", data[i]);
            line += hex;
            if ((i + 1) % 4 == 0 && i + 1 < got) {
                line += " ";
            }
        }
        tcpserver::send_line(line);
        return;
    }

    // FIND|value -- find a 32-bit value (charId, pointer) in game memory.
    if (command.compare(0, 5, "FIND|") == 0) {
        unsigned long value = 0;
        if (std::sscanf(command.c_str() + 5, "%lu", &value) != 1) {
            tcpserver::send_line("STATUS|usage: FIND|value");
            return;
        }

        std::string line = "STATUS|FIND " + std::to_string(value);
        for (std::uintptr_t hit :
             Memory::ScanValue(static_cast<std::uint32_t>(value), 20)) {
            char address[32] = {0};
            std::snprintf(address, sizeof(address), " 0x%08lX",
                          static_cast<unsigned long>(hit));
            line += address;
        }
        tcpserver::send_line(line);
        return;
    }

    // SCAN|x y -- search the game's writable memory for a coordinate pair.
    // The engine uses it to locate the player's position after sending a walk;
    // it is a reverse-engineering aid, not something the game can see.
    if (command.compare(0, 5, "SCAN|") == 0) {
        int x = 0;
        int y = 0;
        if (std::sscanf(command.c_str() + 5, "%d %d", &x, &y) != 2) {
            tcpserver::send_line("STATUS|usage: SCAN|x y");
            return;
        }

        std::string line = "STATUS|SCAN " + std::to_string(x) + " " +
                           std::to_string(y);
        for (int width = 1; width <= 4; width *= 2) {
            const std::vector<std::uintptr_t> hits =
                Memory::ScanPair(x, y, width, 12);
            line += " | ";
            line += std::to_string(width);
            line += ":";
            for (std::uintptr_t hit : hits) {
                char address[32] = {0};
                std::snprintf(address, sizeof(address), " 0x%08lX",
                              static_cast<unsigned long>(hit));
                line += address;
            }
        }
        tcpserver::send_line(line);
        return;
    }

    if (command.compare(0, 6, "STATUS") == 0) {
        tcpserver::send_line(status_line());
        return;
    }

    tcpserver::send_line("STATUS|unknown command: " + command);
}

DWORD WINAPI main_thread(LPVOID module) {
    (void)module;
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

        Sleep(1);
    }

    ntpl::logf("shutting down");
    if (tcpserver::is_connected()) {
        tcpserver::send_line("STATUS|Disconnecting...");
    }

    // Stop enqueueing first, then give a packet that is already inside a hook
    // time to leave it before the original bytes are restored.
    packetlogger::set_enabled(false);
    Sleep(100);

    // Capture whatever was enqueued between the last drain and the disable.
    drain(g_recv_queue, "RECV");
    drain(g_send_queue, "SEND");

    packetlogger::remove_hooks();

    tcpserver::stop();
    ntpl::logf("stopped; module stays loaded until the game exits");
    ntpl::log_close();

    // No FreeLibraryAndExitThread: unhooking while another game thread can
    // still be inside a stub is the one race that actually crashes the
    // client. The module and its trampoline stay mapped until the process
    // exits; a fresh injection after QUIT needs a different DLL filename or a
    // client restart.
    return 0;
}

}  // namespace

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID reserved) {
    (void)reserved;

    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        HANDLE thread =
            CreateThread(nullptr, 0, main_thread, module, 0, nullptr);
        if (thread == nullptr) {
            // Let LoadLibraryA fail so the injector reports a real error
            // instead of a loaded-but-inert DLL.
            return FALSE;
        }
        CloseHandle(thread);
    }

    return TRUE;
}
