#pragma once

#include <cstddef>
#include <cstdint>

namespace Memory {

// Scans the main module for the first occurrence of `pattern`. `mask` uses 'x'
// for a byte that must match and '?' for a wildcard, exactly like the original
// MSVC implementation. Returns 0 when the signature is not present — which is
// what a game patch looks like.
std::uintptr_t FindPattern(const char* pattern, const char* mask);

// Overwrites `len` bytes at `toHook` with a 5-byte JMP to `ourFunc` and fills
// the remainder with INT3/0x90 padding. `len` must be >= 5 and must land on an
// instruction boundary of the target.
bool Hook(void* toHook, void* ourFunc, int len);

// Restores bytes previously saved with memcpy from the target.
void Patch(void* dst, const void* src, unsigned int size);

}  // namespace Memory
