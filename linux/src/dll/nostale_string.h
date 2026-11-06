#pragma once

// Delphi/C++Builder "long string" layout used by the NosTale client:
//
//   +0x00  int32   reference count
//   +0x04  int32   byte length
//   +0x08  char[]  payload, NUL terminated
//
// `get()` returns the payload pointer the game's send function expects. The
// original Windows implementation is kept byte-for-byte identical here; see
// docs/hooking.md for the ownership caveat that comes with it.
//
// Original credit: atom0s (http://atom0s.com/forums/viewtopic.php?f=21&t=151).

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <cwchar>
#include <string>

struct NostaleStringA {
    char*   m_Buffer;
    size_t  m_Length;

    NostaleStringA()
        : m_Buffer(nullptr), m_Length(0) {}

    explicit NostaleStringA(const char* str)
        : m_Buffer(nullptr), m_Length(0) {
        set(str);
    }

    ~NostaleStringA() {
        delete[] m_Buffer;
        m_Buffer = nullptr;
    }

    NostaleStringA(const NostaleStringA&) = delete;
    NostaleStringA& operator=(const NostaleStringA&) = delete;

    size_t len() const {
        return m_Buffer != nullptr ? m_Length : 0;
    }

    char* get() {
        return m_Buffer != nullptr ? m_Buffer + 0x08 : nullptr;
    }

    void set(const char* str) {
        delete[] m_Buffer;
        m_Buffer = nullptr;

        if (str == nullptr) {
            m_Length = 0;
            return;
        }

        m_Length = std::strlen(str);
        m_Buffer = new char[m_Length + 8 + 1];

        std::uint32_t* header = reinterpret_cast<std::uint32_t*>(m_Buffer);
        header[0] = 1;                                   // refcount
        header[1] = static_cast<std::uint32_t>(m_Length);  // byte length
        std::memcpy(m_Buffer + 0x08, str, m_Length);
        m_Buffer[m_Length + 0x08] = '\0';
    }
};

struct NostaleStringW {
    wchar_t* m_Buffer;
    size_t   m_Length;

    NostaleStringW()
        : m_Buffer(nullptr), m_Length(0) {}

    explicit NostaleStringW(const wchar_t* str)
        : m_Buffer(nullptr), m_Length(0) {
        set(str);
    }

    ~NostaleStringW() {
        delete[] m_Buffer;
        m_Buffer = nullptr;
    }

    NostaleStringW(const NostaleStringW&) = delete;
    NostaleStringW& operator=(const NostaleStringW&) = delete;

    size_t len() const {
        return m_Buffer != nullptr ? m_Length : 0;
    }

    wchar_t* get() {
        return m_Buffer != nullptr ? reinterpret_cast<wchar_t*>(
                   reinterpret_cast<char*>(m_Buffer) + 0x08)
                                   : nullptr;
    }

    void set(const wchar_t* str) {
        delete[] m_Buffer;
        m_Buffer = nullptr;

        if (str == nullptr) {
            m_Length = 0;
            return;
        }

        // m_Length is a byte count, matching the original implementation.
        m_Length = std::wcslen(str) * sizeof(wchar_t);
        m_Buffer = new wchar_t[m_Length + 8 + 1];

        char* bytes = reinterpret_cast<char*>(m_Buffer);
        *reinterpret_cast<std::uint32_t*>(bytes + 0x00) = 1;
        *reinterpret_cast<std::uint32_t*>(bytes + 0x04) =
            static_cast<std::uint32_t>(m_Length);
        std::memcpy(bytes + 0x08, str, m_Length);
        *reinterpret_cast<wchar_t*>(bytes + m_Length + 0x08) = L'\0';
    }
};
