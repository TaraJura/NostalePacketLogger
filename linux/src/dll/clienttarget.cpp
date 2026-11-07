#include "clienttarget.h"

#include <windows.h>
#include <psapi.h>

#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

#include "memory.h"

// The client's entity selector (this build):
//
//   void SelectEntity(Controller *controller /*EAX*/, Entity *entity /*EDX*/)
//
// The controller is the object the game UI uses for the current target; it is
// reached through a fixed global. The selector checks that the entity is
// alive, valid for the current map and targetable, then sends `ncif <type>
// <id>` itself and stores the entity as the controller's target. Because the
// server validates `u_s` against the client's own selection, this client-side
// call -- not an injected `ncif` -- is what makes the bot's attack land on the
// intended monster.
//
// Entity objects for this build: vtable at +0 (inside the main module), kind
// at +4 (1 player, 2 monster), entity id at +8, alive flag at +0x33, type at
// +0xA9 (4 = not targetable). The controller keeps the selected object at
// +0x34.
extern "C" void ntpl_client_target(void* controller, void* entity,
                                   std::uintptr_t function);

namespace {

constexpr std::uintptr_t kSelectionGlobal = 0x00795200;
constexpr std::uintptr_t kSelector = 0x0056964C;

// 53 56 57 8B F2 8B F8 85 F6 ... (SelectEntity prologue)
const unsigned char kSelectorSignature[] = {
    0x53, 0x56, 0x57, 0x8B, 0xF2, 0x8B, 0xF8, 0x85, 0xF6,
};

constexpr std::uintptr_t kControllerTarget = 0x34;

struct CacheEntry {
    std::uint32_t id;
    std::uint32_t pad;
    std::uintptr_t entity;
};

constexpr int kCacheSize = 8;
CacheEntry g_cache[kCacheSize] = {};
int g_cache_next = 0;

bool readable(std::uintptr_t address, std::size_t size)
{
    return address >= 0x00010000 && address + size > address &&
           !IsBadReadPtr(reinterpret_cast<const void*>(address), size);
}

std::uint32_t load_u32(std::uintptr_t address)
{
    return *reinterpret_cast<const std::uint32_t*>(address);
}

std::uint8_t load_u8(std::uintptr_t address)
{
    return *reinterpret_cast<const std::uint8_t*>(address);
}

bool module_range(std::uintptr_t& begin, std::uintptr_t& end)
{
    const HMODULE module = GetModuleHandleA(nullptr);
    MODULEINFO info = {};
    if (module == nullptr ||
        !GetModuleInformation(GetCurrentProcess(), module, &info,
                              sizeof(info)) ||
        info.lpBaseOfDll == nullptr || info.SizeOfImage == 0) {
        begin = 0;
        end = 0;
        return false;
    }
    begin = reinterpret_cast<std::uintptr_t>(info.lpBaseOfDll);
    end = begin + info.SizeOfImage;
    return true;
}

bool looks_like_entity(std::uintptr_t entity, std::uint32_t id,
                       std::uintptr_t module_begin, std::uintptr_t module_end)
{
    if (!readable(entity, 0xB0)) {
        return false;
    }
    const std::uint32_t vtable = load_u32(entity);
    if (vtable < module_begin || vtable >= module_end) {
        return false;
    }
    const std::uint32_t kind = load_u32(entity + 4);
    if (kind == 0 || kind > 8) {
        return false;
    }
    if (load_u32(entity + 8) != id) {
        return false;
    }
    if (load_u8(entity + 0x33) == 0) {
        return false;
    }
    if (load_u8(entity + 0xA9) == 4) {
        return false;
    }
    return true;
}

bool resolve_controller(std::uintptr_t& controller, std::string& error)
{
    if (!readable(kSelectionGlobal, sizeof(std::uint32_t))) {
        error = "TARGET: selection global not readable";
        return false;
    }
    const std::uintptr_t holder = load_u32(kSelectionGlobal);
    if (!readable(holder, sizeof(std::uint32_t))) {
        error = "TARGET: selection holder not readable";
        return false;
    }
    controller = load_u32(holder);
    if (!readable(controller, 0x60)) {
        error = "TARGET: selection controller not readable";
        return false;
    }
    return true;
}

bool selector_ok()
{
    return !IsBadReadPtr(reinterpret_cast<const void*>(kSelector),
                         sizeof(kSelectorSignature)) &&
           std::memcmp(reinterpret_cast<const void*>(kSelector),
                       kSelectorSignature, sizeof(kSelectorSignature)) == 0;
}

}  // namespace

bool client_target(unsigned long entity_id, unsigned long& entity_out,
                   unsigned long& controller_out, std::string& error)
{
    if (entity_id == 0) {
        error = "TARGET: invalid entity id";
        return false;
    }
    if (!selector_ok()) {
        error = "TARGET: client build mismatch";
        return false;
    }

    std::uintptr_t module_begin = 0;
    std::uintptr_t module_end = 0;
    if (!module_range(module_begin, module_end)) {
        error = "TARGET: main module not readable";
        return false;
    }

    const std::uint32_t id = static_cast<std::uint32_t>(entity_id);
    std::uintptr_t entity = 0;

    for (const CacheEntry& entry : g_cache) {
        if (entry.id == id &&
            looks_like_entity(entry.entity, id, module_begin, module_end)) {
            entity = entry.entity;
            break;
        }
    }

    if (entity == 0) {
        // The id is stored at entity+8, so the scan hit is entity+8. Monster
        // ids also appear in maps and tables; only a live object with an
        // in-module vtable and the expected layout is accepted.
        const std::vector<std::uintptr_t> hits = Memory::ScanValue(id, 64);
        for (std::uintptr_t hit : hits) {
            if (hit < 8) {
                continue;
            }
            const std::uintptr_t candidate = hit - 8;
            if (looks_like_entity(candidate, id, module_begin, module_end)) {
                entity = candidate;
                break;
            }
        }
    }

    if (entity == 0) {
        error = "TARGET: entity not found in client memory";
        return false;
    }

    std::uintptr_t controller = 0;
    if (!resolve_controller(controller, error)) {
        return false;
    }

    g_cache[g_cache_next % kCacheSize] = {id, 0, entity};
    ++g_cache_next;

    ntpl_client_target(reinterpret_cast<void*>(controller),
                       reinterpret_cast<void*>(entity), kSelector);

    // The selector only keeps an entity that passed its own checks; a
    // different value here means the client refused it (dead, off-map, ...).
    if (load_u32(controller + kControllerTarget) !=
        static_cast<std::uint32_t>(entity)) {
        error = "TARGET: client refused the selection";
        return false;
    }

    entity_out = static_cast<unsigned long>(entity);
    controller_out = static_cast<unsigned long>(controller);
    return true;
}
