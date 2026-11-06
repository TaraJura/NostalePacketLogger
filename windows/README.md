# Windows implementation (original)

This is the original Visual Studio solution, moved here unchanged when the
Linux port was added. It still targets **x86 (Win32) only**.

| Path | What |
|---|---|
| `PacketLogger.sln` | solution (DLL + Injector + GUI) |
| `PacketLogger/` | C++ DLL injected into the game, named-pipe server, inline-asm hooks |
| `Injector/` | C++ console injector |
| `PacketLoggerGUI/` | C# WinForms GUI (.NET 8.0) |
| `error.png` | screenshot from the original development |

The Windows build and the Linux port share the packet formats in
`../docs/protocol/` but not the transport: this tree uses Windows named pipes
and a WinForms client; the Linux tree uses TCP loopback and a Python/GTK4 host.
See `../docs/windows.md` for the full Windows guide and
`../docs/architecture.md` for the comparison.

## Components

- **Injector** — finds running NosTale processes and injects the DLL. Requires
  administrator privileges.
- **PacketLogger** — DLL that hooks the game's send/recv functions with pattern
  scanning and forwards packets over named pipes.
- **PacketLoggerGUI** — WinForms app that connects to the DLL, displays packets,
  filters/exports them, and sends packets back.

## How to use

1. Launch NosTale.
2. Run `Injector.exe` as administrator. It injects `PacketLogger.dll` into all
   detected processes. Both files must be in the same folder.
3. Open `PacketLoggerGUI` and click **Connect**.

## Building

Requires Visual Studio 2022+ (the original notes say VS 18 Insiders / MSVC
v145) and the .NET 8.0 SDK.

```powershell
msbuild PacketLogger.sln /p:Configuration=Release /p:Platform=x86
```

From WSL, with the full MSBuild path:

```bash
"/mnt/c/Program Files/Microsoft Visual Studio/18/Insiders/MSBuild/Current/Bin/MSBuild.exe" \
  "$(wslpath -w /path/to/windows/PacketLogger.sln)" /p:Configuration=Release /p:Platform=x86
```

Outputs:

- `Release/PacketLogger.dll`
- `Release/Injector.exe`
- `PacketLoggerGUI/bin/Release/net8.0-windows/PacketLoggerGUI.exe`

## GUI features

- Real-time packet display with RECV/SEND colour coding
- Text filter by content or opcode
- Send custom packets (`;` chains several)
- Export captured packets
- Copy via right-click or Ctrl+C
- Double-click a packet to resend it

The original project README follows.

---

# NosTale Packet Logger

A packet logger for the MMORPG NosTale. Intercepts and displays sent and
received network packets in real time through a GUI, and allows sending custom
packets back to the game.

Originally based on [FishBot v2](https://github.com/hatz02/FishBot), rewritten
as a general-purpose packet logging tool.

## Components

- **Injector** — Console app that finds running NosTale processes and injects
  the DLL. Requires administrator privileges.
- **PacketLogger** — DLL injected into the game process. Hooks the game's
  send/recv functions using pattern scanning and forwards intercepted packets
  over named pipes.
- **PacketLoggerGUI** — WinForms app (.NET 8.0) that connects to the injected
  DLL and displays packets with filtering, searching, exporting, and packet
  sending capabilities.

## How to use

1. Launch NosTale.
2. Run `Injector.exe` (as administrator). It will inject `PacketLogger.dll` into
   all detected NosTale processes. Both files must be in the same folder.
3. Open `PacketLoggerGUI` and click **Connect**.

Alternatively, inject `PacketLogger.dll` with any DLL injector if you do not
want to inject into all processes at once.

## Building

Requires Visual Studio 2022+ with MSVC v145 toolset and .NET 8.0 SDK. The
native projects target **x86 (Win32)** only.

```
msbuild PacketLogger.sln /p:Configuration=Release /p:Platform=x86
```

## GUI Features

- Real-time packet display with RECV/SEND color coding
- Text filter for packets by content or opcode
- Send custom packets to the game (use `;` to chain multiple packets)
- Export captured packets to a log file
- Copy packets via right-click or Ctrl+C
- Double-click a packet to resend it
