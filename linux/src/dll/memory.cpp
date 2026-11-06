#include "memory.h"

#include <windows.h>
#include <psapi.h>

#include <cstring>
#include <limits>

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

    std::memset(toHook, 0x90, static_cast<std::size_t>(len));

    *reinterpret_cast<unsigned char*>(toHook) = 0xE9;
    *reinterpret_cast<std::int32_t*>(reinterpret_cast<unsigned char*>(toHook) + 1) =
        static_cast<std::int32_t>(relative);

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

    std::memcpy(dst, src, size);
    FlushInstructionCache(GetCurrentProcess(), dst, size);

    DWORD ignored = 0;
    VirtualProtect(dst, size, old_protection, &ignored);
}

}  // namespace Memory
