/*
 * Test-only call wrappers: put the packet pointer in EDX exactly like the game
 * does at the hook points, then call the real detour stubs from hooks.S.
 */

    .text

    .globl _call_hook_recv
    .def   _call_hook_recv; .scl 2; .type 32; .endef
_call_hook_recv:
    pushl   %ebp
    movl    %esp, %ebp
    movl    8(%ebp), %edx
    call    _ntpl_hook_recv
    popl    %ebp
    ret

    .globl _call_hook_send
    .def   _call_hook_send; .scl 2; .type 32; .endef
_call_hook_send:
    pushl   %ebp
    movl    %esp, %ebp
    movl    8(%ebp), %edx
    call    _ntpl_hook_send
    popl    %ebp
    ret
