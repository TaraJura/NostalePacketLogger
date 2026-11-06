# Documentation

| Document | Read it when |
|---|---|
| [architecture.md](architecture.md) | you want the whole picture: components, data flow, why the DLL stays Windows code, Windows vs Linux |
| [hooking.md](hooking.md) | you need to understand or repair the in-game detours, signatures, assembly stubs, packet injection |
| [ipc.md](ipc.md) | you are writing a host, a bot, or another front end and need the wire protocol |
| [linux.md](linux.md) | you are building or running the Linux/Wine side |
| [windows.md](windows.md) | you are building or running the original MSVC/C# side |
| [development.md](development.md) | you are changing the code: layout, conventions, tests, adding packets |
| [roadmap.md](roadmap.md) | you want the phases and the next concrete tasks |
| [troubleshooting.md](troubleshooting.md) | something does not connect, does not appear, or the client crashes |
| [protocol/](protocol/README.md) | you need the meaning of a specific packet opcode |

The packet wiki in `protocol/` is shared by both platforms: the transport and
the host differ, the game's packets do not.

## Status snapshot

- **Windows tree (`windows/`)** — the original working implementation. Injector
  + DLL + WinForms GUI, named pipes.
- **Linux tree (`linux/`)** — new MinGW DLL + TCP loopback + Python CLI + GTK4
  GUI + state engine seed. Builds cleanly, host unit tests pass, and the DLL
  lifecycle/transport is verified under Wine with a fake game. The hooks
  themselves have **not** been exercised against the real client yet, because
  NosTale is not installed in a Wine prefix on this machine. See
  [linux.md](linux.md#honest-status).
