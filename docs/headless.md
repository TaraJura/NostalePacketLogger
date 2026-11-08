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

`--email`/`--password` (or `NTPL_EMAIL`/`NTPL_PASSWORD`) use the current
Gameforge v2 login: the device blackbox, iovation enrollment and the
thin/codes call that yields the NoS0577 token. Run the client with no token
and it prompts for the email and password (nothing is stored or logged).
Gameforge rejects a generated device identity with `CREDENTIALS_INVALID`, so
the login needs a captured 30-value identity at
`~/.config/ntpl/identity.json` (or `--identity`); that file is sensitive,
mode 0600 and never committed. If Gameforge answers with its **image-drop
captcha**, the client downloads three images (the instruction text, the
icon strip and the target scene) to `~/.cache/ntpl/captcha-*`, opens them,
and asks for the icon number that matches the text (1 = leftmost). It
submits the answer and retries the login; a wrong answer refreshes the
images. This is Gameforge's human check — you solve it. A `gf-pow-captcha`
challenge needs a real browser; use a launcher token for that.

```bash
linux/scripts/headless.sh --print-packets
# Gameforge email: ...
# Password: ...
```

If you need to see the password while pasting it, add `--show-password`
(the prompt echoes it). It still uses the same login.

Options: `--login-host/--login-port` (default `79.110.84.75:4000`),
`--region`, `--character <slot>`, `--world-id/--channel`, `--account-id`,
`--locale`, `--identity`, `--print-packets`.

## Limits and next steps

- **Token acquisition**: the current v2 email/password login is implemented
  (terminal prompt or env) and needs a **captured device identity**; a
  generated identity is rejected as `CREDENTIALS_INVALID`. The image-drop
  captcha is solved by the user in the terminal (images open, type the icon
  number). The proof-of-work captcha and OTP are not solved. A token from a
  current launcher always works.
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
packing round trips), `tests/test_headless.py` (`NsTeST` parsing, packet
layout, session framing, version/hash extraction) and
`tests/test_gameforge_auth.py` (blackbox encoding, error classification,
UA magic).
