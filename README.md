# NosTale Packet Logger
<img width="1912" height="1026" alt="artwork (3)" src="https://github.com/user-attachments/assets/974117f1-3f98-4cdf-9e35-12bfbe627125" />

A packet logger for the MMORPG NosTale. Intercepts and displays sent and received network packets in real time through a GUI, and allows sending custom packets back to the game.

Originally based on [FishBot v2](https://github.com/hatz02/FishBot), rewritten as a general-purpose packet logging tool.

## Components

- **Injector** — Console app that finds running NosTale processes and injects the DLL. Requires administrator privileges.
- **PacketLogger** — DLL injected into the game process. Hooks the game's send/recv functions using pattern scanning and forwards intercepted packets over named pipes.
- **PacketLoggerGUI** — WinForms app (.NET 8.0) that connects to the injected DLL and displays packets with filtering, searching, exporting, and packet sending capabilities.

## How to use

1. Launch NosTale.
2. Run `Injector.exe` (as administrator). It will inject `PacketLogger.dll` into all detected NosTale processes. Both files must be in the same folder.
3. Open `PacketLoggerGUI` and click **Connect**.

Alternatively, you can manually inject `PacketLogger.dll` with any DLL injector of your choice if you don't want to inject into all processes at once.

## Building

Requires Visual Studio 2022+ with MSVC v145 toolset and .NET 8.0 SDK. The native projects target **x86 (Win32)** only.

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
