#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

namespace Memory {

// Scans the main module for the first occurrence of `pattern`. `mask` uses 'x'
// for a byte that must match and '?' for a wildcard. Returns 0 when the
// signature is not present -- which is what a game patch looks like.
std::uintptr_t FindPattern(const char* pattern, const char* mask);

// Overwrites `len` bytes at `toHook` with a 5-byte JMP to `ourFunc` and fills
// the remainder with NOPs. `len` must be >= 5 and must land on an instruction
// boundary. Uses an atomic 8-byte exchange when the range fits in one aligned
// qword, so another thread never executes a half-written jump.
bool Hook(void* toHook, void* ourFunc, int len);

// Restores bytes previously saved with memcpy from the target.
void Patch(void* dst, const void* src, unsigned int size);

// Scans committed, readable, writable memory for a pair of coordinates stored
// as 1-, 2- or 4-byte integers (width = 1, 2 or 4). Returns up to `limit`
// addresses. Used by SCAN to locate a predicted walk target.
std::vector<std::uintptr_t> ScanPair(std::int32_t x, std::int32_t y, int width,
                                     std::size_t limit);

// Scans for a single 4-byte value. Used by FIND to locate a character/entity
// structure by its charId.
std::vector<std::uintptr_t> ScanValue(std::uint32_t value, std::size_t limit);

}  // namespace Memory
