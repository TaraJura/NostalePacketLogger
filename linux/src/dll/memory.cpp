#include "memory.h"

#include <windows.h>
#include <psapi.h>

#include <cstring>
#include <limits>
#include <vector>

namespace {

MODULEINFO main_module() {
    MODULEINFO info = {};
    const HMODULE module = GetModuleHandleA(nullptr);
    if (module == nullptr) {
        return info;
    }
    if (!GetModuleInformation(GetCurrentProcess(), module, &info, sizeof(info))) {
        info = {};
    }
    return info;
}

// Writes up to 8 bytes with a single aligned InterlockedExchange64 when the
// target range fits inside one qword. A game thread then sees either the old
// instruction stream or the new jump, never a half-written E9.
void write_block(void* destination, const void* source, unsigned int size) {
    const std::uintptr_t address = reinterpret_cast<std::uintptr_t>(destination);
    const std::uintptr_t aligned = address & ~static_cast<std::uintptr_t>(7);
    const std::size_t offset = static_cast<std::size_t>(address - aligned);

    if (size <= 8 && offset + size <= 8) {
        volatile LONG64* block = reinterpret_cast<volatile LONG64*>(aligned);
        LONG64 value = *block;
        std::memcpy(reinterpret_cast<unsigned char*>(&value) + offset, source,
                    size);
        InterlockedExchange64(block, value);
        return;
    }

    // Rare: the 5-6 byte patch crosses an 8-byte boundary. There is no
    // single-instruction atomic store for that on i386. The trampoline and
    // the DLL stay mapped at shutdown, so the remaining window only matters
    // while hooking/unhooking, not while the game runs.
    std::memcpy(destination, source, size);
}

void self_module_range(std::uintptr_t& begin, std::uintptr_t& end) {
    HMODULE self = nullptr;
    GetModuleHandleExA(
        GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
            GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
        reinterpret_cast<LPCSTR>(&self_module_range), &self);

    MODULEINFO info = {};
    if (self != nullptr) {
        GetModuleInformation(GetCurrentProcess(), self, &info, sizeof(info));
    }
    begin = reinterpret_cast<std::uintptr_t>(info.lpBaseOfDll);
    end = begin + info.SizeOfImage;
}

// Shared scan loop. `values_per_match` is 1 for a single value and 2 for a
// pair; the matcher receives a pointer into the chunk. `on_hit` runs for every
// match and may stop the scan early by returning false.
template <typename Match, typename OnHit>
void scan_memory(int width, int values_per_match, std::size_t limit,
                 Match match, OnHit on_hit) {
    if ((width != 1 && width != 2 && width != 4) || limit == 0) {
        return;
    }

    std::uintptr_t self_begin = 0;
    std::uintptr_t self_end = 0;
    self_module_range(self_begin, self_end);

    SYSTEM_INFO system = {};
    GetSystemInfo(&system);

    std::uintptr_t address =
        reinterpret_cast<std::uintptr_t>(system.lpMinimumApplicationAddress);
    const std::uintptr_t maximum =
        reinterpret_cast<std::uintptr_t>(system.lpMaximumApplicationAddress);

    constexpr std::uint64_t kMaxScanBytes = 512ull * 1024ull * 1024ull;
    std::uint64_t scanned = 0;

    // Stack buffer, deliberately: the current thread's stack is skipped below,
    // so the scan can never read its own search parameters or buffer back as
    // a match. A heap buffer showed exactly that false positive during the
    // first live test.
    unsigned char buffer[64 * 1024];

    const NT_TIB* tib = reinterpret_cast<const NT_TIB*>(NtCurrentTeb());
    const std::uintptr_t stack_low =
        reinterpret_cast<std::uintptr_t>(tib->StackLimit);
    const std::uintptr_t stack_high =
        reinterpret_cast<std::uintptr_t>(tib->StackBase);

    const DWORD readable = PAGE_READWRITE | PAGE_WRITECOPY |
                           PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY;

    const std::size_t match_bytes =
        static_cast<std::size_t>(width * values_per_match);

    std::size_t hits = 0;

    while (address < maximum && hits < limit && scanned < kMaxScanBytes) {
        MEMORY_BASIC_INFORMATION info = {};
        if (VirtualQuery(reinterpret_cast<LPCVOID>(address), &info,
                         sizeof(info)) == 0) {
            break;
        }

        const std::uintptr_t base =
            reinterpret_cast<std::uintptr_t>(info.BaseAddress);
        const std::uintptr_t size =
            static_cast<std::uintptr_t>(info.RegionSize);
        const std::uintptr_t next = base + size;

        const bool usable =
            info.State == MEM_COMMIT && (info.Protect & readable) != 0 &&
            (info.Protect & (PAGE_GUARD | PAGE_NOACCESS)) == 0 &&
            !(base < self_end && next > self_begin) &&
            !(base < stack_high && next > stack_low);

        if (usable) {
            for (std::uintptr_t offset = 0;
                 offset < size && hits < limit && scanned < kMaxScanBytes;
                 offset += sizeof(buffer)) {
                const std::size_t chunk = static_cast<std::size_t>(
                    (size - offset) < sizeof(buffer) ? (size - offset)
                                                    : sizeof(buffer));
                SIZE_T got = 0;
                if (!ReadProcessMemory(GetCurrentProcess(),
                                       reinterpret_cast<LPCVOID>(base + offset),
                                       buffer, chunk, &got) ||
                    got < static_cast<SIZE_T>(match_bytes)) {
                    continue;
                }
                scanned += got;

                for (std::size_t i = 0; i + match_bytes <= got;
                     i += static_cast<std::size_t>(width)) {
                    const std::uintptr_t absolute = base + offset + i;
                    if (absolute % static_cast<std::uintptr_t>(width) != 0) {
                        continue;
                    }

                    if (!match(buffer + i)) {
                        continue;
                    }
                    ++hits;
                    if (!on_hit(absolute) || hits >= limit) {
                        return;
                    }
                }
            }
        }

        if (next <= address) {
            break;
        }
        address = next;
    }
}

}  // namespace

namespace Memory {

std::uintptr_t FindPattern(const char* pattern, const char* mask) {
    if (pattern == nullptr || mask == nullptr) {
        return 0;
    }

    const MODULEINFO info = main_module();
    if (info.lpBaseOfDll == nullptr || info.SizeOfImage == 0) {
        return 0;
    }

    const std::uintptr_t base = reinterpret_cast<std::uintptr_t>(info.lpBaseOfDll);
    const std::size_t size = static_cast<std::size_t>(info.SizeOfImage);
    const std::size_t pattern_length = std::strlen(mask);

    if (pattern_length == 0 || pattern_length > size) {
        return 0;
    }

    const unsigned char* bytes = reinterpret_cast<const unsigned char*>(base);
    const unsigned char* wanted = reinterpret_cast<const unsigned char*>(pattern);

    for (std::size_t i = 0; i + pattern_length <= size; ++i) {
        bool found = true;
        for (std::size_t j = 0; j < pattern_length; ++j) {
            if (mask[j] == '?') {
                continue;
            }
            if (bytes[i + j] != wanted[j]) {
                found = false;
                break;
            }
        }
        if (found) {
            return base + i;
        }
    }

    return 0;
}

bool Hook(void* toHook, void* ourFunc, int len) {
    if (toHook == nullptr || ourFunc == nullptr || len < 5) {
        return false;
    }

    DWORD old_protection = 0;
    if (!VirtualProtect(toHook, static_cast<SIZE_T>(len), PAGE_EXECUTE_READWRITE,
                        &old_protection)) {
        return false;
    }

    const std::intptr_t relative =
        reinterpret_cast<std::intptr_t>(ourFunc) -
        reinterpret_cast<std::intptr_t>(toHook) - 5;

    // E9 takes a signed 32-bit displacement. In a 32-bit process the modules
    // are usually close enough, but an unlucky DLL base can be more than 2 GiB
    // away; a wrapped displacement would jump into random memory.
    if (relative < std::numeric_limits<std::int32_t>::min() ||
        relative > std::numeric_limits<std::int32_t>::max()) {
        DWORD ignored = 0;
        VirtualProtect(toHook, static_cast<SIZE_T>(len), old_protection,
                       &ignored);
        return false;
    }

    unsigned char patch[16];
    std::memset(patch, 0x90, static_cast<std::size_t>(len));
    patch[0] = 0xE9;
    *reinterpret_cast<std::int32_t*>(patch + 1) =
        static_cast<std::int32_t>(relative);

    write_block(toHook, patch, static_cast<unsigned int>(len));

    DWORD ignored = 0;
    VirtualProtect(toHook, static_cast<SIZE_T>(len), old_protection, &ignored);
    FlushInstructionCache(GetCurrentProcess(), toHook, static_cast<SIZE_T>(len));

    return true;
}

void Patch(void* dst, const void* src, unsigned int size) {
    if (dst == nullptr || src == nullptr || size == 0) {
        return;
    }

    DWORD old_protection = 0;
    if (!VirtualProtect(dst, size, PAGE_EXECUTE_READWRITE, &old_protection)) {
        return;
    }

    write_block(dst, src, size);
    FlushInstructionCache(GetCurrentProcess(), dst, size);

    DWORD ignored = 0;
    VirtualProtect(dst, size, old_protection, &ignored);
}

std::vector<std::uintptr_t> ScanPair(std::int32_t x, std::int32_t y, int width,
                                     std::size_t limit) {
    std::vector<std::uintptr_t> results;
    scan_memory(
        width, 2, limit,
        [x, y, width](const unsigned char* p) -> bool {
            if (width == 1) {
                const auto* pair =
                    reinterpret_cast<const std::uint8_t*>(p);
                return pair[0] == static_cast<std::uint8_t>(x) &&
                       pair[1] == static_cast<std::uint8_t>(y);
            }
            if (width == 2) {
                const auto* pair =
                    reinterpret_cast<const std::int16_t*>(p);
                return pair[0] == static_cast<std::int16_t>(x) &&
                       pair[1] == static_cast<std::int16_t>(y);
            }
            const auto* pair = reinterpret_cast<const std::int32_t*>(p);
            return pair[0] == x && pair[1] == y;
        },
        [&results](std::uintptr_t absolute) {
            results.push_back(absolute);
            return true;
        });
    return results;
}

std::vector<std::uintptr_t> ScanValue(std::uint32_t value, std::size_t limit) {
    std::vector<std::uintptr_t> results;
    scan_memory(
        4, 1, limit,
        [value](const unsigned char* p) -> bool {
            std::uint32_t found = 0;
            std::memcpy(&found, p, sizeof(found));
            return found == value;
        },
        [&results](std::uintptr_t absolute) {
            results.push_back(absolute);
            return true;
        });
    return results;
}

std::uintptr_t ScanFirstValue(std::uint32_t value,
                              bool (*accept)(std::uintptr_t, void*),
                              void* context) {
    std::uintptr_t result = 0;
    scan_memory(
        4, 1, std::numeric_limits<std::size_t>::max(),
        [value](const unsigned char* p) -> bool {
            std::uint32_t found = 0;
            std::memcpy(&found, p, sizeof(found));
            return found == value;
        },
        [accept, context, &result](std::uintptr_t absolute) {
            if (accept(absolute, context)) {
                result = absolute;
                return false;
            }
            return true;
        });
    return result;
}

}  // namespace Memory
