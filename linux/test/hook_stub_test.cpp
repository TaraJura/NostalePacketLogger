// Exercises the real detour stubs from src/dll/hooks.S without the game.
//
// The stubs normally do three things: call a C callback with the packet pointer
// that the game left in EDX, replay the original instruction, and continue.
// This harness supplies the callbacks and the continuation addresses, then
// checks that both stubs pass the right string and take the right path.
//
// It is the regression test for the PUSHAD layout: EDX is at +20 on the stack,
// and getting that offset wrong passes a stack address instead of the packet.

#include <cstdint>
#include <cstdio>
#include <cstring>

extern "C" {
void ntpl_hook_recv();
void ntpl_hook_send();
void ntpl_game_send(char* packet);

void call_hook_recv(const char* packet);
void call_hook_send(const char* packet);

void ntpl_on_packet_recv(const char* packet);
void ntpl_on_packet_send(const char* packet);

extern std::uintptr_t ntpl_recv_original_call;
extern std::uintptr_t ntpl_recv_jmp_back;
extern std::uintptr_t ntpl_send_trampoline;
extern std::uintptr_t ntpl_tnt_client;
extern std::uintptr_t ntpl_send_addr;

void fake_original_recv();
void recv_done();
void fake_trampoline();
}

namespace {
char g_recv[256] = {0};
char g_send[256] = {0};
int g_recv_calls = 0;
int g_send_calls = 0;
int g_original_calls = 0;
int g_back_calls = 0;
int g_trampoline_calls = 0;
}  // namespace

extern "C" {

std::uintptr_t ntpl_recv_original_call = 0;
std::uintptr_t ntpl_recv_jmp_back = 0;
std::uintptr_t ntpl_send_trampoline = 0;
std::uintptr_t ntpl_tnt_client = 0;
std::uintptr_t ntpl_send_addr = 0;

void ntpl_on_packet_recv(const char* packet) {
    ++g_recv_calls;
    std::snprintf(g_recv, sizeof(g_recv), "%s", packet != nullptr ? packet : "(null)");
}

void ntpl_on_packet_send(const char* packet) {
    ++g_send_calls;
    std::snprintf(g_send, sizeof(g_send), "%s", packet != nullptr ? packet : "(null)");
}

// Stands in for the game's recv function. The stub calls it and then jumps to
// recv_done, which returns to call_hook_recv's caller.
void fake_original_recv() {
    ++g_original_calls;
}

void recv_done() {
    ++g_back_calls;
}

// Stands in for the trampoline. It returns to call_hook_send's caller.
void fake_trampoline() {
    ++g_trampoline_calls;
}

}  // extern "C"

int main() {
    ntpl_recv_original_call =
        reinterpret_cast<std::uintptr_t>(&fake_original_recv);
    ntpl_recv_jmp_back = reinterpret_cast<std::uintptr_t>(&recv_done);
    ntpl_send_trampoline = reinterpret_cast<std::uintptr_t>(&fake_trampoline);

    call_hook_recv("walk 19 146 0 10");
    call_hook_send("u_s 1 2");

    const bool recv_ok =
        g_recv_calls == 1 && std::strcmp(g_recv, "walk 19 146 0 10") == 0;
    const bool send_ok =
        g_send_calls == 1 && std::strcmp(g_send, "u_s 1 2") == 0;
    const bool replay_ok = g_original_calls == 1;
    const bool back_ok = g_back_calls == 1;
    const bool trampoline_ok = g_trampoline_calls == 1;

    const bool ok = recv_ok && send_ok && replay_ok && back_ok && trampoline_ok;

    std::printf(
        "hook stub test: %s\n"
        "  recv callback: %d call(s), text=%s\n"
        "  send callback: %d call(s), text=%s\n"
        "  original call replayed: %d\n"
        "  recv continuation: %d\n"
        "  send trampoline: %d\n",
        ok ? "PASS" : "FAIL", g_recv_calls, g_recv, g_send_calls, g_send,
        g_original_calls, g_back_calls, g_trampoline_calls);

    return ok ? 0 : 1;
}
