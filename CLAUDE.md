# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

NosTale Packet Logger — a tool for intercepting and displaying network packets from the NosTale MMORPG client. Evolved from FishBot (a fishing bot) into a general-purpose packet logger with a GUI.

### Long-term Goal: Autonomous Player

The ultimate goal is to build an **AI-driven autonomous NosTale player**. The roadmap:

1. **Phase 1 (CURRENT)**: Packet Logger — intercept, display, and send packets
2. **Phase 2**: Protocol Wiki — document every packet type by observing gameplay (see `docs/protocol/`)
3. **Phase 3**: Game State Model — parse packets into a structured world state (map, entities, inventory, combat)
4. **Phase 4**: Autonomous Agent — AI that reads game state and sends packets to play the game

### Protocol Documentation

All packet documentation lives in `docs/protocol/`. Each packet type has its own section. Documentation is built by observing live gameplay — the user performs actions while Claude reads the packet log and documents what each packet means.

- `docs/protocol/README.md` — Master index of all known packets
- `docs/protocol/movement.md` — Movement & positioning packets
- `docs/protocol/combat.md` — Combat & skills
- `docs/protocol/entities.md` — Entity spawn/despawn/info
- `docs/protocol/chat.md` — Chat & messaging
- `docs/protocol/inventory.md` — Items & inventory
- `docs/protocol/stats.md` — Character stats & status
- `docs/protocol/map.md` — Map & world navigation
- `docs/protocol/npc.md` — NPC interaction & shops
- `docs/protocol/unknown.md` — Undocumented packets to investigate

## Build

This is a Visual Studio solution (`PacketLogger.sln`) targeting **x86 (Win32)** only. Requires Visual Studio 18 (Insiders) with MSVC v145 toolset and .NET 8.0 SDK.

### Building from WSL (primary dev environment)

MSBuild is NOT on PATH. Use the full path:

```bash
# Full solution (DLL + Injector + GUI):
"/mnt/c/Program Files/Microsoft Visual Studio/18/Insiders/MSBuild/Current/Bin/MSBuild.exe" "$(wslpath -w /home/novakj/NosTalePacketLogger/PacketLogger.sln)" /p:Configuration=Release /p:Platform=x86

# GUI only:
"/mnt/c/Program Files/Microsoft Visual Studio/18/Insiders/MSBuild/Current/Bin/MSBuild.exe" "$(wslpath -w /home/novakj/NosTalePacketLogger/PacketLoggerGUI/PacketLoggerGUI.csproj)" /p:Configuration=Release
```

No tests or linter are configured.

### Output locations

- `Release/PacketLogger.dll` — the injected DLL
- `Release/Injector.exe` — standalone injector (legacy, GUI now handles injection)
- `PacketLoggerGUI/bin/Release/net8.0-windows/PacketLoggerGUI.exe` — the GUI app

## Architecture

Two main components (GUI now integrates injection):

1. **PacketLogger** (C++ DLL, Win32) — Injected into the game process. Uses pattern scanning (`Memory::FindPattern`) to locate the game's send/recv functions at runtime, then installs inline hooks (5-byte JMP for recv, 6-byte trampoline for send) that intercept packets. Captured packets are pushed into thread-safe queues (`SafeQueue`) and forwarded to the GUI over Windows named pipes. Also supports sending packets into the game via inline assembly that calls the game's send function through `TNTClient`. Logs all packets to `C:\NosTalePacketLog.txt` for live debugging.

2. **PacketLoggerGUI** (C# WinForms, .NET 8.0) — All-in-one app that:
   - Discovers NosTale processes and shows a selector with PID and window title
   - Tags game windows with their PID in the title bar for easy identification
   - Injects `PacketLogger.dll` into the selected process (requires admin, enforced via UAC manifest)
   - Auto-connects to the DLL via named pipes with retry logic (3 attempts, 5s timeout each)
   - Displays packets in a filterable ListView with RECV (cyan) and SEND (yellow) color coding
   - Supports sending packets back to the game (`;`-separated for multiple), exporting logs, and copy/paste
   - Disconnect returns to the process selector view

3. **Injector** (C++ console app) — Used by the GUI behind the scenes (`Injector.exe --pid <PID>` silent mode). Also works standalone with interactive process selection menu. The GUI launches it because native C++ injection is more reliable than C# P/Invoke (see rules below).

### Key files

- `PacketLogger/Packetlogger.cpp` — Core hook logic, pattern scanning, packet injection
- `PacketLogger/dllmain.cpp` — DLL entry, main loop, pipe communication, file logging
- `PacketLogger/PipeServer.cpp` — Named pipe server (DLL side)
- `PacketLoggerGUI/MainForm.cs` — GUI with process selector + packet logger views
- `PacketLoggerGUI/ProcessInjector.cs` — Win32 P/Invoke for process discovery and DLL injection
- `PacketLoggerGUI/PipeClient.cs` — Named pipe client with timeout support

## Key IPC Protocol

Messages over named pipes use `TYPE|DATA` format:
- `RECV|<packet>` — received packet from game server
- `SEND|<packet>` — packet sent by game client
- `STATUS|<message>` — status update
- `QUIT|` — disconnect signal

Pipe names: `NosTalePacketLogger_packets` (DLL→GUI) and `NosTalePacketLogger_commands` (GUI→DLL).

The DLL creates pipes with `ConnectNamedPipe` (blocking) — packets pipe first, then commands pipe. The GUI must connect in the same order.

## Important Details

- **x86 only**: The DLL uses inline x86 assembly (`__declspec(naked)`, `__asm`) for hooking. It cannot be built for x64.
- **C++14 standard** is used for the native projects.
- **Pattern signatures** in `Packetlogger.cpp` must be updated when the game client is patched — they are the primary maintenance burden.
- `NostaleString.h` wraps Delphi-style strings that the game expects when sending packets.
- **GUI requires admin** — enforced via `app.manifest` with `requireAdministrator` UAC level.
- **Live file log** at `C:\NosTalePacketLog.txt` — can be tailed from WSL with `tail -f /mnt/c/NosTalePacketLog.txt`.
- **Command file** at `C:\Users\Ninuška\NosTalePacketCmd.txt` — Claude can send packets by writing to this file (one packet per line). The GUI watches it with `FileSystemWatcher`, sends each line as a packet, then clears the file. Lines starting with `#` are ignored.

---

## IMPORTANT RULES

### Everything Must Be x86 (32-bit)

The game is a 32-bit Delphi application. The DLL uses x86 inline assembly. DLL injection via `CreateRemoteThread`/`LoadLibraryA` requires the injector process to match the target's bitness — `GetModuleHandle("kernel32.dll")` returns the address from the calling process, so a 64-bit injector gets the wrong kernel32 address for a 32-bit target.

**The GUI must be built with `<PlatformTarget>x86</PlatformTarget>`** in the .csproj. Without this, .NET 8 defaults to x64 on 64-bit Windows, injection silently fails (no DLL console appears, no error), and pipe connection times out.

### Line Endings (CRLF)

**MSVC WILL NOT compile files with LF-only line endings.** You get hundreds of `C2001: newline in constant` errors.

When editing C++ files from WSL:
1. The `Write` and `Edit` tools save with LF line endings
2. **After every write to a C/C++ file**, convert to CRLF before building:
   ```bash
   perl -pi -e 's/(?<!\r)\n/\r\n/' /path/to/file.cpp
   ```
3. **Never use `sed` for CRLF conversion** — `sed -i 's/$/\r/'` will double `\r` on files that already have CRLF, making things worse
4. C# files (.cs) are fine with LF — the .NET SDK handles both

### Killing Admin Processes from WSL

`taskkill.exe` from WSL **cannot kill elevated (admin) processes**. You get "Access is denied."

If the GUI or another admin process is locking a file:
- Ask the user to close it manually
- Do NOT retry the build in a loop — it won't work until the process is gone

### DLL Console Window

The DLL allocates a debug console via `AllocConsole()` inside the game process. **Closing this console window kills the game.**

This is prevented by `SetConsoleCtrlHandler` in `dllmain.cpp` that blocks `CTRL_CLOSE_EVENT`. Never remove this handler.

### Pipe Connection After Injection

The DLL's pipe server blocks on `ConnectNamedPipe` during startup. After injecting:
- Wait at least 1 second before attempting pipe connection
- Use a timeout (currently 5s) on `ConnectAsync` — don't wait forever
- Retry up to 3 times — the DLL may take time to initialize hooks and create pipes
- Connect `_packets` pipe first, then `_commands` pipe (must match DLL creation order)

### Building Tips

- Always build the full `.sln` with `/p:Platform=x86` — the DLL and Injector are Win32 only
- The GUI `.csproj` doesn't need the Platform flag (it's AnyCPU / .NET 8)
- Check for locked files before building — running GUI or game with injected DLL will lock outputs
- When build output is huge, pipe through `grep -E "(error |Build succeeded|Build FAILED)"` to see results quickly

### Windows Process Interaction from WSL

You can interact with Windows from WSL:
- `tasklist.exe` — list Windows processes
- `taskkill.exe /PID <pid> /F` — kill non-elevated processes
- `powershell.exe -c "Get-Process"` — PowerShell commands
- Windows executables on the WSL filesystem use UNC paths: `\\wsl.localhost\Ubuntu-24.04\...`
- Use `$(wslpath -w /linux/path)` to convert Linux paths to Windows paths for MSBuild

### C# P/Invoke Injection Does NOT Work — Use the C++ Injector

Do NOT try to inject the DLL using C# P/Invoke (`CreateRemoteThread`/`LoadLibraryA` via `[DllImport]`). It silently fails — no DLL console appears, no error, pipe connection just times out.

The GUI uses `ProcessInjector.cs` only for **process discovery and PID tagging** (via `FindNosTaleProcesses()` and `TagWindowWithPID()`). For actual injection, it launches the C++ `Injector.exe --pid <PID>` as a subprocess. This is proven to work reliably.

The Injector supports two modes:
- `Injector.exe` — interactive mode with process list menu
- `Injector.exe --pid <PID>` — silent mode, returns exit code 0 on success, 1 on failure
- `Injector.exe --pid <PID> --dll <path>` — silent mode with custom DLL path

### File Paths: WSL UNC Paths Don't Work for LoadLibraryA

`LoadLibraryA` inside a 32-bit game process **cannot load DLLs from WSL UNC paths** (`\\wsl.localhost\...`). The Injector uses `GetCurrentDirectoryA` to resolve the DLL path relative to itself, which works because both `Injector.exe` and `PacketLogger.dll` are in the same `Release/` directory.

If files need to be accessed by Windows processes, they must be on a real Windows path (e.g. `C:\...`), not a WSL UNC path.

### Claude Live Interaction with the Game

Claude can interact with the running game through two files:

**Reading packets** (game → Claude):
```bash
tail -f /mnt/c/NosTalePacketLog.txt           # live stream
grep "walk" /mnt/c/NosTalePacketLog.txt        # search specific packets
tail -50 /mnt/c/NosTalePacketLog.txt           # last 50 packets
```

**Sending packets** (Claude → game):
```bash
# Single packet:
echo "walk 19 146 0 10" > "/mnt/c/Users/Ninuška/NosTalePacketCmd.txt"

# Multiple packets (one per line):
printf "walk 19 146 0 10\nwalk 20 146 0 10" > "/mnt/c/Users/Ninuška/NosTalePacketCmd.txt"
```

The command file path is `C:\Users\Ninuška\NosTalePacketCmd.txt`. Cannot write to `C:\` root from WSL (permission denied). The user's home directory works.

### NosTale Packet Format Reference

Common packets seen in logs:
- `walk <x> <y> <checksum> <speed>` — SEND: move character
- `mv <type> <id> <x> <y> <speed>` — RECV: entity movement (type 1=player, 2=pet, 3=NPC/monster)
- `in <type> ...` — RECV: entity spawned on map
- `stat <hp> <hpMax> <mp> <mpMax> ...` — RECV: character stats
- `say <type> <id> <msg>` — RECV: chat message
- `c_map <mapId> ...` — RECV: map change
