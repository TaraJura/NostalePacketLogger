#pragma once

// Pure helpers for the client-side Cast speed hack, kept free of windows.h so
// the host can unit-test them (`make speed-test` compiles this header on
// Linux). The packet rewriting itself lives in packetlogger.cpp, which knows
// the client's `su`/`sr` field layout.
//
// The server sends each skill's cooldown in deciseconds. The DLL zeroes the
// number in the client's receive buffer before the client parses it; the host
// queue and the file log keep the original. The token keeps its width (all
// zeros), so the packet's length/format stays valid.

#include <cstddef>
#include <cstdlib>
#include <cstring>

namespace castspeed {

// Replace the decimal token at `field` (zero-based, space-separated) with
// zeros in place. Returns true when the packet changed. The 65535 "no
// cooldown" sentinel is left alone, as are empty, negative and non-numeric
// fields.
inline bool zero_field(char* packet, int field)
{
    if (packet == nullptr || field < 0) {
        return false;
    }
    char* token = packet;
    for (int i = 0; i < field; ++i) {
        token = std::strchr(token, ' ');
        if (token == nullptr) {
            return false;
        }
        ++token;
    }
    char* end = std::strchr(token, ' ');
    const std::size_t length =
        end != nullptr ? static_cast<std::size_t>(end - token)
                       : std::strlen(token);
    if (length == 0 || length > 9) {
        return false;
    }
    char text[16] = {0};
    std::memcpy(text, token, length);
    char* parse_end = nullptr;
    const long value = std::strtol(text, &parse_end, 10);
    if (parse_end == text || *parse_end != '\0' || value < 0) {
        return false;
    }
    if (value == 0xFFFF) {
        // This protocol's "no cooldown" sentinel; zeroing is harmless but
        // pointless, and leaving it alone keeps the test intent clear.
        return false;
    }
    std::memset(token, '0', length);
    return true;
}

}  // namespace castspeed
