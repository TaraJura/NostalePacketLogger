// Host-side unit test for the DLL's speed-boost cond parsing.
//
// `make speed-test` builds and runs this on Linux; it needs neither Wine nor
// the game. The DLL only reads the packet and never rewrites it, so these are
// the parsing rules that keep the boost tied to the server's real speed.

#include <cstdio>

#include "speedboost.h"

namespace {

int g_failures = 0;

void check(bool condition, const char* what)
{
    if (!condition) {
        std::fprintf(stderr, "FAIL: %s\n", what);
        ++g_failures;
    }
}

void test_target_speed()
{
    check(speedboost::target_speed(12, 0) == 12, "0 leaves the speed");
    check(speedboost::target_speed(12, 1) == 13, "+1 adds one");
    check(speedboost::target_speed(12, 3) == 15, "+3 adds three");
    check(speedboost::target_speed(11, 2) == 13, "+2 on 11");
    check(speedboost::target_speed(10, 5) == 15, "+5 on 10");
    check(speedboost::target_speed(0, 3) == 0, "no base speed, no change");
    check(speedboost::target_speed(12, -1) == 12, "negative bonus is off");
    // The client's cond setter caps at 59 (cmp dl,0x3b / ja).
    check(speedboost::target_speed(20, 100) == 59, "large bonus clamps to 59");
    check(speedboost::target_speed(58, 3) == 59, "sum clamps to 59");
    check(speedboost::target_speed(59, 1) == 59, "59 stays 59");
}

void test_parse_cond()
{
    speedboost::Cond cond =
        speedboost::parse_cond("cond 1 7349 0 0 12", 3);
    check(cond.valid, "cond is valid");
    check(cond.id == 7349, "cond id is parsed");
    check(cond.base == 12, "cond base speed is parsed");
    check(cond.target == 15, "cond target is base + 3");

    cond = speedboost::parse_cond("cond 1 7349 0 0 12", 0);
    check(cond.valid && cond.target == 12, "0 bonus keeps the base");

    // The DLL hook sees CRLF-terminated strings from the game.
    cond = speedboost::parse_cond("cond 1 7001 0 0 12\r\n", 4);
    check(cond.valid && cond.id == 7001 && cond.base == 12 &&
              cond.target == 16,
          "CRLF cond is parsed");

    // Only entity type 1 is the local player; monsters and pets use 2.
    check(!speedboost::parse_cond("cond 2 209552 0 0 12", 3).valid,
          "cond type 2 is ignored");
    check(!speedboost::parse_cond("walk 25 117 0 12", 3).valid,
          "walk is not a cond");

    // The speed must be the last of six fields.
    check(!speedboost::parse_cond("cond 1 7349 0 0 12 7", 3).valid,
          "a cond with an extra field is ignored");
    check(!speedboost::parse_cond("cond 1 7349 0 12", 3).valid,
          "a short cond is ignored");
    check(!speedboost::parse_cond("cond  1 7349 0 0 12", 3).valid,
          "double space is not a valid packet shape");
    check(!speedboost::parse_cond("cond 1 7349 0 0 abc", 3).valid,
          "non-numeric speed is ignored");

    // The target is clamped at the client's walking-speed limit.
    cond = speedboost::parse_cond("cond 1 7 0 0 58", 3);
    check(cond.valid && cond.target == 59, "58 + 3 clamps to 59");

    // The parser does not care about packet field width; the entity setter
    // handles a one-digit server speed fine.
    cond = speedboost::parse_cond("cond 1 7 0 0 9", 1);
    check(cond.valid && cond.target == 10, "9 + 1 targets 10");
}

}  // namespace

int main()
{
    test_target_speed();
    test_parse_cond();
    if (g_failures != 0) {
        std::fprintf(stderr, "%d speed-boost test(s) failed\n", g_failures);
        return 1;
    }
    std::printf("speed_boost_test: all checks passed\n");
    return 0;
}
