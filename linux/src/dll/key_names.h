#pragma once

// Pure key-name parsing, shared by the DLL and the host-compiled key test.
// A key is one ASCII letter/digit or one of the named game keys; the host
// sends the canonical spelling over `KEY|<name>`.

#include <cctype>
#include <cstddef>
#include <string>

namespace ntpl {

struct KeyCode {
    unsigned int vk;
    unsigned int scan;  // 0 = let the Windows side fill it in
    bool extended;
};

inline std::string key_trim_lower(const std::string& input)
{
    std::size_t begin = 0;
    std::size_t end = input.size();
    while (begin < end &&
           std::isspace(static_cast<unsigned char>(input[begin])) != 0) {
        ++begin;
    }
    while (end > begin &&
           std::isspace(static_cast<unsigned char>(input[end - 1])) != 0) {
        --end;
    }
    std::string text = input.substr(begin, end - begin);
    for (char& character : text) {
        character = static_cast<char>(
            std::tolower(static_cast<unsigned char>(character)));
    }
    return text;
}

inline bool parse_key_name(const std::string& input, KeyCode& code,
                           std::string& error)
{
    const std::string text = key_trim_lower(input);
    if (text.empty()) {
        error = "empty key";
        return false;
    }

    if (text.size() == 1 &&
        std::isalnum(static_cast<unsigned char>(text[0])) != 0) {
        code.vk = static_cast<unsigned int>(
            std::toupper(static_cast<unsigned char>(text[0])));
        code.scan = 0;
        code.extended = false;
        return true;
    }

    struct Named {
        const char* name;
        unsigned int vk;
        bool extended;
    };
    // VK values from winuser.h, repeated here so this header stays usable by
    // the native test that has no Windows headers.
    static const Named named[] = {
        {"space", 0x20, false},   {"spacebar", 0x20, false},
        {"enter", 0x0D, false},
        {"return", 0x0D, false},  {"tab", 0x09, false},
        {"esc", 0x1B, false},     {"escape", 0x1B, false},
        {"backspace", 0x08, false}, {"shift", 0x10, false},
        {"ctrl", 0x11, false},    {"control", 0x11, false},
        {"alt", 0x12, false},     {"up", 0x26, true},
        {"down", 0x28, true},     {"left", 0x25, true},
        {"right", 0x27, true},    {"home", 0x24, true},
        {"end", 0x23, true},      {"insert", 0x2D, true},
        {"delete", 0x2E, true},   {"pageup", 0x21, true},
        {"pagedown", 0x22, true},
    };
    for (const Named& item : named) {
        if (text == item.name) {
            code.vk = item.vk;
            code.scan = 0;
            code.extended = item.extended;
            return true;
        }
    }

    if (text.size() >= 2 && text[0] == 'f') {
        const int number = (text.size() == 2) ? (text[1] - '0')
                          : (text.size() == 3)
                              ? (text[1] - '0') * 10 + (text[2] - '0')
                              : -1;
        if (text.size() <= 3 && number >= 1 && number <= 12) {
            code.vk = 0x70U + static_cast<unsigned int>(number - 1);
            code.scan = 0;
            code.extended = false;
            return true;
        }
    }

    error = "unknown key: " + input;
    return false;
}

}  // namespace ntpl
