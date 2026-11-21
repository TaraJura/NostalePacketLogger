# Local NosTale database

`ntpl database` keeps the client's game data and the public packet
documentation in one SQLite file so later tools do not re-parse archives or
re-fetch repositories.

Default location:

```
~/.config/ntpl/nostale.db        # NTPL_DB overrides
~/.cache/ntpl/sources/           # cloned public repos (NTPL_SOURCE_CACHE)
```

The file is per-user and created by `ntpl db build`; nothing in `~/.git`,
`usercache-*`, `webcache-*` or any game session is read.

## Build

```bash
cd ~/games/gameforge/NostalePacketLogger

# client data + protocol wiki only
PYTHONPATH=linux/host python3 -m ntpl db build --local-only

# also clone/index the public packet projects (network)
PYTHONPATH=linux/host python3 -m ntpl db build

# limit the online part to one or more sources
PYTHONPATH=linux/host python3 -m ntpl db build \
    --only-source nosmooth --only-source noscore-packets
```

`--game-dir` points at another `NostaleData` directory; `--cache` changes the
clone cache. Existing clones are reused; the current commit hash and license
are stored per source.

## What is stored

| Table | Contents |
|---|---|
| `sources` / `source_files` | every indexed project, commit/license, and its text files |
| `packets` | opcode, direction (C2S/S2C), class/name, field list, description, source path |
| `items` | all `Item.dat` records: vnum, name, type, NPC price, full record JSON |
| `monsters` | all `monster.dat` records: vnum, name, level, full record |
| `skills` | `Skill.dat`: name, type, target, range, cast time, cooldown, MP |
| `maps` | `NStcData.NOS` grids: id, name, width, height |
| `nosmall_records` | every `<language>_nosmall.dat` record: mall vnum, item/base vnum, quantity, titles, COST/LINK blocks |
| `nosmall_groups` | variant vnums grouped by base item, ready to become `c_blist` groups |

The decrypted `Item.dat`, `monster.dat`, `Skill.dat`, `npctalk.dat`,
`shoptype.dat`, `MapIDData.dat` and the NosMall data are also kept in
`source_files`, so a future parser can re-read them without touching the client
archives. The NosBazar inventory picker reads those `Item.dat` records directly
(`nosmall.load_item_info`) for names, NPC prices and categories, so it works
without a built database; the same fields are in the `items` table.

### Public sources indexed

- [Rutherther/NosSmooth](https://github.com/Rutherther/NosSmooth) — C# packet
  classes with `[PacketHeader]`/`[PacketIndex]`
- [NosCoreIO/NosCore.Packets](https://github.com/NosCoreIO/NosCore.Packets) —
  modern server packet models
- [OpenNos/OpenNos](https://github.com/OpenNos/OpenNos) — classic server
  `ClientPackets`/`ServerPackets` (sparse checkout)
- [Scuym/go-noskit](https://github.com/Scuym/go-noskit) — Go clientless packet
  structs (`c_blist`, `c_slist`, `npc_req`, ...)
- [hatz2/nosbazar](https://github.com/hatz2/nosbazar) — NosBazar/Bibi Basar
  packets
- [hatz2/GflessClient](https://github.com/hatz2/GflessClient) — Gameforge login
  emulation
- [stdLemon/nostale-auth](https://github.com/stdLemon/nostale-auth),
  [morsisko/NosTale-Auth](https://github.com/morsisko/NosTale-Auth) — `NoS0577`
- [morsisko/NosCrypto](https://github.com/morsisko/NosCrypto) — protocol crypto
- [TaraJura/Nostale-NB](https://github.com/TaraJura/Nostale-NB) — the
  reference bazaar bot
- `imxeno/devtale`, `deva-hub/noscore`,
  `infinity-blackhole/celestial_protocol`,
  `Rutherther/NosTale-PacketLogger` — loggers and protocol experiments

The source list lives in `ntpl/database.py::ONLINE_SOURCES`; add a
`SourceSpec` to include another project. Files are stored as text with their
license/commit; binary files and files over 2 MB are skipped.

## Query

```bash
PYTHONPATH=linux/host python3 -m ntpl db stats
PYTHONPATH=linux/host python3 -m ntpl db sources
PYTHONPATH=linux/host python3 -m ntpl db packets --opcode c_blist
PYTHONPATH=linux/host python3 -m ntpl db packets --search bazaar
PYTHONPATH=linux/host python3 -m ntpl db items --vnum 2174
PYTHONPATH=linux/host python3 -m ntpl db items --search "Soulstone"
PYTHONPATH=linux/host python3 -m ntpl db nosmall --search "Wings"
PYTHONPATH=linux/host python3 -m ntpl db nosmall --export ~/bazar-nosmall-all.json --all
```

`db nosmall --export` writes a normal Bazar config with every group. The full
catalog is 1,184 groups (one search per group, 2.5 s pacing), so a full
monitor pass is long; prefer the GUI's **Import NosMall…** picker and add
only what you track. The picker reads `nosmall_groups` from this database
(or parses the client archives if the DB is missing) and offers only groups
with a known ND cost, excluding pets, fairies and partner supplies.

## NosMall prices

`<language>_nosmall.dat` does not contain the live web-shop ND price: its COST
block has bundle/quantity fields, and prices are server-side. The importer
therefore hides unpriced groups (a profit figure is impossible without a
cost): the curated `known_reference_costs` from Nostale-NB and the costs of
items already configured in the window are the price map. Add an item with
**New**, set its Cost, then reopen **Import NosMall…** to offer that group.

## Tests

`test_database.py` builds a temporary DB from the installed client data and
the local protocol wiki; `test_nosmall.py` checks catalog grouping and export.
Both skip when the client data is not installed, and they never use the
network.
