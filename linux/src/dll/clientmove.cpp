#include "clientmove.h"

#include <cstdint>
#include <cstring>
#include <string>

#include <windows.h>
#include <tlhelp32.h>

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
extern "C" void ntpl_client_speed(void* entity, int speed,
                                  std::uintptr_t function);

namespace {

// Client build 2026-11-25 (0.9.3.3262, the Gameforge maintenance update).
// The previous build's values were 0x007954B4 / 0x00576920 (0.9.3.3261,
// 2026-11-18).  Wine maps the PE at its usual 0x00400000 base, so these are
// stable virtual addresses; the prologue signatures below are still used to
// re-find MOVE after a patch by scanning the code range.
constexpr std::uintptr_t kPlayerGlobal = 0x007964BC;
constexpr std::uintptr_t kMoveFunction = 0x0056620C;

// The entity speed setter the client's `cond` handler calls. It writes the
// speed byte at entity +0xAA (and recomputes the walking timer). The walk
// packet builder reads that same byte, so setting it moves the player.
// This function did not move in the 2026-11-25 build.
constexpr std::uintptr_t kSpeedFunction = 0x00492938;

// 55 8B EC 83 C4 EC 53 56 57 66 89 4D FA ...  (MoveTo prologue)
const unsigned char kMoveSignature[] = {
    0x55, 0x8B, 0xEC, 0x83, 0xC4, 0xEC, 0x53, 0x56, 0x57, 0x66, 0x89, 0x4D,
    0xFA,
};

// 53 83 C4 F8 8B D8 3A 93 AA 00 00 00 ...  (speed setter prologue)
const unsigned char kSpeedSignature[] = {
    0x53, 0x83, 0xC4, 0xF8, 0x8B, 0xD8, 0x3A, 0x93, 0xAA, 0x00, 0x00, 0x00,
};

bool readable(std::uintptr_t address)
{
    return address >= 0x00010000 &&
           !IsBadReadPtr(reinterpret_cast<const void*>(address), sizeof(void*));
}

}  // namespace

bool client_position(int& x, int& y, unsigned long& char_id, int& resting,
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
        IsBadReadPtr(reinterpret_cast<const void*>(player + 4), 0xA6)) {
        error = "POS: player object not readable";
        return false;
    }

    // The local player object (verified on 0.9.3.3262 too): int16 x at +4,
    // int16 y at +6, uint32 char id at +0x24, rest state at +0xA9 (1 sitting,
    // 0 standing).
    x = *reinterpret_cast<const std::int16_t*>(player + 4);
    y = *reinterpret_cast<const std::int16_t*>(player + 6);
    char_id = *reinterpret_cast<const std::uint32_t*>(player + 0x24);
    const std::uint8_t rest =
        *reinterpret_cast<const std::uint8_t*>(player + 0xA9);
    resting = rest <= 1 ? static_cast<int>(rest) : -1;
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

    // Sitting blocks movement; the client's MoveTo would silently do nothing
    // (no walk packet), which looks like a stuck bot to the host.
    if (!IsBadReadPtr(reinterpret_cast<const void*>(player + 0xA9), 1) &&
        *reinterpret_cast<const std::uint8_t*>(player + 0xA9) == 1) {
        error = "MOVE: character is sitting (stand up first)";
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

namespace {

// The local player entity whose speed byte the walk packet builder reads:
// manager = [[0x7964BC]], entity = [manager + 0x20].
bool player_entity(std::uintptr_t& entity, std::string& error)
{
    if (!readable(kPlayerGlobal)) {
        error = "player global not readable";
        return false;
    }
    const std::uintptr_t holder =
        *reinterpret_cast<const std::uintptr_t*>(kPlayerGlobal);
    if (!readable(holder)) {
        error = "player holder not readable";
        return false;
    }
    const std::uintptr_t manager =
        *reinterpret_cast<const std::uintptr_t*>(holder);
    if (!readable(manager) ||
        IsBadReadPtr(reinterpret_cast<const void*>(manager + 0x20),
                     sizeof(std::uintptr_t))) {
        error = "player object not readable";
        return false;
    }
    entity = *reinterpret_cast<const std::uintptr_t*>(manager + 0x20);
    if (!readable(entity) ||
        IsBadReadPtr(reinterpret_cast<const void*>(entity), 0xC0)) {
        error = "player entity not readable";
        return false;
    }
    return true;
}

}  // namespace

bool client_speed_get(int& speed, std::string& error)
{
    speed = 0;
    std::uintptr_t entity = 0;
    if (!player_entity(entity, error)) {
        error = "SPEED: " + error;
        return false;
    }
    speed = *reinterpret_cast<const std::uint8_t*>(entity + 0xAA);
    return true;
}

namespace {

// The client's movement timing reads a 60-entry float table, indexed by the
// entity's speed byte. Its setter refuses anything above 59
// (`cmp dl,0x3b / ja`). Both limits are lifted here for the local player:
// the cap byte becomes 0xff and every code reference to the table is pointed
// at a 256-entry copy. Entries 0..59 are the client's own values, so normal
// play is unchanged; 60..255 continue the same linear timing and entry 255
// can be scaled to emulate a speed above the byte maximum.
//
// 0.9.3.3262 (2026-11-25): the table moved 0x78A3C8 -> 0x78B3C8 and the
// references use the indexed `opcode modrm sib disp32` form
// (e.g. `D8 34 85 C8 B3 78 00`) as well as the old `opcode 85 disp32`.
constexpr std::uintptr_t kCodeStart = 0x00401000;
constexpr std::uintptr_t kCodeEnd = 0x00787DC4;
constexpr std::uintptr_t kSpeedTable = 0x0078B3C8;
constexpr int kSpeedTableEntries = 256;

std::uintptr_t g_speed_table = 0;  // our extended table; 0 = not installed
bool g_speed_extend_failed = false;

struct SuspendedThreads {
    HANDLE handles[256];
    int count;
};

void suspend_other_threads(SuspendedThreads& suspended)
{
    suspended.count = 0;
    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
    if (snapshot == INVALID_HANDLE_VALUE) {
        return;
    }
    const DWORD pid = GetCurrentProcessId();
    const DWORD self = GetCurrentThreadId();
    THREADENTRY32 entry = {};
    entry.dwSize = sizeof(entry);
    if (Thread32First(snapshot, &entry)) {
        do {
            if (entry.th32OwnerProcessID != pid ||
                entry.th32ThreadID == self) {
                continue;
            }
            if (suspended.count >=
                static_cast<int>(sizeof(suspended.handles) /
                                 sizeof(suspended.handles[0]))) {
                break;
            }
            HANDLE thread = OpenThread(THREAD_SUSPEND_RESUME, FALSE,
                                       entry.th32ThreadID);
            if (thread == nullptr) {
                continue;
            }
            if (SuspendThread(thread) == static_cast<DWORD>(-1)) {
                CloseHandle(thread);
                continue;
            }
            suspended.handles[suspended.count++] = thread;
        } while (Thread32Next(snapshot, &entry));
    }
    CloseHandle(snapshot);
}

void resume_threads(SuspendedThreads& suspended)
{
    for (int i = 0; i < suspended.count; ++i) {
        ResumeThread(suspended.handles[i]);
        CloseHandle(suspended.handles[i]);
    }
    suspended.count = 0;
}

// Install the extended table once. The game's other threads are suspended
// while the code bytes change, so none of them runs a half-written table
// address. The table lives in the game's address space and is deliberately
// never freed: after the DLL unloads, the patched instructions still run and
// must find a valid table there.
bool extend_speed_table(std::string& error)
{
    if (g_speed_table != 0) {
        return true;
    }
    if (g_speed_extend_failed) {
        error = "SPEED: this client build cannot go above 59";
        return false;
    }

    const unsigned char* code =
        reinterpret_cast<const unsigned char*>(kCodeStart);
    const std::size_t code_size = kCodeEnd - kCodeStart;
    unsigned char* table_refs[32];
    int ref_count = 0;
    const unsigned char table_bytes[4] = {
        static_cast<unsigned char>(kSpeedTable & 0xFF),
        static_cast<unsigned char>((kSpeedTable >> 8) & 0xFF),
        static_cast<unsigned char>((kSpeedTable >> 16) & 0xFF),
        static_cast<unsigned char>((kSpeedTable >> 24) & 0xFF),
    };
    for (std::size_t i = 3; i + 4 <= code_size; ++i) {
        if (std::memcmp(code + i, table_bytes, 4) != 0) {
            continue;
        }
        // Two encodings reference the table:
        //   opcode 85 <disp32>           (e.g. D9 85 C8 A3 78 00)
        //   opcode modrm 85 <disp32>     (e.g. D8 34 85 C8 B3 78 00)
        if (code[i - 1] != 0x85) {
            continue;
        }
        bool matches = code[i - 2] == 0xD9 || code[i - 2] == 0xD8 ||
                       code[i - 2] == 0x8B;
        if (!matches) {
            const unsigned char modrm = code[i - 2];
            matches = (modrm & 0xC7) == 0x04 &&
                      (code[i - 3] == 0xD9 || code[i - 3] == 0xD8 ||
                       code[i - 3] == 0x8B);
        }
        if (!matches) {
            continue;
        }
        if (ref_count >=
            static_cast<int>(sizeof(table_refs) / sizeof(table_refs[0]))) {
            break;
        }
        table_refs[ref_count++] = const_cast<unsigned char*>(code + i);
    }

    const unsigned char cap_expected[5] = {0x80, 0xFA, 0x3B, 0x77, 0x5C};
    const unsigned char* cap =
        reinterpret_cast<const unsigned char*>(kSpeedFunction) + 0x0E;
    const float* source = reinterpret_cast<const float*>(kSpeedTable);
    if (ref_count != 11 || std::memcmp(cap, cap_expected, 5) != 0 ||
        !(source[1] > 0.0001f && source[1] < 0.0003f &&
          source[59] > 0.0100f && source[59] < 0.0130f)) {
        g_speed_extend_failed = true;
        error = "SPEED: this client build cannot go above 59";
        return false;
    }

    float* table = static_cast<float*>(
        VirtualAlloc(nullptr, kSpeedTableEntries * sizeof(float),
                     MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
    if (table == nullptr) {
        error = "SPEED: cannot allocate the speed table";
        return false;
    }
    for (int i = 0; i < 60; ++i) {
        table[i] = source[i];
    }
    for (int i = 60; i < kSpeedTableEntries; ++i) {
        table[i] = 0.0002f * static_cast<float>(i);
    }

    SuspendedThreads suspended;
    suspend_other_threads(suspended);
    DWORD old_protection = 0;
    bool patched = VirtualProtect(
                       reinterpret_cast<void*>(kCodeStart), code_size,
                       PAGE_EXECUTE_READWRITE, &old_protection) != FALSE;
    if (patched) {
        const std::uintptr_t address =
            reinterpret_cast<std::uintptr_t>(table);
        for (int i = 0; i < ref_count; ++i) {
            std::memcpy(table_refs[i], &address, sizeof(address));
        }
        const unsigned char unlimited = 0xFF;
        std::memcpy(const_cast<unsigned char*>(cap) + 2, &unlimited, 1);
        FlushInstructionCache(GetCurrentProcess(),
                              reinterpret_cast<const void*>(kCodeStart),
                              code_size);
        DWORD ignored = 0;
        VirtualProtect(reinterpret_cast<void*>(kCodeStart), code_size,
                       old_protection, &ignored);
    }
    resume_threads(suspended);

    if (!patched) {
        VirtualFree(table, 0, MEM_RELEASE);
        error = "SPEED: cannot patch the client speed table";
        return false;
    }
    g_speed_table = reinterpret_cast<std::uintptr_t>(table);
    return true;
}

bool speed_set_byte(int speed, int& previous, int& applied,
                    std::string& error, bool force)
{
    previous = 0;
    applied = 0;
    if (speed <= 0 || speed > 255) {
        error = "SPEED: value out of range";
        return false;
    }

    if (IsBadReadPtr(reinterpret_cast<const void*>(kSpeedFunction),
                     sizeof(kSpeedSignature)) ||
        std::memcmp(reinterpret_cast<const void*>(kSpeedFunction),
                    kSpeedSignature, sizeof(kSpeedSignature)) != 0) {
        error = "SPEED: client build mismatch";
        return false;
    }

    std::uintptr_t entity = 0;
    if (!player_entity(entity, error)) {
        error = "SPEED: " + error;
        return false;
    }

    previous = *reinterpret_cast<const std::uint8_t*>(entity + 0xAA);
    if (previous == speed) {
        applied = previous;
        return true;
    }
    if (!force && previous > speed) {
        // Never lower a buffed value during normal operation; a forced reset
        // (boost off, or the server's accepted settle value) may lower it.
        applied = previous;
        return true;
    }

    ntpl_client_speed(reinterpret_cast<void*>(entity), speed,
                      kSpeedFunction);
    applied = *reinterpret_cast<const std::uint8_t*>(entity + 0xAA);
    if (applied == previous) {
        error = "SPEED: client ignored the value";
        return false;
    }
    return true;
}

}  // namespace

bool client_speed_apply(int target, int& previous, int& applied,
                        std::string& error, bool force)
{
    previous = 0;
    applied = 0;
    if (target <= 0) {
        error = "SPEED: value out of range";
        return false;
    }
    if (target <= 59) {
        return speed_set_byte(target, previous, applied, error, force);
    }
    if (!extend_speed_table(error)) {
        return false;
    }

    // The client's byte tops out at 255. Above that the byte stays 255 and
    // its timing entry is scaled to the emulated speed, so scrolling can keep
    // raising the value past the byte limit as well.
    const int byte = target < 255 ? target : 255;
    float* table = reinterpret_cast<float*>(g_speed_table);
    table[byte] =
        0.0002f * static_cast<float>(byte == target ? byte : target);

    int byte_previous = 0;
    int byte_applied = 0;
    if (!speed_set_byte(byte, byte_previous, byte_applied, error, force)) {
        return false;
    }
    if (byte_applied != byte) {
        error = "SPEED: client kept a higher buffed speed";
        return false;
    }
    previous = byte_previous;
    applied = target;
    return true;
}
