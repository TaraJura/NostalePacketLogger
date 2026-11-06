/*
 * Call the game's own MoveTo(PlayerObject *this, packedXY, flags, run).
 *
 * EAX = this, EDX = packed x/y, ECX = flags, one 32-bit stack argument.
 * The callee ends with `ret 4`, which consumes the pushed flag.
 */
    .text
    .globl _ntpl_client_move
_ntpl_client_move:
    pushl %ebp
    movl %esp, %ebp
    pushl %ebx
    pushl %esi
    pushl %edi

    movl 8(%ebp), %eax
    movl 12(%ebp), %edx
    movl 16(%ebp), %ebx
    xorl %ecx, %ecx
    pushl $0
    call *%ebx

    popl %edi
    popl %esi
    popl %ebx
    leave
    ret
