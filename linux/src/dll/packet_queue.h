#pragma once

// Fixed-capacity ring buffer shared between the in-game hooks (producers) and
// the worker thread (consumer).
//
// Why not std::queue<std::string>: the hooks run inside the game's own network
// and logic threads. They must never allocate, block for long, or throw. Every
// slot here is allocated once, before the first hook is installed.
//
// Why a spinlock instead of a lock-free SPSC ring: the recv hook and the send
// hook can fire on two different game threads at the same time, so there are
// two producers. The critical section is a bounded memcpy into a preallocated
// slot and is never held across a call into the game. A dropped packet is
// counted, never waited for: the client's network thread always wins.

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string>

#if defined(__GNUC__) || defined(__clang__)
#define NTPL_CPU_RELAX() __builtin_ia32_pause()
#else
#include <windows.h>
#define NTPL_CPU_RELAX() YieldProcessor()
#endif

namespace ntpl {

template <std::size_t Capacity = 512, std::size_t SlotSize = 2048>
class PacketQueue {
    static_assert(Capacity > 0, "Capacity must be positive");
    static_assert(SlotSize >= 2, "SlotSize must hold at least one byte and NUL");

public:
    PacketQueue()
        : lock_(ATOMIC_FLAG_INIT), head_(0), tail_(0), dropped_(0) {}

    PacketQueue(const PacketQueue&) = delete;
    PacketQueue& operator=(const PacketQueue&) = delete;

    // Producer side. Returns false when the packet was dropped (queue full or
    // too long). Never blocks for more than the consumer's slot copy.
    bool push(const char* text, std::size_t len) {
        if (text == nullptr) {
            return false;
        }
        if (len >= SlotSize) {
            len = SlotSize - 1;
        }

        lock();
        const std::uint32_t head = head_.load(std::memory_order_relaxed);
        const std::uint32_t tail = tail_.load(std::memory_order_acquire);

        if (head - tail >= Capacity) {
            dropped_.fetch_add(1, std::memory_order_relaxed);
            unlock();
            return false;
        }

        Slot& slot = slots_[head % Capacity];
        std::memcpy(slot.data, text, len);
        slot.data[len] = '\0';
        slot.len = static_cast<std::uint32_t>(len);

        head_.store(head + 1, std::memory_order_release);
        unlock();
        return true;
    }

    bool push(const char* text) {
        if (text == nullptr) {
            return false;
        }
        std::size_t len = 0;
        while (len < SlotSize - 1 && text[len] != '\0') {
            ++len;
        }
        return push(text, len);
    }

    // Consumer side. Copies the next packet into `out`. The std::string
    // allocation happens after the lock is released, so a producer never
    // waits on the allocator.
    bool pop(std::string& out) {
        char tmp[SlotSize];
        std::uint32_t len = 0;

        lock();
        const std::uint32_t tail = tail_.load(std::memory_order_relaxed);
        const std::uint32_t head = head_.load(std::memory_order_acquire);

        if (tail == head) {
            unlock();
            return false;
        }

        Slot& slot = slots_[tail % Capacity];
        len = slot.len;
        if (len >= SlotSize) {
            len = SlotSize - 1;
        }
        std::memcpy(tmp, slot.data, len);
        tmp[len] = '\0';

        tail_.store(tail + 1, std::memory_order_release);
        unlock();

        out.assign(tmp, len);
        return true;
    }

    std::uint32_t dropped() const {
        return dropped_.load(std::memory_order_relaxed);
    }

    std::uint32_t size() const {
        const std::uint32_t head = head_.load(std::memory_order_acquire);
        const std::uint32_t tail = tail_.load(std::memory_order_acquire);
        return head - tail;
    }

private:
    struct Slot {
        std::uint32_t len;
        char          data[SlotSize];
    };

    void lock() {
        while (lock_.test_and_set(std::memory_order_acquire)) {
            NTPL_CPU_RELAX();
        }
    }

    void unlock() {
        lock_.clear(std::memory_order_release);
    }

    std::atomic_flag          lock_;
    Slot                      slots_[Capacity];
    std::atomic<std::uint32_t> head_;
    std::atomic<std::uint32_t> tail_;
    std::atomic<std::uint32_t> dropped_;
};

}  // namespace ntpl
