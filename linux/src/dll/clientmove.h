#pragma once

#include <string>

// Ask the game client to walk to a map cell through its own movement
// controller (pathfinding + local animation + its own walk packet), instead of
// injecting a packet that only the server sees.
//
// This is client-build-specific: it validates the prologue of the movement
// function before calling it and reports a normal error on a mismatch.
bool client_move(int x, int y, std::string& error);

// Read the client's own local player position and char id. This is the
// position the client renders and pathfinds from; it is updated by the
// client's movement controller, not by injected packets.
//
// `resting` is 1 when the character sits, 0 when standing, -1 when the client
// does not expose it. Sitting blocks movement and casting, so the host needs
// this after attaching to a character that sat before the host connected.
bool client_position(int& x, int& y, unsigned long& char_id, int& resting,
                     std::string& error);

// Set the local player entity's movement speed through the client's own
// speed setter (the same function the server's `cond` packet reaches).
// Normally only raises the value (a buffed speed is never lowered); `force`
// also lowers it, for a boost-off/reset or the server's accepted settle
// value. `previous`/`applied` are the entity's speed byte before and after.
// Validates the client build prologue before calling.
bool client_speed_set(int speed, int& previous, int& applied,
                      std::string& error, bool force = false);

// Read the local player entity's current speed byte. Used as the base when
// the DLL starts after the last `cond`, so +1 applies without waiting for the
// next one.
bool client_speed_get(int& speed, std::string& error);
