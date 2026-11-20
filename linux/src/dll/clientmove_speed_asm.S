/*
 * void ntpl_client_speed(void* entity, int speed, uintptr_t function)
 *
 * Calls the client's entity speed setter with the Delphi register convention
 * (EAX = entity, EDX = speed) and preserves the C caller's registers. The
 * setter may use the FPU and the stack; it has no stack arguments.
 */
    .text

    .globl _ntpl_client_speed
    .def   _ntpl_client_speed; .scl 2; .type 32; .endef
_ntpl_client_speed:
    pushl   %ebp
    movl    %esp, %ebp
    pushl   %ebx
    pushl   %esi
    pushl   %edi
    movl    8(%ebp), %eax           /* entity */
    movl    12(%ebp), %edx          /* speed */
    call    *16(%ebp)               /* function */
    popl    %edi
    popl    %esi
    popl    %ebx
    popl    %ebp
    ret
