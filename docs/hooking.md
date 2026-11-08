# How the hooks work

This is the part that has to be right. Everything else can be rewritten in a
pleasant language; the two detours are x86 machine code inside a 32-bit Delphi
application.

The Linux port keeps the original's hook strategy and pattern signatures. What
changed is the **implementation** of the stubs: MSVC inline assembler became a
GAS `.S` file, so the same code can be assembled by MinGW on Linux (and by
clang on Windows if wanted).

## Pattern scanning

`Memory::FindPattern(pattern, mask)` walks the main module's image
(`GetModuleInformation` on `GetModuleHandle(NULL)`) and returns the first
address where every non-`?` byte of `mask` matches. It returns `0` when the
signature is absent — which is exactly what a client patch looks like.

Three signatures are resolved:

| Name | Signature (mask) | What it finds |
|---|---|---|
| recv | `E8 ?? ?? ?? ?? 33 C0 55 68 ?? ?? ?? ?? 64 FF ?? 64 89 ?? 8D 45 ?? 8B 55` (`x????xxxx????xx?xx?xx?xx`) | the `call rel32` that invokes the game's recv function |
| send | `EB ?? EB ?? 39 19 8B D6` (`x?x?xxxx`) | a marker 6 bytes **after** the start of the game's send function |
| TNTClient | `A1 ?? ?? ?? ?? 8B 00 E8 ?? ?? ?? ?? A1 ?? ?? ?? ?? 8B 00 33 D2 89 10` (`x????xxx????x????xxxxxx`) | a `mov eax, [addr]` whose operand is the TNTClient global |

The send hook needs the function start, so the code subtracts the six bytes of
its prologue:

```
53            push ebx
56            push esi
8B F2         mov esi, edx
8B D8         mov ebx, eax
```

The TNT signature starts at the `A1` opcode; `+1` points at the 4-byte address
operand, which is what `send_packet` dereferences.

## The recv hook

At the hook address there is a normal 5-byte `E8 rel32` call. The detour
replaces it with a 5-byte `E9 rel32` jump to `ntpl_hook_recv`. The stub must
reproduce the original call and then continue after it:

```asm
_ntpl_hook_recv:
    pushfl
    pushal
    movl    %esp, %ebp          # ebp = post-pushal esp
    andl    $-16, %esp          # align for the C call ...
    subl    $12, %esp
    pushl   20(%ebp)            # saved EDX = packet pointer
    call    _ntpl_on_packet_recv
    movl    %ebp, %esp
    popal
    popfl
    call    *_ntpl_recv_original_call
    jmp     *_ntpl_recv_jmp_back
```

- At the hook point **EDX holds the packet pointer**. `pushal` lays the saved
  registers out as `+0 EDI, +4 ESI, +8 EBP, +12 ESP, +16 EBX, +20 EDX,
  +24 ECX, +28 EAX` from the post-`pushal` ESP. After `movl %esp,%ebp` the
  pointer is therefore at `20(%ebp)`, not `8(%ebp)`. This offset is covered by
  `linux/test/hook_stub_test.cpp`; do not change it without re-running that
  test.
- `pushfl`/`popfl` make the detour invisible in the flags register.
- `andl $-16` + `subl $12` + `pushl` leaves ESP 16-byte aligned at the `call`.
  The DLL is also compiled with `-mstackrealign`, so even a compiler that
  assumes nothing about incoming alignment is safe.
- `call *_ntpl_recv_original_call` is an indirect call through the 4-byte
  variable that `initialize()` filled with `hook + 5 + rel32`. The called
  function returns to the stub.
- `jmp *_ntpl_recv_jmp_back` continues at `hook + 5`, the instruction after the
  original `E8`.

The hook is installed with `Memory::Hook(addr, &ntpl_hook_recv, 5)`:
`VirtualProtect` to RWX, write `E9 rel32`, restore protection,
`FlushInstructionCache`.

## The send hook and its trampoline

The send function is hooked at its **first instruction**, not at a call site,
and the first 6 bytes are a prologue that must still execute. The DLL copies
those 6 bytes into an executable buffer and appends a jump back to
`SendAddy + 6`:

```
sendTrampoline:
    53 56 8B F2 8B D8      # the original 6 bytes
    E9 <rel32>             # jmp SendAddy + 6
```

The detour then becomes:

```asm
_ntpl_hook_send:
    pushfl
    pushal
    movl    %esp, %ebp
    andl    $-16, %esp
    subl    $12, %esp
    pushl   20(%ebp)            # saved EDX = packet pointer
    call    _ntpl_on_packet_send
    movl    %eax, -4(%ebp)      # 1 = send, 0 = drop
    movl    %ebp, %esp
    popal                       # restores the CALLER's EBP
    popfl
    cmpl    $0, -40(%esp)       # [ebp-4]; after popal/popfl esp = ebp+36
    je      .Lsend_dropped
    jmp     *_ntpl_send_trampoline
.Lsend_dropped:
    ret                         # packet dropped; return to the caller
```

`ntpl_on_packet_send` returns whether the packet may be sent. Returning 0
skips the trampoline entirely: the function is a register-convention procedure
whose result the callers ignore, so returning to the caller is safe. This is
how **Block portals** drops `preq`. Note that the decision must be read
relative to **ESP**, not EBP: `popal` restores the caller's EBP, so
`-4(%ebp)` after `popal` is the wrong address. That bug made the first drop
attempt send the packet anyway and is covered by `HookStubTest.exe`.

The trampoline runs with the registers and flags exactly as the game had them,
executes the original prologue, and jumps back. The 6-byte length is
deliberate: it is the smallest overwrite that lands on an instruction boundary
and is at least 5 bytes (the size of `E9 rel32`). `Memory::Hook` fills the
remaining byte with `0x90` before writing the jump.

## The skill packet hook

The client's `u_s` skill builder calls a lower-level send function at
`0x4EB610`; the normal send hook is a different wrapper and does not see those
packets. To capture the client's own skill packets, the DLL hooks the 5-byte
call site inside the builder:

```
72D5A3  mov ecx, [ebp-0x14]      ; finished `u_s ...` string
72D5A7  mov eax, [0x7946A0]
72D5AD  mov edx, 0x72D640        ; "u_s"
72D5B2  call 0x4EB610            ; <-- hooked call
```

The pattern is `BA 40 D6 72 00 E8 ?? ?? ?? ?? 8B D8 84 DB 74 ?`; the call
target is decoded from the rel32 and must point inside the game image. The stub
reads the packet pointer from the saved **ECX** (`pushal` layout offset +24),
queues it through `ntpl_on_packet_send`, replays the original call and returns
to the instruction after the call site:

```asm
_ntpl_hook_skill:
    pushfl
    pushal
    movl    %esp, %ebp
    andl    $-16, %esp
    subl    $12, %esp
    pushl   24(%ebp)            # saved ECX = packet pointer
    call    _ntpl_on_packet_send
    movl    %eax, -4(%ebp)
    movl    %ebp, %esp
    popal
    popfl
    cmpl    $0, -40(%esp)
    je      .Lskill_dropped
    call    *_ntpl_skill_original_call
    jmp     *_ntpl_skill_jmp_back     # NOT `ret`: the hook is a JMP, so no
                                      # return address was pushed
.Lskill_dropped:
    movl    $1, %eax            # the builder tests this Boolean
    jmp     *_ntpl_skill_jmp_back
```

The client sends its `preq` portal request through this same builder, so the
drop is applied here too (with EAX=1 so the builder thinks the send
succeeded). Without that, **Block portals** would only stop the paths, not the
warp: the normal send hook never sees `preq`.

`Memory::Hook` replaces the call with a **JMP**; the original return address is
not on the stack. The stub must therefore replay the call and jump back to the
instruction after the call site explicitly. A `ret` here crashes the client the
next time it uses a skill.

The status line reports it as `skill=ACTIVE` (or `NOT-FOUND` after a patch).
Use this hook to learn the client's exact `u_s` field order and skill vnums
from a real manual cast; injected `u_s` packets are still logged as
`MANUAL_SEND`.

## Sending a packet into the game

`send_packet` builds a Delphi-style string (see below) and calls the game's own
send function. The original inline assembly is now `_ntpl_game_send` in
`hooks.S`:

```asm
_ntpl_game_send:
    pushl %ebp
    movl  %esp, %ebp
    pushl %ebx
    pushl %esi
    pushl %edi
    movl  8(%ebp), %edx          # packet
    movl  _ntpl_tnt_client, %eax
    movl  (%eax), %eax
    movl  (%eax), %eax
    movl  (%eax), %eax
    call  *_ntpl_send_addr
    popl  %edi
    popl  %esi
    popl  %ebx
    popl  %ebp
    ret
```

`TNTClient` is a pointer to a pointer chain; the game's send function expects
its object in EAX and the packet in EDX (Delphi register calling convention).
EBX/ESI/EDI are preserved around the call. The original does the same
dereference chain; the Linux port only adds the register saves.

**Caveat:** the game's send function is being called from the DLL worker
thread, not from the game's own thread. The original Windows build does exactly
this and it works, but it is not a documented thread-safety guarantee. Keep
manual sends infrequent; if the client becomes unstable under heavy injection,
that is the first suspect.

## The Delphi string

NosTale is a Delphi application, so the send function wants a Delphi "long
string":

```
+0x00  uint32   reference count
+0x04  uint32   byte length
+0x08  char[]   payload (NUL terminated here)
```

`NostaleStringA` (copied from the original, which credits atom0s) allocates
`length + 8 + 1` bytes, writes refcount `1`, the byte length, the payload and a
terminator. `get()` returns the payload pointer.

**Ownership caveat:** the wrapper frees its buffer with `delete[]` when it goes
out of scope right after the call. If the game also decrements the refcount to
zero and frees it with its own memory manager, that would be a double free. The
original Windows implementation has behaved this way in practice; the Linux
port keeps it byte-for-byte. If you ever see a crash immediately after a
manual send, instrument this first: either leak the buffer deliberately or make
the game take ownership, and document which one the current client expects.

## The packet queue

`PacketQueue` is a fixed-capacity ring, not `std::queue<std::string>`:

- There are **two producers** (recv thread and send thread) and one consumer.
  A single-producer/single-consumer lock-free ring would be wrong.
- A `std::mutex` inside a hook is a latency risk. The implementation uses an
  `atomic_flag` spinlock. The critical section is a bounded `memcpy` into a
  preallocated slot; it is never held across a game call.
- `pop()` copies the slot into a stack buffer under the lock and constructs the
  `std::string` **after** releasing it, so a producer never waits on the
  allocator.
- When the ring is full the newest packet is dropped and `drops` increments.
  The game thread never waits.

The original Windows build pushed the raw `char*` and read it later, which is a
use-after-free if the game reuses the buffer. The port copies inside the hook.

## Install and removal

`initialize()` only resolves addresses and prepares the trampoline; it does not
touch the game's code. `install_hooks()` performs the two patches and records
their state.

Removal is the delicate part:

1. `set_enabled(false)` — the stubs still run, but they no longer enqueue.
2. `Sleep(100)` — let a packet that is already inside a stub leave it.
3. Restore the saved bytes with `Memory::Patch`.
4. Free the trampoline.

There is still a theoretical race: a game thread could enter the stub between
step 3 and its own `jmp` to the trampoline. The 100 ms drain makes it very
unlikely, and the original has the same property. Disconnect while the client
is idle (character select, not mid-fight) if you want to be careful.

## Maintaining the signatures after a client patch

A game update changes the binary; one of the three signatures will eventually
stop matching. `STATUS` will say `NOT-FOUND` and the DLL will keep running
without hooks.

Procedure:

1. Inject, run `ntpl status`. Note which address is zero.
2. Open the current `NostaleClientX.exe` in a disassembler (Ghidra, IDA,
   `objdump -D -b pei-i386`, or `diag/refs.py` in the Gameforge tree for a
   similar trick).
3. Find the same code shape:
   - recv: a `call` followed by `xor eax,eax; push ebp; push imm32; fs:[...]`
   - send: the `push ebx; push esi; mov esi,edx; mov ebx,eax` prologue, with
     the marker that follows it
   - TNT: the `mov eax,[imm32]; mov eax,[eax]; call ...` chain
4. Update the pattern bytes and mask in `linux/src/dll/packetlogger.cpp` (and
   the Windows copy if it is still maintained).
5. If the send prologue is no longer 6 bytes, update `SEND_HOOK_SIZE` in both
   the C++ and the assembly. It must be `>= 5` and end on an instruction
   boundary.
6. Rebuild, inject, verify `recv`/`send` are non-zero and both hooks are
   `ACTIVE`.
7. Capture a few known actions and confirm the packet text is sensible.

The pattern-scan failure path is intentionally non-fatal: a stale signature
must never take the game down.

## Limitations

- **x86 only.** The target is a 32-bit Delphi application; the stubs are
  32-bit. Do not try to build the DLL for x64.
- **Patterns are build-specific.** There is no version detection; a patched
  client needs new signatures.
- **No anti-cheat work.** The DLL does not hide itself, spoof signatures,
  bypass integrity checks, or evade detection. Gameforge's terms prohibit
  automation; use on a live account is the owner's decision and risk.
- **The hook is not free.** Every packet does a bounded copy under a spinlock.
  At NosTale's packet rates this is negligible; a pathological burst is dropped
  rather than allowed to stall the client.
