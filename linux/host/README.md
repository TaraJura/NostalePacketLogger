# ntpl — Python host

The native side of the Linux port: a TCP client for the injected DLL, a packet
model, a world-state engine seed, a CLI and a GTK4 GUI.

Nothing here depends on Windows. The DLL is the only component that must be
32-bit Windows code, because it lives inside the game.

## Run without installing

```bash
PYTHONPATH=linux/host python3 -m ntpl --help
PYTHONPATH=linux/host python3 -m ntpl monitor
```

Or through the wrapper:

```bash
linux/scripts/host.sh status
linux/scripts/host.sh gui
```

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e linux/host
ntpl monitor
```

The core (`protocol`, `client`, `engine`, `cli`) uses only the standard
library. The GUI needs PyGObject and GTK4; on Ubuntu install them with apt:

```bash
sudo apt install python3-gi gir1.2-gtk-4.0
```

## Modules

| Module | Responsibility |
|---|---|
| `protocol.py` | `TYPE|PAYLOAD` line protocol, `Message`, `Packet`, port from `NTPL_PORT` |
| `client.py` | TCP connect with retries, line reassembly, send, reader thread |
| `engine.py` | `WorldState` (map, position, entities, stats, chat) fed by packets |
| `cli.py` | `monitor`, `walk`, `cast`, `send`, `status`, `replay`, `gui` |
| `gui.py` | GTK4 window; optional import |

## Tests

From `linux/`:

```bash
make host-test      # 37 unittest cases
make gui-test       # headless GTK4 smoke test (xvfb)
```

The tests start a fake TCP server, so they need neither the DLL nor Wine.
