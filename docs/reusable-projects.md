# Reusable upstream projects (Gameforge / NosTale protocol)

Five MIT-licensed projects are cloned next to this repository in
`~/games/gameforge/`. They solve problems we still have: running the game
without the Gameforge launcher, and talking to the game servers without the
game client at all. This page records what each one contains, what we can
reuse, the license terms, and the order in which it makes sense to adopt it.

The sources were only read, not executed. Treat them as untrusted code until
the parts we copy have been reviewed.

| Project | Local clone | Language | Latest commit | License |
|---|---|---|---|---|
| **NosBazar** | `~/games/gameforge/nosbazar` | C++23 (CMake, asio, vcpkg) | `7e0e643`, 2026-10-18 | MIT, © 2026 NosBazar contributors |
| **GflessClient** | `~/games/gameforge/GflessClient` | C++/Qt, TypeScript, C# | `dc2437f`, 2026-11-03 | MIT, © 2021 hatz02 |
| **NosTale-Gfless** | `~/games/gameforge/NosTale-Gfless` | Python | `437cd30`, 2020-04-09 | MIT, © 2020 Michael |
| **NosTale-Auth** | `~/games/gameforge/NosTale-Auth` | Python | `aa447a1`, 2021-12-05 | MIT, © 2019 Michael |
| **nostale-auth** | `~/games/gameforge/nostale-auth` | Go | `59f2110`, 2026-03-03 | MIT, © 2022 std::Lemon |

GflessClient credits [morsisko](https://github.com/morsisko)
(`NosTale-Auth`, `NosTale-Gfless`) and
[stdLemon](https://github.com/stdLemon) (`nostale-auth`) as the projects its
protocol work is based on. `NosTale-Gfless` includes `NosTale-Auth` as a git
submodule.

## Why these matter for our goals

Our current engine needs the real client, a Gameforge login, and an injected
DLL. These projects open two other paths:

1. **Real client without the Gameforge launcher.** `NosTale-Auth` +
   `NosTale-Gfless` (or the `GflessClient` launcher) authenticate against the
   Gameforge HTTP API, obtain the session token, emulate the
   `\\.\pipe\GameforgeClientJSONRPC` named pipe, and start
   `NostaleClientX.exe gf <region>`. The game reaches the server-selection
   screen without the Gameforge client. Our DLL/injector then works unchanged.
2. **Clientless play.** `NosBazar` logs into the Nostale login server and a
   world server by itself, with the client's own encryption. It never starts
   the game. This is the foundation for a headless bot and would also give us
   the login-time `ski` skill list directly (the current "slot unknown"
   problem), all `in`/`out`/`st` state, and full packet control.

Both directions pass through the same protocol we already document; the
packet headers in NosBazar (`at`, `c_map`, `in`, `walk`, `gp`, `cond`, …)
match our [docs/protocol/](protocol/README.md), which is a useful independent
validation of our model.

## What to reuse, component by component

### Cryptography — the key enabler

| Source | What it is |
|---|---|
| `nosbazar/backend/src/noscrypto.{h,cpp}` (315 lines) | Login and world encryption/decryption, the 16-byte tables, session XOR and the first-packet special case, `unpack`. |

This is the piece that lets a host speak to the login/world servers without
the client. Port it once (C++ for a native host, or Python for a clientless
engine) and keep the upstream header/notice.

### Clientless login and sessions

| Source | What it is |
|---|---|
| `nosbazar/backend/src/clientless.{h,cpp}` (~400 lines) | The full state machine: `phase_authenticate` → `phase_login` → `phase_world` → `phase_game`, retries, captcha exit code, disconnect handling. |
| `nosbazar/backend/src/net.{h,cpp}` (~465 lines) | asio TCP client, delimiter packet accumulator, login/world session classes, request/response over HTTP (curl). |
| `nosbazar/backend/src/packets/login_packet.{h,cpp}` | `NoS0577` login packet (session token, installation id, region, client version/hash) and `NsTeST` world-server list parsing. |
| `nosbazar/backend/src/packets/*.h` | Parsed packet structs for `at`, `c_map`, `in`, `walk`, `gp`, `cond`, `c_blist`, `rc_blist`. |
| `nosbazar/backend/src/auth/nosauth.{h,cpp}`, `identity.*`, `blackbox.*`, `token_repository.*` | Gameforge HTTP authentication, iovation blackbox, identities, token storage. |

### Clientless game state and play

| Source | What it is |
|---|---|
| `nosbazar/backend/src/game/sensors.{h,cpp}` | Packet-driven `SelfPlayer`, scene NPCs and portals — the same shape as our `WorldState`, independently implemented. |
| `nosbazar/backend/src/agent/*` | State machine for clientless play: `walk_state`, `walk_think_state`, `open_bazar_state`, `search_bazar_state`. Useful as a reference for a headless bot loop and for its map/portal handling. |
| `nosbazar/backend/src/map_grid_repository.*`, `distances.*` | Map grid storage and distance helpers. |

### Game data parsing

| Source | What it is |
|---|---|
| `nosbazar/backend/src/io/skill_dat_parser.*` | `Skill.dat` scan (vnum, name) — compare with our `spells.py`. |
| `nosbazar/backend/src/io/item_dat_parser.*`, `monster_dat_parser.*` | Item and monster static data. |
| `nosbazar/backend/src/io/lang_*`, `const_string_parser.*`, `nos_file_text_reader.*`, `nos_text_dat_file_decryptor.*`, `nos_zlib_decryptor.*`, `nsip_data_reader.*` | Language archives, text dat decryptors, NOS/NSip readers — cross-check against `monsternames.py`, `mapdata.py`. |

### Gameforge launcher emulation (real client)

| Source | What it is |
|---|---|
| `NosTale-Auth/nosauth/api.py` | **Python**, directly importable: `NtLauncher.auth(email, password)`, `getAccounts()`, `getToken(uid, raw)`; documents the `NoS0577` login packet and the `_TNT_SESSION_ID` / `_TNT_CLIENT_APPLICATION_ID` mechanics. |
| `NosTale-Gfless/server.py` | Minimal named-pipe server: answers `ClientLibrary.isClientRunning`, `initSession`, `queryAuthorizationCode`, `queryGameAccountName`, and launches `NostaleClientX.exe gf <country>`. The smallest complete "no GF launcher" prototype. |
| `GflessClient/Launcher/src/auth/gflessclient.{h,cpp}` | Production version: pipe server, response framing, token/display name, `injectDll` of the GflessDLL. |
| `GflessClient/Launcher/src/auth/nostaleauth.*`, `identity.*`, `blackbox.*`, `fingerprint.*`, `captchasolver.*`, `powcaptchasolver.*` | Gameforge auth, identities, blackbox, captcha (image and proof-of-work) — the complete login flow. |
| `GflessClient/Launcher/src/auth/gameupdater.*` | Patch-manifest download/verification (same idea as NosBazar's `nosclient.cpp`). |
| `nostale-auth` (Go) | iovation blackbox/identity generation reference (schema v12) — useful when the Python/TS implementations need updating. |

### In-client automation (our DLL)

| Source | What it is |
|---|---|
| `GflessClient/GflessDLL/GflessDLL/dllmain.cpp` | Injected DLL that reads server/channel/character settings from a named pipe and automates the selection screen. |
| `GflessClient/GflessDLL/GflessDLL/PatternScanner.*` | Compact pattern scanner; compare with our `Memory::FindPattern`. |
| `GflessClient/GflessDLL/GflessDLL/GameStructures/*` | Reversed widget layouts: `TCharacterSelectWidget` (character info, buttons, `selectedIndex`), `TNTNewServerSelectWidget2`, `TLBSWidget(List)`, `TEWButtonWidget`, `TEWGraphicButtonWidget`, `NostaleString.h`. These offsets are directly useful if we automate server/channel/character selection or other UI. |
| `GflessClient/GflessDLL/GflessDLL/NosmallDisabler.*` | Example of a targeted in-client patch. |

## Licensing and compliance

- All five projects are **MIT**: reuse, modification and redistribution are
  allowed provided the copyright notice and permission text are retained.
  Keep the upstream notices on copied files and add an attribution line in
  the file/commit that introduces them.
- Game content, data, trademarks and the client belong to Gameforge 4D GmbH
  and Entwell Co., Ltd. A clientless login may violate the game's terms of
  service; the account risk stays with the owner (see the README note).
- Never commit credentials, session tokens, `identity.json`, blackbox strings,
  certificates, or `.env` files. The upstream repositories gitignore those;
  ours must keep doing the same.
- The clones are third-party code: audit anything we copy before building or
  running it. Do not run their installers/binaries as part of our tests.

## Suggested adoption order

1. **Protocol reference (now, no code copied).** Cross-check
   `docs/protocol/` with NosBazar's packet structs and add any missing fields.
2. **Real client without the Gameforge launcher (small, high value).** Port
   `NosTale-Auth/nosauth/api.py` + the named-pipe JSON-RPC server from
   `NosTale-Gfless/server.py` (or reuse `GflessClient`) so the game starts
   with `gf <region>`; our existing DLL/injector keeps working.
3. **Clientless engine (the big step).** Port `noscrypto` and the
   login/world session from NosBazar to Python, reuse our A*/map/bot logic,
   and feed packets into the existing `Engine`. This removes the client
   dependency and provides the login `ski` list, complete entity lifecycles
   (including `out`), and full packet control.
4. **Data/agent reuse.** Compare/port the Skill.dat, item, monster and
   language parsers; borrow the clientless agent state-machine structure.
5. **Optional in-client automation.** Adapt the GflessDLL widget layouts and
   pattern scanning for server/channel/character selection and other UI
   actions in our DLL.

## Provenance

Cloned on 2026-11-07 next to this repository:

```
git clone https://github.com/hatz2/nosbazar.git        # 7e0e643
git clone https://github.com/hatz2/GflessClient.git    # dc2437f
git clone https://github.com/morsisko/NosTale-Gfless.git  # 437cd30
git clone https://github.com/morsisko/NosTale-Auth.git    # aa447a1
git clone https://github.com/stdLemon/nostale-auth.git    # 59f2110
```

The launcher/DLL binaries and installers in these repositories were not
executed.
