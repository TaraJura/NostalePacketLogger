#pragma once

// Parsing for the client-side speed boost.
//
// The DLL only reads the server's `cond 1 <id> <unknown> <unknown> <speed>`
// packet to learn the local player's base speed. It never rewrites a packet:
// the worker thread adds the configured flat bonus and calls the client's own
// entity speed setter. The client's walk builder reads that same entity, so
// the wire is not independently inflated; a server correction settles at +1
// (or turns the boost off if even +1 was rejected), the 2026-11-20 safety.
//
// Deliberately header-only and free of Windows headers so the host build can
// unit-test it (`make speed-test`) without Wine or the game.

#include <cstddef>
#include <cstdio>
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
        if (parsed > 2000000000UL) {
            return false;
        }
    }
    value = parsed;
    return true;
}

// Highest emulated walk speed the flat bonus may reach. The client's own speed
// byte is capped at 255; values above that are carried by scaling the player's
// table entry (the DLL extends the client's speed table, see clientmove.cpp),
// so the host spin has no small cap of its own.
constexpr int kMaxSpeed = 2000000000;

// Speed for `base` with a flat `add` bonus, the way a NosTale speed buff
// works (+1, +2, +3, ...). Returns base when the bonus is off. The original
// client only accepts 0..59 and indexes a 60-entry timing table; the DLL
// raises that limit and supplies the table, so the sum is no longer clamped
// at 59 here.
inline int target_speed(int base, int add)
{
    if (base <= 0 || add <= 0) {
        return base;
    }
    const long long sum = static_cast<long long>(base) + add;
    return sum > kMaxSpeed ? kMaxSpeed : static_cast<int>(sum);
}

// Report the server's own base speed in an outgoing walk packet while the
// client entity moves faster. Only same-width digits are replaced, so the
// AnsiString length/terminator stay valid.
inline bool clamp_walk(char* text, int base)
{
    if (text == nullptr || base <= 0) {
        return false;
    }
    Tokens tokens;
    if (!split(text, tokens) || tokens.count != 5) {
        return false;
    }
    if (!token_is(text, tokens.items[0], "walk")) {
        return false;
    }
    unsigned long speed = 0;
    if (!parse_uint(text, tokens.items[4], speed) ||
        speed <= static_cast<unsigned long>(base)) {
        return false;
    }
    const std::size_t width = tokens.items[4].length;
    char digits[16] = {0};
    const int written =
        std::snprintf(digits, sizeof(digits), "%d", base);
    if (written <= 0 || width >= sizeof(digits) ||
        static_cast<std::size_t>(written) > width) {
        return false;
    }
    // Keep the AnsiString length valid when the boosted speed has more digits
    // than the server's own value: 255 -> 012, not a shorter string. The
    // server's integer parser accepts leading zeros; the field width is what
    // the client's string header depends on.
    char padded[16] = {0};
    std::memset(padded, '0', width);
    std::memcpy(padded + (width - static_cast<std::size_t>(written)), digits,
                static_cast<std::size_t>(written));
    std::memcpy(text + tokens.items[4].offset, padded, width);
    return true;
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
