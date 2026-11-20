#pragma once

// Parsing for the client-side speed boost.
//
// The DLL only reads the server's `cond 1 <id> <unknown> <unknown> <speed>`
// packet to learn the local player's base speed. It never rewrites a packet:
// the worker thread adds the configured flat bonus and calls the client's own
// entity speed setter. The client's walk builder reads that same entity, so
// the wire is not independently inflated; a server correction at the first
// teleport turns the boost off (the 2026-11-20 teleports and kick).
//
// Deliberately header-only and free of Windows headers so the host build can
// unit-test it (`make speed-test`) without Wine or the game.

#include <cstddef>
#include <cstring>

namespace speedboost {

constexpr std::size_t kMaxTokens = 8;

struct Token {
    std::size_t offset = 0;
    std::size_t length = 0;
};

struct Tokens {
    Token items[kMaxTokens];
    std::size_t count = 0;
};

struct Cond {
    bool valid = false;
    unsigned long id = 0;  // entity id from the packet
    int base = 0;          // server's speed
    int target = 0;        // base + the configured flat bonus (capped)
};

// Split a space-separated packet. The game hands the hooks CRLF-terminated
// strings (`... 12\r\n`); the terminator is trimmed and is not part of the
// last field. Single spaces only; a malformed shape is rejected.
inline bool split(const char* text, Tokens& out)
{
    out = Tokens{};
    if (text == nullptr || *text == '\0' || *text == ' ') {
        return false;
    }
    std::size_t length = std::strlen(text);
    while (length > 0 &&
           (text[length - 1] == '\r' || text[length - 1] == '\n')) {
        --length;
    }
    if (length == 0) {
        return false;
    }
    const char* end = text + length;
    const char* p = text;
    while (p < end) {
        const char* start = p;
        while (p < end && *p != ' ') {
            ++p;
        }
        if (out.count >= kMaxTokens) {
            return false;
        }
        out.items[out.count].offset =
            static_cast<std::size_t>(start - text);
        out.items[out.count].length =
            static_cast<std::size_t>(p - start);
        ++out.count;
        if (p < end && *p == ' ') {
            ++p;
            if (p >= end || *p == ' ') {
                return false;
            }
        }
    }
    return out.count > 0;
}

inline bool token_is(const char* text, const Token& token, const char* value)
{
    const std::size_t length = std::strlen(value);
    return token.length == length &&
           std::memcmp(text + token.offset, value, length) == 0;
}

// Positive decimal integer; false for an empty field or a non-digit.
inline bool parse_uint(const char* text, const Token& token,
                       unsigned long& value)
{
    if (token.length == 0) {
        return false;
    }
    unsigned long parsed = 0;
    for (std::size_t i = 0; i < token.length; ++i) {
        const char c = text[token.offset + i];
        if (c < '0' || c > '9') {
            return false;
        }
        parsed = parsed * 10 + static_cast<unsigned long>(c - '0');
        if (parsed > 1000000UL) {
            return false;
        }
    }
    value = parsed;
    return true;
}

// Speed for `base` with a flat `add` bonus, the way a NosTale speed buff
// works (+1, +2, +3, ...). Returns base when the bonus is off.
inline int target_speed(int base, int add)
{
    if (base <= 0 || add <= 0) {
        return base;
    }
    // The client's own cond speed setter ignores anything above 0x3B (59):
    // `cmp dl,0x3b / ja skip`. Stay at or below the cap, and never lower a
    // value the client already accepted.
    if (base >= 59) {
        return base;
    }
    const int sum = base + add;
    return sum >= 59 ? 59 : sum;
}

// Parse `cond 1 <id> <unknown> <unknown> <speed>` for the local player.
inline Cond parse_cond(const char* text, int add)
{
    Cond cond;
    Tokens tokens;
    if (!split(text, tokens) || tokens.count != 6) {
        return cond;
    }
    if (!token_is(text, tokens.items[0], "cond") ||
        !token_is(text, tokens.items[1], "1")) {
        return cond;
    }
    unsigned long id = 0;
    unsigned long base = 0;
    if (!parse_uint(text, tokens.items[2], id) ||
        !parse_uint(text, tokens.items[5], base)) {
        return cond;
    }
    cond.valid = true;
    cond.id = id;
    cond.base = static_cast<int>(base);
    cond.target = target_speed(cond.base, add);
    return cond;
}

}  // namespace speedboost
