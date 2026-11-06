/*
 * x86 (32-bit) hook stubs for the NosTale client.
 *
 * This file replaces the MSVC inline assembler (`__declspec(naked)` + `__asm`)
 * from the Windows build. Keeping the stubs in a real .S file means the code
 * is the same whether the DLL is assembled by MinGW on Linux or by clang on
 * Windows, and it removes the MSVC-only syntax from the C++ sources.
 *
 * Calling convention notes:
 *   - The game is a 32-bit Delphi application. At the recv hook point EDX
 *     holds the packet pointer; at the send hook point EDX holds it too.
 *   - pushal/popal preserve every general-purpose register, so the game sees
 *     no side effects from the detour.
 *   - The stack is realigned before the C call because the game only promises
 *     4-byte alignment at its call sites.
 *   - `call *_symbol` is an indirect call through the 4-byte variable at that
 *     symbol; the C++ side fills those variables at runtime.
 */

    .text

/* ------------------------------------------------------------------ recv --
 * The original instruction at the hook address is a 5-byte `call rel32`.
 * We replay that call and then jump to the instruction after it.
 */
    .globl _ntpl_hook_recv
    .def   _ntpl_hook_recv; .scl 2; .type 32; .endef
_ntpl_hook_recv:
    pushfl
    pushal
    movl    %esp, %ebp              /* ebp = post-pushal esp */
    andl    $-16, %esp              /* 16-byte alignment ... */
    subl    $12, %esp               /* ... plus 12 = aligned call site */
/*
 * PUSHAL layout at the post-pushal ESP (Intel order: EAX ECX EDX EBX ESP EBP
 * ESI EDI, stack growing down):
 *   +0 EDI  +4 ESI  +8 EBP  +12 ESP  +16 EBX  +20 EDX  +24 ECX  +28 EAX
 * The packet pointer is in EDX, therefore +20, not +8.
 */
    pushl   20(%ebp)                /* saved EDX = packet pointer */
    call    _ntpl_on_packet_recv
    movl    %ebp, %esp
    popal
    popfl
    call    *_ntpl_recv_original_call
    jmp     *_ntpl_recv_jmp_back

/* ------------------------------------------------------------------ send --
 * The game's send function is hooked at its first instruction:
 *   53          push ebx
 *   56          push esi
 *   8B F2       mov esi, edx
 *   8B D8       mov ebx, eax
 * Those six bytes are copied into a trampoline; after our detour we jump to
 * the trampoline, which replays them and returns to the instruction after.
 */
    .globl _ntpl_hook_send
    .def   _ntpl_hook_send; .scl 2; .type 32; .endef
_ntpl_hook_send:
    pushfl
    pushal
    movl    %esp, %ebp
    andl    $-16, %esp
    subl    $12, %esp
    pushl   20(%ebp)                /* saved EDX, see the layout note above */
    call    _ntpl_on_packet_send
    movl    %ebp, %esp
    popal
    popfl
    jmp     *_ntpl_send_trampoline

/* --------------------------------------------------------------- skill ---
 * The client's skill packet builder calls its lower-level send function at
 * 0x72D5B2 with the finished `u_s ...` string in ECX. Hooking that call site
 * captures the client's own skill packet (the normal send hook is a different
 * wrapper and does not see it). Replay the original call, then return to the
 * instruction after the call site.
 */
    .globl _ntpl_hook_skill
    .def   _ntpl_hook_skill; .scl 2; .type 32; .endef
_ntpl_hook_skill:
    pushfl
    pushal
    movl    %esp, %ebp
    andl    $-16, %esp
    subl    $12, %esp
    pushl   24(%ebp)                /* saved ECX = packet pointer */
    call    _ntpl_on_packet_send
    movl    %ebp, %esp
    popal
    popfl
    call    *_ntpl_skill_original_call
    /*
     * The call site was replaced with a JMP, not a CALL, so the original
     * return address is not on the stack. Replay the call and then jump to
     * the instruction after the call site explicitly.
     */
    jmp     *_ntpl_skill_jmp_back

/* ------------------------------------------------------------ game send --
 * void ntpl_game_send(char* packet)
 *
 * Replicates the original inline assembly:
 *   mov eax, [TNTClient]
 *   mov eax, [eax]
 *   mov eax, [eax]
 *   mov eax, [eax]
 *   mov edx, packet
 *   call SendAddy
 */
    .globl _ntpl_game_send
    .def   _ntpl_game_send; .scl 2; .type 32; .endef
_ntpl_game_send:
    pushl   %ebp
    movl    %esp, %ebp
    pushl   %ebx
    pushl   %esi
    pushl   %edi
    movl    8(%ebp), %edx           /* packet */
    movl    _ntpl_tnt_client, %eax
    movl    (%eax), %eax
    movl    (%eax), %eax
    movl    (%eax), %eax
    call    *_ntpl_send_addr
    popl    %edi
    popl    %esi
    popl    %ebx
    popl    %ebp
    ret
