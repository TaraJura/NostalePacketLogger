#include "clientmove.h"

#include <cstdint>
#include <cstring>
#include <string>

#include <windows.h>

// The client's own "walk to" entry point (this build):
//
//   __fastcall bool MoveTo(PlayerObject *this /*EAX*/, uint32_t packedXY
//                          /*EDX*/, int flags /*ECX*/, int run /*stack*/)
//
// The function computes a path, updates the local movement state and sends
// walk packets itself. Calling it gives natural client-side walking; just
// injecting a `walk` packet only moves the server and makes the client snap
// when the server sends a correction.
extern "C" void ntpl_client_move(void* player, std::uint32_t packed,
                                 std::uintptr_t function);

namespace {

constexpr std::uintptr_t kPlayerGlobal = 0x00792468;
constexpr std::uintptr_t kMoveFunction = 0x00565E88;

// 55 8B EC 83 C4 EC 53 56 57 66 89 4D FA ...  (MoveTo prologue)
const unsigned char kMoveSignature[] = {
    0x55, 0x8B, 0xEC, 0x83, 0xC4, 0xEC, 0x53, 0x56, 0x57, 0x66, 0x89, 0x4D,
    0xFA,
};

bool readable(std::uintptr_t address)
{
    return address >= 0x00010000 &&
           !IsBadReadPtr(reinterpret_cast<const void*>(address), sizeof(void*));
}

}  // namespace

bool client_position(int& x, int& y, unsigned long& char_id,
                     std::string& error)
{
    if (!readable(kPlayerGlobal)) {
        error = "POS: player global not readable";
        return false;
    }
    const std::uintptr_t holder =
        *reinterpret_cast<const std::uintptr_t*>(kPlayerGlobal);
    if (!readable(holder)) {
        error = "POS: player holder not readable";
        return false;
    }
    const std::uintptr_t player =
        *reinterpret_cast<const std::uintptr_t*>(holder);
    if (!readable(player) ||
        IsBadReadPtr(reinterpret_cast<const void*>(player + 4), 0x24)) {
        error = "POS: player object not readable";
        return false;
    }

    // The local player object this build: int16 x at +4, int16 y at +6,
    // uint32 char id at +0x24.
    x = *reinterpret_cast<const std::int16_t*>(player + 4);
    y = *reinterpret_cast<const std::int16_t*>(player + 6);
    char_id = *reinterpret_cast<const std::uint32_t*>(player + 0x24);
    return true;
}

bool client_move(int x, int y, std::string& error)
{
    if (x < 0 || y < 0 || x > 0x7FFF || y > 0x7FFF) {
        error = "MOVE: coordinates out of range";
        return false;
    }

    if (IsBadReadPtr(reinterpret_cast<const void*>(kMoveFunction),
                     sizeof(kMoveSignature)) ||
        std::memcmp(reinterpret_cast<const void*>(kMoveFunction),
                    kMoveSignature, sizeof(kMoveSignature)) != 0) {
        error = "MOVE: client build mismatch";
        return false;
    }

    if (!readable(kPlayerGlobal)) {
        error = "MOVE: player global not readable";
        return false;
    }

    const std::uintptr_t holder =
        *reinterpret_cast<const std::uintptr_t*>(kPlayerGlobal);
    if (!readable(holder)) {
        error = "MOVE: player holder not readable";
        return false;
    }

    const std::uintptr_t player =
        *reinterpret_cast<const std::uintptr_t*>(holder);
    if (!readable(player)) {
        error = "MOVE: player object not readable";
        return false;
    }

    // The client packs the target as x in the low 16 bits and y in the high
    // 16 bits; the pathfinder reads them back with two `movswl`.
    const std::uint32_t packed =
        static_cast<std::uint16_t>(x) |
        (static_cast<std::uint32_t>(static_cast<std::uint16_t>(y)) << 16);

    ntpl_client_move(reinterpret_cast<void*>(player), packed, kMoveFunction);
    return true;
}
