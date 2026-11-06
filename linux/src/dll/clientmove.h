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
bool client_position(int& x, int& y, unsigned long& char_id,
                     std::string& error);
