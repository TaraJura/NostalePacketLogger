// Host-side unit test for the DLL's speed-boost cond parsing.
//
// `make speed-test` builds and runs this on Linux; it needs neither Wine nor
// the game. The DLL only reads the packet and never rewrites it, so these are
// the parsing rules that keep the boost tied to the server's real speed.

#include <cstdio>
#include <cstring>

#include "castspeed.h"
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
    // The host spin has no 59 cap any more; the DLL lifts the client's own
    // limit and extends its speed table instead.
    check(speedboost::target_speed(20, 100) == 120, "large bonus is not capped at 59");
    check(speedboost::target_speed(58, 3) == 61, "sum past 59 is kept");
    check(speedboost::target_speed(59, 1) == 60, "59 + 1 is 60");
    check(speedboost::target_speed(59, speedboost::kMaxSpeed) ==
              speedboost::kMaxSpeed,
          "the emulated speed has an int bound");
}

void test_clamp_walk()
{
    char buffer[64] = {0};

    std::strcpy(buffer, "walk 25 117 0 16");
    check(speedboost::clamp_walk(buffer, 12), "walk 16 clamps to 12");
    check(std::strcmp(buffer, "walk 25 117 0 12") == 0,
          "clamped walk text");

    std::strcpy(buffer, "walk 25 117 0 12");
    check(!speedboost::clamp_walk(buffer, 12), "walk at base is unchanged");

    std::strcpy(buffer, "walk 25 117 0 16\r\n");
    check(speedboost::clamp_walk(buffer, 12), "CRLF walk clamps");
    check(std::strcmp(buffer, "walk 25 117 0 12\r\n") == 0,
          "clamped CRLF walk text");

    // Width must not change in place; a shorter base is zero-padded so the
    // client's AnsiString length stays valid.
    std::strcpy(buffer, "walk 25 117 0 10");
    check(speedboost::clamp_walk(buffer, 9), "10 -> 9 is zero-padded");
    check(std::strcmp(buffer, "walk 25 117 0 09") == 0,
          "padded one-digit base");

    // Speeds above 59 use a three-digit field; the clamped value pads to it.
    std::strcpy(buffer, "walk 25 117 0 255");
    check(speedboost::clamp_walk(buffer, 12), "255 clamps to 012");
    check(std::strcmp(buffer, "walk 25 117 0 012") == 0,
          "three-digit speed padded to the base width");

    std::strcpy(buffer, "mv 1 7349 25 117 16");
    check(!speedboost::clamp_walk(buffer, 12), "mv is not a walk");
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

    // The target follows the extended speed, not the client's old 59 cap.
    cond = speedboost::parse_cond("cond 1 7 0 0 58", 3);
    check(cond.valid && cond.target == 61, "58 + 3 targets 61");

    // The parser does not care about packet field width; the entity setter
    // handles a one-digit server speed fine.
    cond = speedboost::parse_cond("cond 1 7 0 0 9", 1);
    check(cond.valid && cond.target == 10, "9 + 1 targets 10");
}

void test_cast_speed_zero()
{
    char buffer[80] = {0};

    // su <op> <type> <id> <type> <id> <vnum> <cooldown> ... -- the cooldown
    // is raw field 6 ("su" itself is field 0). Cast speed zeroes it.
    std::strcpy(buffer, "su 1 7349 3 7020 838 300 40 4209");
    check(castspeed::zero_field(buffer, 6), "300 ds zeroes");
    check(std::strcmp(buffer, "su 1 7349 3 7020 838 000 40 4209") == 0,
          "zeroed cooldown keeps the field width");

    std::strcpy(buffer, "su 1 7349 3 7020 838 900");
    check(castspeed::zero_field(buffer, 6), "900 ds zeroes");
    check(std::strcmp(buffer, "su 1 7349 3 7020 838 000") == 0,
          "last field zeroes");

    std::strcpy(buffer, "su 1 1 3 2 866 9");
    check(castspeed::zero_field(buffer, 6), "one digit zeroes");
    check(std::strcmp(buffer, "su 1 1 3 2 866 0") == 0, "9 -> 0");

    // Malformed shapes are left alone.
    std::strcpy(buffer, "su 1 1 3 2 866 9");
    check(!castspeed::zero_field(buffer, 9), "missing field ignored");
    check(std::strcmp(buffer, "su 1 1 3 2 866 9") == 0,
          "missing field leaves the packet unchanged");

    std::strcpy(buffer, "su 1 1 3 2 866 abc");
    check(!castspeed::zero_field(buffer, 6), "non-number ignored");
    check(std::strcmp(buffer, "su 1 1 3 2 866 abc") == 0,
          "non-number text unchanged");

    std::strcpy(buffer, "su 1 1 3 2 866 -5");
    check(!castspeed::zero_field(buffer, 6), "negative ignored");
    check(std::strcmp(buffer, "su 1 1 3 2 866 -5") == 0,
          "negative text unchanged");

    // 65535 is the protocol's "no cooldown" sentinel; leave it alone.
    std::strcpy(buffer, "su 1 1 3 2 866 65535");
    check(!castspeed::zero_field(buffer, 6), "0xffff is left alone");
    check(std::strcmp(buffer, "su 1 1 3 2 866 65535") == 0,
          "0xffff text unchanged");

    // The `sr -10` duration is raw field 3.
    std::strcpy(buffer, "sr -10 876 900 0");
    check(castspeed::zero_field(buffer, 3), "sr duration zeroes");
    check(std::strcmp(buffer, "sr -10 876 000 0") == 0,
          "sr duration text");
}

}  // namespace

int main()
{
    test_target_speed();
    test_clamp_walk();
    test_parse_cond();
    test_cast_speed_zero();
    if (g_failures != 0) {
        std::fprintf(stderr, "%d speed-boost test(s) failed\n", g_failures);
        return 1;
    }
    std::printf("speed_boost_test: all checks passed\n");
    return 0;
}
