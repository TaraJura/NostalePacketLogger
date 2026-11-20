// Host-side unit test for the DLL's speed-boost packet rewriting.
//
// `make speed-test` builds and runs this on Linux; it needs neither Wine nor
// the game. The rewriting is deliberately pure (speedboost.h) because it
// writes into a live client's AnsiString buffer, so the field-width and
// idempotence rules are worth pinning down.

#include <cstdio>
#include <cstring>

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

void expect_packet(const char* before, const char* after)
{
    if (std::strcmp(before, after) != 0) {
        std::fprintf(stderr, "FAIL: expected \"%s\", got \"%s\"\n", after,
                     before);
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

void test_cond_patch()
{
    const char* cond = "cond 1 7349 0 0 12";
    speedboost::Change change = speedboost::plan_cond(cond, 3);
    check(change.valid, "cond is valid");
    check(change.id == 7349, "cond id is parsed");
    check(change.from == 12 && change.to == 15, "cond speed +3");
    check(change.rewrite, "15 fits the two-digit field");

    char buffer[64] = {0};
    std::strcpy(buffer, cond);
    check(speedboost::apply(buffer, change), "cond rewrite applies");
    expect_packet(buffer, "cond 1 7349 0 0 15");

    change = speedboost::plan_cond(cond, 0);
    check(change.valid && !change.rewrite, "0 bonus never rewrites");
    std::strcpy(buffer, cond);
    check(!speedboost::apply(buffer, change), "0 bonus apply is a no-op");
    expect_packet(buffer, cond);

    // A walk packet is not a cond packet.
    check(!speedboost::plan_cond("walk 25 117 0 12", 3).valid,
          "walk is not a cond");

    // Only entity type 1 is the local player; monsters and pets use 2.
    check(!speedboost::plan_cond("cond 2 209552 0 0 12", 3).valid,
          "cond type 2 is ignored");

    // The speed must be the last of six fields.
    check(!speedboost::plan_cond("cond 1 7349 0 0 12 7", 3).valid,
          "a cond with an extra field is ignored");
    check(!speedboost::plan_cond("cond 1 7349 0 12", 3).valid,
          "a short cond is ignored");
    check(!speedboost::plan_cond("cond  1 7349 0 0 12", 3).valid,
          "double space is not a valid packet shape");
    check(!speedboost::plan_cond("cond 1 7349 0 0 abc", 3).valid,
          "non-numeric speed is ignored");

    // A one-digit speed cannot grow to two digits in place.
    change = speedboost::plan_cond("cond 1 7 0 0 9", 1);
    check(change.valid && !change.rewrite,
          "9 -> 10 is refused (field width)");
    char narrow[32] = {0};
    std::strcpy(narrow, "cond 1 7 0 0 9");
    check(!speedboost::apply(narrow, change), "narrow apply is a no-op");
    expect_packet(narrow, "cond 1 7 0 0 9");

    // The client cap still leaves a two-digit field.
    change = speedboost::plan_cond("cond 1 7 0 0 58", 3);
    check(change.valid && change.rewrite && change.to == 59,
          "58 + 3 clamps to 59");
    std::strcpy(buffer, "cond 1 7 0 0 58");
    check(speedboost::apply(buffer, change), "clamped cond applies");
    expect_packet(buffer, "cond 1 7 0 0 59");

    // The DLL hook sees CRLF-terminated strings from the game. The line
    // terminator must not become part of the speed token.
    change = speedboost::plan_cond("cond 1 7349 0 0 12\r\n", 3);
    check(change.valid && change.rewrite, "CRLF cond is parsed and rewritten");
    char crlf[64] = {0};
    std::strcpy(crlf, "cond 1 7349 0 0 12\r\n");
    check(speedboost::apply(crlf, change), "CRLF cond rewrite applies");
    expect_packet(crlf, "cond 1 7349 0 0 15\r\n");
}

void test_walk_patch()
{
    char buffer[64] = {0};

    // The client still sends the server's speed: patch it.
    speedboost::Change change =
        speedboost::plan_walk("walk 25 117 0 12", 12, 15);
    check(change.valid && change.rewrite, "walk with base speed is patched");
    std::strcpy(buffer, "walk 25 117 0 12");
    check(speedboost::apply(buffer, change), "walk rewrite applies");
    expect_packet(buffer, "walk 25 117 0 15");

    // The client already uses the boosted cond speed: leave it alone (a
    // second bonus would be added twice).
    change = speedboost::plan_walk("walk 25 117 0 15", 12, 15);
    check(change.valid && !change.rewrite, "already boosted walk is kept");

    // A different speed (mount, buff) is not guessed at.
    change = speedboost::plan_walk("walk 25 117 0 13", 12, 15);
    check(change.valid && !change.rewrite, "unknown walk speed is kept");

    // Width limits apply here too.
    change = speedboost::plan_walk("walk 25 117 0 9", 9, 10);
    check(change.valid && !change.rewrite, "walk 9 -> 10 is refused");

    // No cond seen yet: nothing to do.
    check(!speedboost::plan_walk("walk 25 117 0 12", 0, 0).rewrite,
          "no cond, no walk rewrite");

    check(!speedboost::plan_walk("mv 1 7349 25 117 12", 12, 15).valid,
          "mv is not a walk");

    // The checksum field is untouched.
    std::strcpy(buffer, "walk 22 123 1 11");
    change = speedboost::plan_walk(buffer, 11, 13);
    check(speedboost::apply(buffer, change), "walk 11 -> 13 applies");
    expect_packet(buffer, "walk 22 123 1 13");

    // CRLF-terminated walk packets (what the send hook actually sees).
    std::strcpy(buffer, "walk 25 117 0 12\r\n");
    change = speedboost::plan_walk(buffer, 12, 15);
    check(change.rewrite, "CRLF walk is parsed");
    check(speedboost::apply(buffer, change), "CRLF walk rewrite applies");
    expect_packet(buffer, "walk 25 117 0 15\r\n");
}

}  // namespace

int main()
{
    test_target_speed();
    test_cond_patch();
    test_walk_patch();
    if (g_failures != 0) {
        std::fprintf(stderr, "%d speed-boost test(s) failed\n", g_failures);
        return 1;
    }
    std::printf("speed_boost_test: all checks passed\n");
    return 0;
}
