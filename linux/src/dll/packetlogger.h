#pragma once

#include <cstdint>

#include "packet_queue.h"

namespace packetlogger {

// 4096 slots x 2048 bytes = 8 MiB per direction. The game can emit hundreds
// of entity updates in one server tick; 512 slots overflowed during real
// gameplay and dropped thousands of packets.
using Queue = ntpl::PacketQueue<4096, 2048>;

struct Status {
    bool           recv_found = false;
    bool           send_found = false;
    bool           skill_found = false;
    bool           recv_hooked = false;
    bool           send_hooked = false;
    bool           skill_hooked = false;
    std::uintptr_t recv_addr = 0;
    std::uintptr_t send_addr = 0;
    std::uintptr_t skill_addr = 0;
    std::uintptr_t tnt_client = 0;
};

// Scans the game's image for the two signatures and prepares the send
// trampoline. Does not modify the game. Safe to call once.
bool initialize(Queue* recv, Queue* send, Status& status);

// Installs the detours. Returns true only if every hook that could be prepared
// was installed. A missing signature is not a crash: the DLL keeps running and
// reports the failure over TCP so the host can show it.
bool install_hooks(Status& status);

// Stops capturing and restores the original bytes. Call set_enabled(false)
// first and give in-flight packets a moment, otherwise a thread can be inside
// the hook while the bytes change.
void remove_hooks();
void set_enabled(bool enabled);

// Portal blocking: while enabled, the client's own `preq` (portal request)
// packet is logged but never reaches the socket, so walking on a warp tile
// cannot change maps. The GUI sets this from its Block portals toggle.
void set_portal_block(bool enabled);
bool portal_block();

// Sends one already-formatted packet through the game's own send function.
// Must be called from the worker thread (never from inside a hook).
bool send_packet(const char* packet);

const Status& status();

std::uint32_t recv_dropped();
std::uint32_t send_dropped();

}  // namespace packetlogger

// Implemented in hooks.S. Referenced by the C++ side, defined in assembly.
extern "C" {
void ntpl_hook_recv();
void ntpl_hook_send();
void ntpl_hook_skill();
void ntpl_game_send(char* packet);

extern std::uintptr_t ntpl_recv_original_call;
extern std::uintptr_t ntpl_recv_jmp_back;
extern std::uintptr_t ntpl_send_trampoline;
extern std::uintptr_t ntpl_skill_original_call;
extern std::uintptr_t ntpl_skill_jmp_back;
extern std::uintptr_t ntpl_tnt_client;
extern std::uintptr_t ntpl_send_addr;
}
