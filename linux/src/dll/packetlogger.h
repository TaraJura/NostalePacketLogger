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

// Client-side speed boost: while `add` > 0, the worker thread adds that flat
// bonus to the local player's server-reported cond speed and applies it
// through the client's own entity speed setter (a NosTale-style +1, +2, +3).
// No packets are modified; the client's own walk builder reads the same
// entity speed, so the wire follows the client's entity rather than an
// independently patched field. 0 turns it off. This is a client hack (high
// ban risk); the GUI sets it from its Speed + control.
void set_speed_boost(int add);
int speed_boost();      // effective bonus currently applied
int speed_request();    // bonus the host asked for

// Client-side cast speed: `percent` above 100 shortens the skill cooldown the
// client takes from the server's own `su <...> <cooldown> ...` packet, so the
// skill bar becomes usable sooner (the server still enforces its own
// cooldown and may refuse a cast sent too early). The packet is rewritten in
// place before the client parses it; the host's log keeps the original. 100
// is normal. This is a client hack (high ban risk); the GUI sets it from its
// Cast speed control.
void set_cast_speed(int percent);
int cast_speed();

// True once after the server corrected the player's position while a speed
// boost was active. `settle` is the bonus to keep: +1 when a higher value was
// rejected (the +1 probe had passed), 0 when even +1 was rejected. The worker
// reports it to the host so the GUI can show the limit.
bool take_speed_correction(int& settle);

// True once after the server corrected even the +1 probe: this server does
// not accept any client-side speed above its own cond value. The GUI shows
// this as a definitive "not usable here" message.
bool take_speed_unsupported();

// A forced re-apply is pending: boost turned off, a new request started, or
// the server settled on a lower value. The worker must set the entity even
// when the new value is lower (the client's entity otherwise stays at the
// last boosted speed while idle, with no cond to reset it).
bool take_speed_reset();

// The value for a forced reset: the server base when the boost is off, or
// base + the effective bonus. 0 when no base is known.
int speed_reset_target();

// The speed to apply to the local player entity: the last server cond speed
// plus the configured flat bonus, or 0 when no boost/cond is known. The
// worker thread calls the client's own setter with this value.
int speed_target();

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
