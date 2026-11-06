# Windows guide (original implementation)

`windows/` is the original Visual Studio solution. It was moved there unchanged
when the Linux port was added. It still works the way it did; the only path
that changed is the solution location.

## Layout

| Path | What |
|---|---|
| `windows/PacketLogger.sln` | solution |
| `windows/PacketLogger/` | C++ DLL (Win32), inline-asm hooks, named-pipe server |
| `windows/Injector/` | C++ console injector |
| `windows/PacketLoggerGUI/` | C# WinForms GUI (.NET 8.0, x86) |
| `windows/error.png` | original screenshot |

## Build

Requires Visual Studio (the original notes say VS 18 Insiders with MSVC v145)
and the .NET 8.0 SDK. MSBuild is not on PATH in WSL; use the full path.

```powershell
msbuild windows\PacketLogger.sln /p:Configuration=Release /p:Platform=x86
```

From WSL:

```bash
"/mnt/c/Program Files/Microsoft Visual Studio/18/Insiders/MSBuild/Current/Bin/MSBuild.exe" \
  "$(wslpath -w /path/to/NostalePacketLogger/windows/PacketLogger.sln)" \
  /p:Configuration=Release /p:Platform=x86
```

Outputs:

- `windows/Release/PacketLogger.dll`
- `windows/Release/Injector.exe`
- `windows/PacketLoggerGUI/bin/Release/net8.0-windows/PacketLoggerGUI.exe`

No tests or linter are configured.

## Run

1. Launch NosTale.
2. Run `Injector.exe` as administrator. It finds the client by executable name
   and injects `PacketLogger.dll`. Both files must be in the same folder.
3. Open `PacketLoggerGUI` and click **Connect**.

The DLL allocates a console window inside the game. **Closing that console
kills the game.** A `SetConsoleCtrlHandler` blocks `CTRL_CLOSE_EVENT`; do not
remove it. Use the GUI's Disconnect button instead.

## Transport

Two named pipes:

- `\\.\pipe\NosTalePacketLogger_packets` — DLL → GUI
- `\\.\pipe\NosTalePacketLogger_commands` — GUI → DLL

The DLL creates the packets pipe first, then the commands pipe. The GUI must
connect in the same order, wait at least a second after injection, and retry
(3 attempts, 5 s timeout each). Messages use the same `TYPE|DATA` format as
[ipc.md](ipc.md), just over pipes.

## Files the original GUI watches

- `C:\NosTalePacketLog.txt` — live packet log. Tail it from WSL with
  `tail -f /mnt/c/NosTalePacketLog.txt`.
- `C:\Users\<user>\NosTalePacketCmd.txt` — Claude/automation command file. The
  GUI watches it with `FileSystemWatcher`, sends each line as a packet, then
  clears it. Lines starting with `#` are ignored. The original notes say
  `C:\` root is not writable from WSL, so the user's home is used.

The Linux port replaces both with the TCP protocol and the `ntpl send` command.

## x86 rules

- The game and the DLL are 32-bit. The DLL uses `__declspec(naked)` and
  `__asm`, so it cannot be built for x64.
- The injector must also be 32-bit. `GetModuleHandle("kernel32.dll")` returns an
  address from the calling process; a 64-bit injector gets the wrong address
  for a 32-bit target.
- The C# GUI must be `<PlatformTarget>x86</PlatformTarget>`. Without it, .NET 8
  defaults to x64, injection silently fails, and the pipe connection times out.
- C# P/Invoke injection (`CreateRemoteThread`/`LoadLibraryA` via `DllImport`)
  does not work reliably; the GUI launches `Injector.exe --pid <PID>` instead.
  `ProcessInjector.cs` is used only for process discovery and PID tagging.

## WSL / MSVC quirks the original notes called out

- **CRLF:** MSVC will not compile LF-only C/C++ files (`C2001: newline in
  constant`). After editing from WSL, convert with
  `perl -pi -e 's/(?<!\r)\n/\r\n/' file.cpp`. Never use
  `sed -i 's/$/\r/'` — it doubles `\r` on files that already have it. C# files
  are fine with LF.
- **Admin processes:** `taskkill.exe` from WSL cannot kill elevated processes.
  Ask the user to close the GUI; do not retry the build in a loop.
- **UNC paths:** `LoadLibraryA` inside the game cannot load a DLL from
  `\\wsl.localhost\...`. The injector resolves the DLL relative to itself, so
  both files must be in a real Windows directory.
- **Locked files:** a running GUI or a game with the DLL injected locks the
  build outputs. Close them before rebuilding.

## Relationship to the Linux tree

| | `windows/` | `linux/` |
|---|---|---|
| Compiler | MSVC | MinGW-w64 |
| Assembly | MSVC inline `__asm` | GAS `.S` |
| Transport | named pipes | TCP loopback |
| GUI | WinForms | Python + GTK4 |
| Status | GUI label | `STATUS|` line + `ntpl status` |
| File log | `C:\NosTalePacketLog.txt` | same, with `%LOCALAPPDATA%` fallback |
| Console | `AllocConsole()` | none |

The hook addresses, patterns and offsets are the same. The Linux DLL can run on
real Windows too (it is a normal PE32 DLL), but the WinForms GUI speaks named
pipes and will not talk to it; use the Python host with the TCP DLL, or keep
using this tree on Windows.
