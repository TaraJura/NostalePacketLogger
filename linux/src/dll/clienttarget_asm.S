/*
 * Call the game's own entity selector (EAX = controller, EDX = entity).
 *
 * The selector takes its two arguments in registers, ends with a plain `ret`
 * and preserves EBX/ESI/EDI itself; saving them here too keeps the caller's
 * state intact even if a client patch changes that.
 */
    .text
    .globl _ntpl_client_target
_ntpl_client_target:
    pushl %ebp
    movl %esp, %ebp
    pushl %ebx
    pushl %esi
    pushl %edi

    movl 8(%ebp), %eax
    movl 12(%ebp), %edx
    movl 16(%ebp), %ebx
    call *%ebx

    popl %edi
    popl %esi
    popl %ebx
    leave
    ret
