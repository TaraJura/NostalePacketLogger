#include "packetlogger.h"

#include <windows.h>

#include <atomic>
#include <cstdint>
#include <cstring>
#include <limits>

#include "memory.h"
#include "nostale_string.h"

// The signatures are the same ones the Windows build uses. They are tied to a
// specific NostaleClientX.exe build and must be re-derived after every client
// patch; docs/hooking.md describes how.
namespace {

constexpr char kRecvPattern[] =
    "\xe8\x00\x00\x00\x00\x33\xc0\x55\x68\x00\x00\x00\x00\x64\xff\x00"
    "\x64\x89\x00\x8d\x45\x00\x8b\x55";
constexpr char kRecvMask[] = "x????xxxx????xx?xx?xx?xx";

// The send function starts 6 bytes before this signature; those 6 bytes are
// the function prologue copied into the trampoline.
constexpr char kSendPattern[] = "\xeb\x00\xeb\x00\x39\x19\x8b\xd6";
constexpr char kSendMask[] = "x?x?xxxx";

// The `u_s` builder calls the lower-level send function here, with the
// finished packet in ECX. The normal send hook is a different wrapper and
// does not see client skill packets.
constexpr char kSkillPattern[] =
    "\xBA\x40\xD6\x72\x00\xE8\x00\x00\x00\x00\x8B\xD8\x84\xDB\x74";
constexpr char kSkillMask[] = "xxxxxx????xxxxx?";

constexpr char kTntPattern[] =
    "\xA1\x00\x00\x00\x00\x8B\x00\xE8\x00\x00\x00\x00\xA1\x00\x00\x00"
    "\x00\x8B\x00\x33\xD2\x89\x10";
constexpr char kTntMask[] = "x????xxx????x????xxxxxx";

packetlogger::Queue* g_recv = nullptr;
packetlogger::Queue* g_send = nullptr;

packetlogger::Status g_status;
std::atomic<bool>    g_enabled{false};

std::uint8_t g_recv_original[5] = {0};
std::uint8_t g_send_original[6] = {0};
std::uint8_t g_skill_original[5] = {0};
void*        g_trampoline_mem = nullptr;

}  // namespace

extern "C" {
std::uintptr_t ntpl_recv_original_call = 0;
std::uintptr_t ntpl_recv_jmp_back = 0;
std::uintptr_t ntpl_send_trampoline = 0;
std::uintptr_t ntpl_skill_original_call = 0;
std::uintptr_t ntpl_skill_jmp_back = 0;
std::uintptr_t ntpl_tnt_client = 0;
std::uintptr_t ntpl_send_addr = 0;

// Called by hooks.S from inside the game's recv path.
void ntpl_on_packet_recv(const char* packet) {
    if (!g_enabled.load(std::memory_order_relaxed) || packet == nullptr ||
        g_recv == nullptr) {
        return;
    }
    g_recv->push(packet);
}

// Called by hooks.S from inside the game's send path.
void ntpl_on_packet_send(const char* packet) {
    if (!g_enabled.load(std::memory_order_relaxed) || packet == nullptr ||
        g_send == nullptr) {
        return;
    }
    g_send->push(packet);
}
}

namespace packetlogger {

bool initialize(Queue* recv, Queue* send, Status& status) {
    g_recv = recv;
    g_send = send;
    g_status = Status{};
    g_enabled.store(false, std::memory_order_release);

    if (g_recv == nullptr || g_send == nullptr) {
        status = g_status;
        return false;
    }

    // ---- recv: the hook lands on a 5-byte `call rel32` ---------------------
    const std::uintptr_t recv_addr =
        Memory::FindPattern(kRecvPattern, kRecvMask);
    if (recv_addr != 0) {
        g_status.recv_found = true;
        g_status.recv_addr = recv_addr;

        const std::int32_t relative =
            *reinterpret_cast<const std::int32_t*>(recv_addr + 1);
        ntpl_recv_original_call =
            recv_addr + 5 + static_cast<std::uintptr_t>(
                               static_cast<std::intptr_t>(relative));
        ntpl_recv_jmp_back = recv_addr + 5;

        std::memcpy(g_recv_original,
                    reinterpret_cast<const void*>(recv_addr),
                    sizeof(g_recv_original));
    }

    // ---- send: hook 6 bytes before the signature ---------------------------
    const std::uintptr_t send_hint = Memory::FindPattern(kSendPattern, kSendMask);
    if (send_hint > 6) {
        const std::uintptr_t send_addr = send_hint - 6;
        g_status.send_found = true;
        g_status.send_addr = send_addr;
        ntpl_send_addr = send_addr;

        std::memcpy(g_send_original,
                    reinterpret_cast<const void*>(send_addr),
                    sizeof(g_send_original));

        g_trampoline_mem =
            VirtualAlloc(nullptr, 32, MEM_COMMIT | MEM_RESERVE,
                         PAGE_EXECUTE_READWRITE);
        if (g_trampoline_mem != nullptr) {
            auto* trampoline = static_cast<std::uint8_t*>(g_trampoline_mem);
            std::memcpy(trampoline, g_send_original, sizeof(g_send_original));
            trampoline[6] = 0xE9;

            const std::intptr_t relative =
                static_cast<std::intptr_t>(send_addr + 6) -
                (reinterpret_cast<std::intptr_t>(trampoline) + 6 + 5);

            if (relative < std::numeric_limits<std::int32_t>::min() ||
                relative > std::numeric_limits<std::int32_t>::max()) {
                // The trampoline landed too far away for an E9. Do not
                // install the send hook; recv can still work.
                VirtualFree(g_trampoline_mem, 0, MEM_RELEASE);
                g_trampoline_mem = nullptr;
            } else {
                *reinterpret_cast<std::int32_t*>(trampoline + 7) =
                    static_cast<std::int32_t>(relative);
                ntpl_send_trampoline =
                    reinterpret_cast<std::uintptr_t>(trampoline);
            }
        }
    }

    // ---- skill packet: capture the client's own `u_s` builder call site ----
    const std::uintptr_t skill_hint =
        Memory::FindPattern(kSkillPattern, kSkillMask);
    if (skill_hint != 0) {
        const std::uintptr_t call_addr = skill_hint + 5;
        const std::int32_t relative =
            *reinterpret_cast<const std::int32_t*>(call_addr + 1);
        const std::uintptr_t target =
            call_addr + 5 + static_cast<std::uintptr_t>(
                               static_cast<std::intptr_t>(relative));
        // Stay inside the game image; the send wrapper is around 0x4EB610.
        if (target >= 0x00400000 && target < 0x01400000) {
            g_status.skill_found = true;
            g_status.skill_addr = call_addr;
            ntpl_skill_original_call = target;
            ntpl_skill_jmp_back = call_addr + 5;
            std::memcpy(g_skill_original,
                        reinterpret_cast<const void*>(call_addr),
                        sizeof(g_skill_original));
        }
    }

    // ---- the TNTClient global used by the send path ------------------------
    const std::uintptr_t tnt = Memory::FindPattern(kTntPattern, kTntMask);
    if (tnt != 0) {
        ntpl_tnt_client = tnt + 1;
        g_status.tnt_client = ntpl_tnt_client;
    }

    status = g_status;

    // A missing send path must not disable a working recv hook. The main
    // thread installs whatever was prepared and reports the rest as FAILED or
    // NOT-FOUND.
    return g_status.recv_found || g_status.send_found;
}

bool install_hooks(Status& status) {
    bool ok = true;

    if (g_status.recv_found) {
        g_status.recv_hooked = Memory::Hook(
            reinterpret_cast<void*>(g_status.recv_addr),
            reinterpret_cast<void*>(&ntpl_hook_recv), 5);
        ok = ok && g_status.recv_hooked;
    }

    if (g_status.send_found && ntpl_send_trampoline != 0) {
        g_status.send_hooked = Memory::Hook(
            reinterpret_cast<void*>(g_status.send_addr),
            reinterpret_cast<void*>(&ntpl_hook_send), 6);
        ok = ok && g_status.send_hooked;
    }

    if (g_status.skill_found && ntpl_skill_original_call != 0) {
        g_status.skill_hooked = Memory::Hook(
            reinterpret_cast<void*>(g_status.skill_addr),
            reinterpret_cast<void*>(&ntpl_hook_skill), 5);
        ok = ok && g_status.skill_hooked;
    }

    g_enabled.store(g_status.recv_hooked || g_status.send_hooked ||
                        g_status.skill_hooked,
                    std::memory_order_release);
    status = g_status;
    return ok;
}

void remove_hooks() {
    g_enabled.store(false, std::memory_order_release);

    if (g_status.recv_hooked) {
        Memory::Patch(reinterpret_cast<void*>(g_status.recv_addr),
                      g_recv_original, sizeof(g_recv_original));
        g_status.recv_hooked = false;
    }

    if (g_status.send_hooked) {
        Memory::Patch(reinterpret_cast<void*>(g_status.send_addr),
                      g_send_original, sizeof(g_send_original));
        g_status.send_hooked = false;
    }

    if (g_status.skill_hooked) {
        Memory::Patch(reinterpret_cast<void*>(g_status.skill_addr),
                      g_skill_original, sizeof(g_skill_original));
        g_status.skill_hooked = false;
    }

    // Deliberately keep the trampoline allocated until the process exits.
    // A game thread can be between the hook address and the trampoline when
    // the original bytes are restored; freeing it here would send that thread
    // into freed memory. 32 bytes is cheap, a crashed client is not.
    //
    // The DLL itself is also left loaded at shutdown (see dllmain.cpp): the
    // stubs must stay mapped for the same reason.
    (void)g_trampoline_mem;
}

void set_enabled(bool enabled) {
    g_enabled.store(enabled, std::memory_order_release);
}

bool send_packet(const char* packet) {
    if (packet == nullptr || ntpl_send_addr == 0 || ntpl_tnt_client == 0) {
        return false;
    }
    if (!g_enabled.load(std::memory_order_relaxed)) {
        return false;
    }

    NostaleStringA string(packet);
    if (string.get() == nullptr) {
        return false;
    }

    ntpl_game_send(string.get());
    return true;
}

const Status& status() {
    return g_status;
}

std::uint32_t recv_dropped() {
    return g_recv != nullptr ? g_recv->dropped() : 0;
}

std::uint32_t send_dropped() {
    return g_send != nullptr ? g_send->dropped() : 0;
}

}  // namespace packetlogger
