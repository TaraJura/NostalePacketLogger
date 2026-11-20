#include <windows.h>

#include <cstdio>
#include <string>

#include "clientmove.h"
#include "clienttarget.h"
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
        "hooks recv=%s send=%s skill=%s | drops recv=%lu send=%lu | "
        "portalblock=%s | speed=+%d | cast=%d%%",
        ntpl::kVersion,
        static_cast<unsigned long>(st.recv_addr),
        static_cast<unsigned long>(st.send_addr),
        static_cast<unsigned long>(st.tnt_client),
        hook_state(st.recv_found, st.recv_hooked),
        hook_state(st.send_found, st.send_hooked),
        hook_state(st.skill_found, st.skill_hooked),
        static_cast<unsigned long>(packetlogger::recv_dropped()),
        static_cast<unsigned long>(packetlogger::send_dropped()),
        packetlogger::portal_block() ? "BLOCKED" : "off",
        packetlogger::speed_boost(),
        packetlogger::cast_speed());

    return std::string(buffer);
}

// The GUI/CLI runs outside Wine and cannot see Windows PIDs, so each DLL
// instance advertises its listening port in a small file keyed by the game's
// Windows PID. The GUI pairs that file with the injector's --list output and
// can then attach to any of several running clients, each on its own port.
std::string port_file_path(DWORD pid) {
    char path[MAX_PATH] = {0};
    std::snprintf(path, sizeof(path), "C:\\ntpl-%lu.port",
                  static_cast<unsigned long>(pid));
    return std::string(path);
}

void publish_port(std::uint16_t port) {
    const std::string path = port_file_path(GetCurrentProcessId());
    char text[16] = {0};
    const int length = std::snprintf(text, sizeof(text), "%u\r\n", port);

    HANDLE file =
        CreateFileA(path.c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr,
                    CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) {
        ntpl::logf("cannot write %s", path.c_str());
        return;
    }

    DWORD written = 0;
    WriteFile(file, text, static_cast<DWORD>(length), &written, nullptr);
    CloseHandle(file);
}

void unpublish_port() {
    DeleteFileA(port_file_path(GetCurrentProcessId()).c_str());
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
        int resting = -1;
        unsigned long char_id = 0;
        std::string error;
        if (!client_position(x, y, char_id, resting, error)) {
            tcpserver::send_line("STATUS|" + error);
            return;
        }
        char reply[96] = {0};
        std::snprintf(reply, sizeof(reply), "STATUS|POS %d %d %lu %d", x, y,
                      char_id, resting);
        tcpserver::send_line(reply);
        return;
    }

    // TARGET|entityId -- select an entity through the client's own selection
    // routine. The client highlights it and sends its own `ncif`; the server
    // then accepts the following `u_s` for this target.
    if (command.compare(0, 7, "TARGET|") == 0) {
        unsigned long id = 0;
        if (std::sscanf(command.c_str() + 7, "%lu", &id) != 1 || id == 0) {
            tcpserver::send_line("STATUS|usage: TARGET|entityId");
            return;
        }

        unsigned long entity = 0;
        unsigned long controller = 0;
        std::string error;
        if (!client_target(id, entity, controller, error)) {
            char reply[256] = {0};
            std::snprintf(reply, sizeof(reply), "STATUS|TARGET %lu ERR %s",
                          id, error.c_str());
            tcpserver::send_line(reply);
            return;
        }

        char reply[128] = {0};
        std::snprintf(reply, sizeof(reply),
                      "STATUS|TARGET %lu OK 0x%08lX 0x%08lX", id, entity,
                      controller);
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

    // PORTALS|1 / PORTALS|0 -- drop or allow the client's own `preq` portal
    // requests. The packet is still logged, so the host sees the attempt.
    if (command.compare(0, 8, "PORTALS|") == 0) {
        const bool enabled = command.size() > 8 && command[8] == '1';
        packetlogger::set_portal_block(enabled);
        ntpl::logf("portal blocking %s", enabled ? "on" : "off");
        tcpserver::send_line(std::string("STATUS|PORTALS ") +
                             (enabled ? "BLOCKED" : "ALLOWED"));
        return;
    }

    // SPEED|N -- add N to the local player's movement speed in the client
    // (0 = normal), the way a NosTale speed buff works (+1, +2, +3, ...).
    // The incoming `cond` speed is rewritten before the client parses it;
    // see speedboost.h. This is a client hack and high ban risk, which is why
    // the default is 0.
    if (command.compare(0, 6, "SPEED|") == 0) {
        int add = 0;
        if (std::sscanf(command.c_str() + 6, "%d", &add) != 1) {
            tcpserver::send_line("STATUS|usage: SPEED|add(0-1000000)");
            return;
        }
        packetlogger::set_speed_boost(add);
        const int requested = packetlogger::speed_request();
        char reply[64] = {0};
        std::snprintf(reply, sizeof(reply), "STATUS|SPEED +%d", requested);
        tcpserver::send_line(reply);
        return;
    }

    // CAST|N -- shorten the skill cooldown the client takes from the server's
    // `su` packet to 100/N percent (100 = normal, 200 = half). The client's
    // skill bar clears sooner; the server keeps its own cooldown and may
    // refuse a cast sent before it is over. Client hack, high ban risk.
    if (command.compare(0, 5, "CAST|") == 0) {
        int percent = 100;
        if (std::sscanf(command.c_str() + 5, "%d", &percent) != 1) {
            tcpserver::send_line("STATUS|usage: CAST|percent(100-1000)");
            return;
        }
        packetlogger::set_cast_speed(percent);
        const int applied = packetlogger::cast_speed();
        char reply[64] = {0};
        std::snprintf(reply, sizeof(reply), "STATUS|CAST %d%%", applied);
        tcpserver::send_line(reply);
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

    const std::uint16_t base_port = ntpl::config_port();
    std::uint16_t port = base_port;
    bool server_up = false;
    // Several clients can share one prefix. The base port belongs to whichever
    // DLL starts first (a restarted client may also find a stale listener left
    // behind by Wine), so every instance probes upward for a free port instead
    // of waiting forever on one that will never free up.
    for (unsigned offset = 0; offset < 10 && !server_up; ++offset) {
        const unsigned candidate = static_cast<unsigned>(base_port) + offset;
        if (candidate > 65535) {
            break;
        }
        port = static_cast<std::uint16_t>(candidate);
        server_up = tcpserver::start(port);
        if (!server_up && offset == 0) {
            ntpl::logf("cannot listen on 127.0.0.1:%u, trying the next ports",
                       static_cast<unsigned>(base_port));
        }
    }

    if (server_up) {
        ntpl::logf("listening on 127.0.0.1:%u", static_cast<unsigned>(port));
        publish_port(port);
    } else {
        ntpl::logf("TCP server could not start; packet capture still works "
                   "and goes to the file log only");
    }

    DWORD last_status_tick = GetTickCount();
    DWORD last_speed_tick = 0;
    int logged_speed_target = -1;

    while (!g_quit) {
        if (server_up && !tcpserver::is_connected()) {
            // The host is gone (closed or crashed). Stop re-applying the
            // boost; the server's next `cond` returns the player to normal.
            if (packetlogger::speed_boost() != 0) {
                packetlogger::set_speed_boost(0);
            }
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
        // Speed +: re-apply the flat bonus to the local player entity every
        // 100 ms. The client's own cond handler resets the entity to the
        // server value whenever a cond arrives, so a one-shot set would be
        // undone; this watchdog keeps it applied without touching packets.
        // The client's walk builder reads this same entity, so the wire only
        // carries a speed the player is actually moving at.
        if (now - last_speed_tick >= 25) {
            last_speed_tick = now;
            if (packetlogger::take_speed_reset()) {
                const int reset_target = packetlogger::speed_reset_target();
                if (reset_target > 0) {
                    int previous = 0;
                    int applied = 0;
                    std::string error;
                    if (client_speed_apply(reset_target, previous, applied,
                                           error, true) &&
                        applied != previous) {
                        ntpl::logf("speed: player actor reset %d -> %d",
                                   previous, applied);
                    }
                }
            }
            const int target = packetlogger::speed_target();
            if (target > 0) {
                int previous = 0;
                int applied = 0;
                std::string error;
                if (client_speed_apply(target, previous, applied, error) &&
                    applied == target && target != logged_speed_target) {
                    logged_speed_target = target;
                    ntpl::logf(
                        "speed: player actor %d -> %d (target %d)", previous,
                        applied, target);
                    if (tcpserver::is_connected()) {
                        tcpserver::send_line(
                            "STATUS|SPEED APPLIED " +
                            std::to_string(target));
                    }
                } else if (!error.empty() && target > 59 &&
                           target != logged_speed_target) {
                    // A client build whose speed table cannot be extended
                    // keeps the 59 limit; say so once per requested value
                    // instead of silently doing nothing.
                    logged_speed_target = target;
                    ntpl::logf("speed: %s", error.c_str());
                    if (tcpserver::is_connected()) {
                        tcpserver::send_line(
                            "STATUS|SPEED EXTEND FAILED " + error);
                    }
                }
            }
        }
        if (packetlogger::take_speed_unsupported()) {
            ntpl::logf(
                "speed boost unsupported: the server rejected even +1");
            if (tcpserver::is_connected()) {
                tcpserver::send_line("STATUS|SPEED UNSUPPORTED");
            }
        }
        int settle = 0;
        if (packetlogger::take_speed_correction(settle)) {
            if (settle > 0) {
                ntpl::logf(
                    "speed boost settled at +%d after a server correction",
                    settle);
                if (tcpserver::is_connected()) {
                    tcpserver::send_line("STATUS|SPEED TUNED " +
                                         std::to_string(settle));
                }
            } else {
                ntpl::logf(
                    "speed boost turned off after a server correction");
                if (tcpserver::is_connected()) {
                    tcpserver::send_line("STATUS|SPEED CORRECTED");
                }
            }
        }
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
    unpublish_port();
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
