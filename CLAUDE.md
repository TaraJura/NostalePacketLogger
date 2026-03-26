# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

NosTale Packet Logger — a tool for intercepting and displaying network packets from the NosTale MMORPG client. Evolved from FishBot (a fishing bot) into a general-purpose packet logger with a GUI.

## Build

This is a Visual Studio solution (`PacketLogger.sln`) targeting **x86 (Win32)** only. Requires Visual Studio 2022+ with MSVC v145 toolset and .NET 8.0 SDK.

```
msbuild PacketLogger.sln /p:Configuration=Release /p:Platform=x86
```

Or open `PacketLogger.sln` in Visual Studio and build. Output goes to `Release/` (PacketLogger.dll, Injector.exe).

The GUI project:
```
dotnet build PacketLoggerGUI/PacketLoggerGUI.csproj
```

No tests or linter are configured.

## Architecture

Three projects communicate in a pipeline:

1. **Injector** (C++ console app, requires admin) — Finds running NosTale processes (`NostaleClientX.exe`, `NostaleX.dat`, `CustomClient.exe`) and injects `PacketLogger.dll` into them via `CreateRemoteThread`/`LoadLibrary`.

2. **PacketLogger** (C++ DLL, Win32) — Injected into the game process. Uses pattern scanning (`Memory::FindPattern`) to locate the game's send/recv functions at runtime, then installs inline hooks (5-byte JMP for recv, 6-byte trampoline for send) that intercept packets. Captured packets are pushed into thread-safe queues (`SafeQueue`) and forwarded to the GUI over Windows named pipes. Also supports sending packets into the game via inline assembly that calls the game's send function through `TNTClient`.

3. **PacketLoggerGUI** (C# WinForms, .NET 8.0) — Connects to the DLL via named pipes (`NosTalePacketLogger_packets` for reading, `NosTalePacketLogger_commands` for writing). Displays packets in a filterable ListView with RECV (cyan) and SEND (yellow) color coding. Supports sending packets back to the game (`;`-separated for multiple), exporting logs, and copy/paste.

## Key IPC Protocol

Messages over named pipes use `TYPE|DATA` format:
- `RECV|<packet>` — received packet from game server
- `SEND|<packet>` — packet sent by game client
- `STATUS|<message>` — status update
- `QUIT|` — disconnect signal

## Important Details

- **x86 only**: The DLL uses inline x86 assembly (`__declspec(naked)`, `__asm`) for hooking. It cannot be built for x64.
- **C++14 standard** is used for the native projects.
- **Pattern signatures** in `Packetlogger.cpp` must be updated when the game client is patched — they are the primary maintenance burden.
- `NostaleString.h` wraps Delphi-style strings that the game expects when sending packets.
- The Injector requires administrator privileges (set via UAC manifest in vcxproj).
