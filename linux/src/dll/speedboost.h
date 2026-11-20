#pragma once

// Pure packet rewriting for the client-side speed boost.
//
// The game client takes the local player's movement speed from the server's
// `cond 1 <id> <unknown> <unknown> <speed>` packet and then sends its own
// `walk <x> <y> <checksum> <speed>` with that same value. Rewriting the speed
// field in the incoming `cond` makes the client walk faster; rewriting the
// `walk` field as a fallback keeps the packet consistent if the client uses a
// cached speed.
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

// A planned rewrite. `valid` means the packet had the expected shape;
// `rewrite` means the replacement fits the existing field width.
struct Change {
    bool valid = false;
    bool rewrite = false;
    unsigned long id = 0;  // cond only: entity id from the packet
    int from = 0;          // speed currently in the packet
    int to = 0;            // speed to write
    std::size_t offset = 0;
    std::size_t length = 0;
};

inline bool split(const char* text, Tokens& out)
{
    out = Tokens{};
    if (text == nullptr || *text == '\0' || *text == ' ') {
        return false;
    }
    const char* p = text;
    while (*p != '\0') {
        const char* start = p;
        while (*p != '\0' && *p != ' ') {
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
        if (*p == ' ') {
            ++p;
            // Single spaces only: a double or trailing space is not a packet
            // this code understands, and refusing it is safer than guessing.
            if (*p == ' ' || *p == '\0') {
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

// Positive decimal integer; returns false for an empty field or a non-digit.
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

inline std::size_t digits(int value)
{
    std::size_t count = 1;
    while (value >= 10) {
        value /= 10;
        ++count;
    }
    return count;
}

// Speed for `base` at `percent` more (100 = double). Returns base when the
// boost is off; the caller treats to == from as "no change".
inline int target_speed(int base, int percent)
{
    if (base <= 0 || percent <= 0) {
        return base;
    }
    const long long scaled =
        (static_cast<long long>(base) * (100LL + percent) + 50LL) / 100LL;
    if (scaled > 999999LL) {
        return 999999;
    }
    return static_cast<int>(scaled);
}

inline bool write_token(char* text, const Change& change)
{
    char digits_text[16] = {0};
    const int written =
        std::snprintf(digits_text, sizeof(digits_text), "%d", change.to);
    if (written <= 0 ||
        static_cast<std::size_t>(written) != change.length) {
        return false;
    }
    std::memcpy(text + change.offset, digits_text,
                static_cast<std::size_t>(written));
    return true;
}

inline Change plan_cond(const char* text, int percent)
{
    Change change;
    Tokens tokens;
    if (!split(text, tokens) || tokens.count != 6) {
        return change;
    }
    if (!token_is(text, tokens.items[0], "cond") ||
        !token_is(text, tokens.items[1], "1")) {
        return change;
    }
    unsigned long id = 0;
    unsigned long base = 0;
    if (!parse_uint(text, tokens.items[2], id) ||
        !parse_uint(text, tokens.items[5], base)) {
        return change;
    }
    change.valid = true;
    change.id = id;
    change.from = static_cast<int>(base);
    change.to = target_speed(change.from, percent);
    change.offset = tokens.items[5].offset;
    change.length = tokens.items[5].length;
    // The field is rewritten in place, so the replacement must not need more
    // digits than the original (a Delphi AnsiString has exactly one byte for
    // the terminator after the old field).
    change.rewrite =
        change.to != change.from && digits(change.to) == change.length;
    return change;
}

// `base` is the server's last cond speed and `target` its boosted value.
// Returns a rewrite only when the walk still carries the unboosted value; a
// walk that already equals the target came from the patched cond.
inline Change plan_walk(const char* text, int base, int target)
{
    Change change;
    Tokens tokens;
    if (base <= 0 || target <= base) {
        return change;
    }
    if (!split(text, tokens) || tokens.count != 5) {
        return change;
    }
    if (!token_is(text, tokens.items[0], "walk")) {
        return change;
    }
    unsigned long speed = 0;
    if (!parse_uint(text, tokens.items[4], speed)) {
        return change;
    }
    change.valid = true;
    change.from = static_cast<int>(speed);
    change.to = target;
    change.offset = tokens.items[4].offset;
    change.length = tokens.items[4].length;
    change.rewrite = change.from == base &&
                     digits(change.to) == change.length;
    return change;
}

inline bool apply(char* text, const Change& change)
{
    if (text == nullptr || !change.valid || !change.rewrite) {
        return false;
    }
    return write_token(text, change);
}

}  // namespace speedboost
