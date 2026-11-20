#pragma once

// Pure helpers for the client-side Cast speed hack, kept free of windows.h so
// the host can unit-test them (`make speed-test` compiles this header on
// Linux). The packet rewriting itself lives in packetlogger.cpp, which knows
// the client's `su`/`sr` field layout.
//
// The server sends each skill's cooldown in deciseconds. The DLL shortens the
// number in the client's receive buffer before the client parses it; the host
// queue and the file log keep the original. Only scaling down is supported,
// so the rewritten token never needs more digits than the original and the
// packet's length/format stays valid.

#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace castspeed {

// Rewrite the decimal token at `field` (zero-based, space-separated) in
// place, keeping the token's width by left-padding with '0'. Returns true
// when the packet changed. `percent` must be > 100.
inline bool scale_field(char* packet, int field, int percent)
{
    if (packet == nullptr || field < 0 || percent <= 100) {
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
        // The skill packet's "no cooldown" sentinel; scaling it would turn a
        // ready skill into a very long cooldown.
        return false;
    }
    const long scaled = value * 100 / percent;
    char digits[16] = {0};
    const int written =
        std::snprintf(digits, sizeof(digits), "%ld", scaled);
    if (written <= 0 || static_cast<std::size_t>(written) > length) {
        return false;
    }
    std::memset(token, '0', length);
    std::memcpy(token + (length - static_cast<std::size_t>(written)), digits,
                static_cast<std::size_t>(written));
    return true;
}

}  // namespace castspeed
