# Headless (clientless) client

`ntpl headless` logs into NosTale without the game client, the DLL or the
Gameforge launcher. It speaks the login and world protocols itself and feeds
the decrypted packets into the same `ntpl.engine.Engine` the GUI/MCP use.
This is the path to running many bots without many game windows.

Protocol work is ported from [NosBazar](https://github.com/hatz2/nosbazar)
(`backend/src/noscrypto.*`, `packets/login_packet.*`, `clientless.cpp`,
`net.*`), MIT License, Copyright (c) 2026 NosBazar contributors. See
[reusable-projects.md](reusable-projects.md).

## What works

- **Login cipher and framing** — `noscrypto/linux/host/ntpl/noscrypto.py`:
  the login XOR/shift, the packed world channel, session XOR and the first
  packet special case.
- **Login packet** — `NoS0577 <hex token>  NONE_CII <random> 0\x0B<version> 0 <md5>`
  with the version and MD5 taken from the installed `NostaleClientX.exe` /
  `NostaleClient.exe`.
- **`NsTeST` world list** — session id, account name, world servers.
- **World handshake** — `<session id>`, `<account> GF 0`, `thisisgfmode`,
  then `clist` → `select <slot>` → `game_start` → `lbs 0` → `ok`.
- **Keepalive** — `pulse <seconds> 0` every 60 s; `0` after 10 s without a
  send.
- **Engine feed** — every game packet after `ok` is fed to `Engine`, so the
  same map/entity/combat state is available without a client.

## Live validation (2026-11-08)

The real EU login server is `79.110.84.75:4000` (the client's own strings,
confirmed by probing its open ports; `4001` is closed). Sending a `NoS0577`
with a deliberately invalid token produced a **decrypted `failc 1`**, which
proves the packet layout, client version/hash and login cipher are accepted
by the server. The world/character phase needs a valid token.

## Token

The token is the **hexlified Gameforge thin token** (the value the real
client passes in `NoS0577`). Obtain it with the launcher auth path (e.g. the
MIT `NosTale-Auth` `NtLauncher.auth` + `getToken`), or the Gfless launcher.
Never commit it, log it, or paste it into the repository. The CLI takes it
from `--token`, `--token-file` (chmod 600) or `NTPL_HEADLESS_TOKEN`.

```bash
PYTHONPATH=linux/host python3 -m ntpl headless \
    --token-file ~/.config/ntpl/token \
    --game-dir "/path/to/Nostale" \
    --print-packets
```

`--email`/`--password` (or `NTPL_EMAIL`/`NTPL_PASSWORD`, which keeps the
password out of `ps`) use a best-effort bridge to the 2021 MIT
`NosTale-Auth` clone (`--nosauth-path`, default
`~/games/gameforge/NosTale-Auth`). That API predates the current Gameforge
blackbox/captcha flow and may be rejected; the dependable path is a token
from a current launcher. Credentials are used in memory only.

Options: `--login-host/--login-port` (default `79.110.84.75:4000`),
`--region`, `--character <slot>`, `--world-id/--channel`, `--account-id`,
`--locale`, `--print-packets`.

## Limits and next steps

- **Token acquisition**: a best-effort bridge to the 2021 `NosTale-Auth`
  launcher API is built in; the current Gameforge auth (iovation blackbox,
  captcha/PoW, OTP) is not ported. Use a token from a current launcher for
  reliable logins.
- **World phase** with a real token must be verified on a test account.
- **One session per account**: a second login for the same account can
  disconnect the game client or the other session.
- **Headless broker**: to let the GUI bot/MCP drive a headless session, wrap
  the headless transport in a broker with the DLL's command surface
  (`MOVE|`, `TARGET|`, `SEND|`, `POS`). Not implemented yet.
- **Many bots**: one process per account/session; a manager can start them
  from a config list without any game client.
- Gameforge's terms prohibit automation; the account risk stays with the
  owner. Do not add detection evasion.

## Tests

`make -C linux host-test` includes `tests/test_noscrypto.py` (cipher and
packing round trips) and `tests/test_headless.py` (`NsTeST` parsing, packet
layout, session framing, version/hash extraction).
