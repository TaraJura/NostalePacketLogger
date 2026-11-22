// Host-side unit test for the DLL's `KEY|<name>` parsing.
//
// `make key-test` builds and runs this on Linux; it needs neither Wine nor
// the game. The same header is compiled into the DLL.

#include <cstdio>
#include <string>

#include "key_names.h"

namespace {

int g_failures = 0;

void check(bool condition, const char* what)
{
    if (!condition) {
        std::fprintf(stderr, "FAIL: %s\n", what);
        ++g_failures;
    }
}

bool parse(const std::string& name, ntpl::KeyCode& code)
{
    std::string error;
    return ntpl::parse_key_name(name, code, error);
}

void test_letters_and_digits()
{
    ntpl::KeyCode code = {};
    check(parse("y", code), "y parses");
    check(code.vk == 'Y', "y maps to VK_Y");
    check(!code.extended, "y is not an extended key");

    check(parse(" Y ", code), "y with spaces parses");
    check(code.vk == 'Y', "trimmed y maps to VK_Y");

    check(parse("7", code), "7 parses");
    check(code.vk == '7', "7 maps to VK_7");
    check(!code.extended, "7 is not extended");
}

void test_named_keys()
{
    ntpl::KeyCode code = {};
    check(parse("space", code) && code.vk == 0x20, "space");
    check(parse("spacebar", code) && code.vk == 0x20, "spacebar alias");
    check(parse("enter", code) && code.vk == 0x0D, "enter");
    check(parse("return", code) && code.vk == 0x0D, "return alias");
    check(parse("ESC", code) && code.vk == 0x1B, "esc");
    check(parse("escape", code) && code.vk == 0x1B, "escape alias");
    check(parse("ctrl", code) && code.vk == 0x11, "ctrl");
    check(parse("control", code) && code.vk == 0x11, "control alias");

    check(parse("f1", code) && code.vk == 0x70, "f1");
    check(parse("F12", code) && code.vk == 0x7B, "f12");
    check(parse("pageup", code) && code.vk == 0x21 && code.extended,
          "pageup is extended");
    check(parse("down", code) && code.vk == 0x28 && code.extended,
          "down is extended");
    check(parse("delete", code) && code.vk == 0x2E && code.extended,
          "delete is extended");
}

void test_rejects()
{
    ntpl::KeyCode code = {};
    check(!parse("", code), "empty");
    check(!parse("   ", code), "spaces");
    check(!parse("yy", code), "two letters");
    check(!parse("!", code), "punctuation");
    check(!parse("f0", code), "f0");
    check(!parse("f13", code), "f13");
    check(!parse("f1x", code), "f1x");
    check(!parse("page up", code), "space in a name");
}

}  // namespace

int main()
{
    test_letters_and_digits();
    test_named_keys();
    test_rejects();
    if (g_failures != 0) {
        std::fprintf(stderr, "%d key-name test(s) failed\n", g_failures);
        return 1;
    }
    std::printf("key names: all checks passed\n");
    return 0;
}
