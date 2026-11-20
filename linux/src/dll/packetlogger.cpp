#include "packetlogger.h"

#include <windows.h>

#include <atomic>
#include <cstdint>
#include <cstring>
#include <limits>
#include <string>

#include "clientmove.h"
#include "log.h"
#include "memory.h"
#include "nostale_string.h"
#include "speedboost.h"

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
std::atomic<bool>    g_block_portal{false};

// Client-side speed boost. The host sets a flat bonus; the worker thread
// applies it directly through the client's own entity speed setter (the same
// function the server's `cond` packet reaches). Nothing on the wire is
// rewritten: the client's walk builder reads the same entity, so the wire
// follows the client's entity speed rather than an independently patched
// field. If the server corrects the player (tp 1 <self>) while a boost is
// active, the boost is turned off at once to stop a correction/kick loop.
std::atomic<int>           g_speed_boost{0};      // effective add
std::atomic<int>           g_speed_request{0};    // what the host asked for
std::atomic<int>           g_cond_base{0};
std::atomic<int>           g_logged_actor{-1};
// -1 = no correction; otherwise the add to settle on. The worker reports it
// to the host. The boost always probes +1 first, then applies the requested
// value; a correction settles at +1 (or 0) instead of feeding a kick loop.
std::atomic<int>           g_speed_correction{-1};
std::atomic<bool>          g_speed_reset{false};
std::atomic<bool>          g_speed_unsupported{false};
std::atomic<unsigned long> g_self_id{0};
std::atomic<unsigned long> g_self_id_at{0};

// The local character id, read from the client's own player object. Cached
// briefly: the recv hook runs on the network thread and a cond arrives every
// few hundred ms, so reading through client_position every time is harmless,
// but the cache keeps a relog from needing it on every packet.
unsigned long local_char_id() {
    const unsigned long now = GetTickCount();
    const unsigned long cached =
        g_self_id.load(std::memory_order_relaxed);
    if (cached != 0 &&
        now - g_self_id_at.load(std::memory_order_relaxed) < 2000) {
        return cached;
    }
    int x = 0;
    int y = 0;
    int resting = -1;
    unsigned long char_id = 0;
    std::string error;
    if (client_position(x, y, char_id, resting, error) && char_id != 0) {
        g_self_id.store(char_id, std::memory_order_relaxed);
        g_self_id_at.store(now, std::memory_order_relaxed);
        return char_id;
    }
    return cached;
}

// Remember the server's current cond speed for the local player. The client
// parses the packet unchanged; the worker thread adds the user's flat bonus
// and sets the player entity directly a moment later, after the client's own
// cond handler has run. No packet bytes are changed.
void note_cond_packet(const char* packet) {
    if (packet == nullptr || std::strncmp(packet, "cond ", 5) != 0) {
        return;
    }
    const speedboost::Cond cond = speedboost::parse_cond(
        packet, g_speed_boost.load(std::memory_order_relaxed));
    if (!cond.valid) {
        return;
    }
    // `cond 1` was observed only for the local player, but official crowded
    // maps may send others too; match the client's own char id when known.
    const unsigned long self = local_char_id();
    if (self == 0 || cond.id != self) {
        return;
    }
    g_cond_base.store(cond.base, std::memory_order_relaxed);
}

// Keep the outgoing walk speed at the server's own cond value while the
// client entity moves faster locally. This is the classic speed-hack shape:
// the server sees a normal walk field, the client renders/animates faster.
void clamp_walk_packet(char* packet) {
    if (packet == nullptr ||
        g_speed_boost.load(std::memory_order_relaxed) <= 0) {
        return;
    }
    const int base = g_cond_base.load(std::memory_order_relaxed);
    if (base <= 0 || std::strncmp(packet, "walk ", 5) != 0) {
        return;
    }
    speedboost::clamp_walk(packet, base);
}

// `preq` (portal request) is what the client sends when the character steps
// on a warp tile; the server answers with sp/c_map and moves it to another
// map. There is no server-side "decline": the only way to stay is not to send
// the request.
bool is_portal_request(const char* packet) {
    return std::memcmp(packet, "preq", 4) == 0 &&
           (packet[4] == '\0' || packet[4] == ' ');
}

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
    // Keep the server's packet untouched. The speed boost acts on the
    // client's entity, not on the wire.
    g_recv->push(packet);
    note_cond_packet(packet);
    // A server correction while the boost is active means the server did not
    // accept the faster walk (the 2026-11-20 teleports/kick). Drop the boost
    // at the first one instead of feeding it more corrections.
    if (g_speed_boost.load(std::memory_order_relaxed) > 0) {
        unsigned long id = 0;
        if (std::sscanf(packet, "tp 1 %lu", &id) == 1 &&
            id == local_char_id()) {
            const int effective = g_speed_boost.load(std::memory_order_relaxed);
            const int settle = effective > 1 ? 1 : 0;
            if (effective <= 1) {
                g_speed_unsupported.store(true, std::memory_order_release);
            }
            g_speed_request.store(settle, std::memory_order_relaxed);
            g_speed_boost.store(settle, std::memory_order_release);
            g_speed_correction.store(settle, std::memory_order_release);
            g_speed_reset.store(true, std::memory_order_release);
            ntpl::logf("speed boost limited to +%d by the server", settle);
        }
    }
}

// Called by hooks.S from inside the game's send path. Returns 1 when the
// original function must run (packet goes out) and 0 when the packet must be
// dropped. The assembler stub checks the value; the skill stub ignores it.
extern "C" int ntpl_on_packet_send(const char* packet) {
    if (!g_enabled.load(std::memory_order_relaxed) || packet == nullptr) {
        return 1;
    }
    // Report the server's own walk speed while the entity moves faster.
    if (g_speed_boost.load(std::memory_order_relaxed) > 0) {
        clamp_walk_packet(const_cast<char*>(packet));
    }
    // Keep the packet in the stream/log even when dropping it: the host can
    // then show "preq blocked" instead of a silent stop.
    if (g_send != nullptr) {
        g_send->push(packet);
    }
    if (g_block_portal.load(std::memory_order_relaxed) &&
        is_portal_request(packet)) {
        return 0;
    }
    return 1;
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

void set_portal_block(bool enabled) {
    g_block_portal.store(enabled, std::memory_order_release);
}

bool portal_block() {
    return g_block_portal.load(std::memory_order_relaxed);
}

void set_speed_boost(int add) {
    if (add < 0) {
        add = 0;
    }
    if (add > 59) {
        add = 59;
    }
    const int previous =
        g_speed_request.exchange(add, std::memory_order_release);
    g_logged_actor.store(-1, std::memory_order_relaxed);
    if (add <= 0) {
        g_speed_boost.store(0, std::memory_order_release);
        g_speed_reset.store(true, std::memory_order_release);
        if (previous != 0) {
            ntpl::logf("speed boost off");
        }
        return;
    }
    // Apply the requested value immediately. The walk layer clamps the
    // outgoing speed back to the server's cond, so there is no probe delay.
    g_speed_boost.store(add, std::memory_order_release);
    g_speed_reset.store(true, std::memory_order_release);
    ntpl::logf("speed +%d (client-side)%s", add, " -- high ban risk");
}

int speed_boost() {
    return g_speed_boost.load(std::memory_order_relaxed);
}

int speed_request() {
    return g_speed_request.load(std::memory_order_relaxed);
}

bool take_speed_correction(int& settle) {
    const int value = g_speed_correction.exchange(-1, std::memory_order_acq_rel);
    if (value < 0) {
        return false;
    }
    settle = value;
    return true;
}

bool take_speed_unsupported() {
    return g_speed_unsupported.exchange(false, std::memory_order_acq_rel);
}

bool take_speed_reset() {
    return g_speed_reset.exchange(false, std::memory_order_acq_rel);
}

int speed_reset_target() {
    int base = g_cond_base.load(std::memory_order_relaxed);
    if (base <= 0) {
        std::string error;
        if (!client_speed_get(base, error) || base <= 0) {
            return 0;
        }
        g_cond_base.store(base, std::memory_order_relaxed);
    }
    return speedboost::target_speed(
        base, g_speed_boost.load(std::memory_order_relaxed));
}

int speed_target() {
    const int add = g_speed_boost.load(std::memory_order_relaxed);
    if (add <= 0) {
        return 0;
    }
    int base = g_cond_base.load(std::memory_order_relaxed);
    if (base <= 0) {
        // No cond seen since injection: use the entity's current (server)
        // speed as the base once, so +1 applies without waiting for the
        // next packet. After this the stored base is refreshed by every
        // local-player cond.
        std::string error;
        if (!client_speed_get(base, error) || base <= 0) {
            return 0;
        }
        g_cond_base.store(base, std::memory_order_relaxed);
    }
    return speedboost::target_speed(base, add);
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
