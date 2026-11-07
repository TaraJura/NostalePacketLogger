#pragma once

#include <string>

// Select an entity in the game client through the client's own selection
// routine: the client highlights the target and sends its own `ncif` packet.
// The server validates that packet before it accepts the following `u_s`, so
// injecting `ncif` alone is not enough -- the client's local selection has to
// change too.
//
// This is client-build-specific: it validates the selector prologue before
// calling it and reports a normal error on a mismatch.
//
// `entity` and `controller` receive the addresses the call used, for the
// STATUS reply and diagnostics.
bool client_target(unsigned long entity_id, unsigned long& entity,
                   unsigned long& controller, std::string& error);
