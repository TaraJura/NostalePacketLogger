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
| `mapdata.py` | `NStcData.NOS` parser, walkability, 8-direction A*, map ranking, PNG render |
| `monsternames.py` | `NSlangData_*.NOS` text archive, nibble codec, monster/skill names |
| `spells.py` | `NSgtdData.NOS` → `Skill.dat` spell catalog (MP, range, cooldown) |
| `spawnlog.py` | entity → vnum/player-name backfill from the DLL session log |
| `botconfig.py` | JSON bot configuration (targets, spell rules, settings) |
| `cli.py` | `monitor`, `walk`, `cast`, `send`, `status`, `map`, `replay`, `gui` |
| `gui.py` | GTK4 window: packet table + Map/Bot tab; optional import |

## Tests

From `linux/`:

```bash
make host-test      # 84 unittest cases
make gui-test       # headless GTK4: map switch, tooltips, toggles, recovery banner
make bot-test       # 5 smokes: combat, rules/config, spell order, rejection, recovery
```

The tests start a fake TCP server, so they need neither the DLL nor Wine. The
map tests build a synthetic NOS archive; the test against the real
`NStcData.NOS` is skipped when the game is not installed.

See `../../docs/map.md` for the map format and the bot loop.
