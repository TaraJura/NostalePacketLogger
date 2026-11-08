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
Gameforge v2 login. Run the client with no token and it prompts for the
email and password (nothing is stored or logged).

Without a captured identity the client logs in through **gameforge.com's
own sign-in form in headless Chrome**: the site loads `game1.js`, solves
its PoW challenge, and returns the bearer; the same page generates the
plain and request blackboxes. This needs Node.js and Chrome/Chromium
(`NTPL_NODE`, `NTPL_CHROME`). The credentials login is verified live
(HTTP 201) and **iovation passes** with this website session (HTTP 200).
The login request must use the same User-Agent the blackbox was generated
with (read from the blackbox's `userAgent` field); sending the launcher's
Chrome/72 UA with a Chrome 156 blackbox makes Gameforge answer
`CREDENTIALS_INVALID`.

A captured 30-value identity at `~/.config/ntpl/identity.json` (or
`--identity`) uses the direct API login; that file is sensitive, mode 0600
and never committed. With that path, Gameforge's **image-drop captcha** is
handled in **one captcha window**: the client downloads the instruction
text, the icon strip and the target scene, and shows the three images with
an icon-number box. Read the instruction, find the matching icon in the
strip (1 = leftmost), enter its number and press Submit. The answer is
posted and the login retries with the `gf-challenge-id` header, as
Gameforge's own `withFetchPatch.js` does; a wrong answer refreshes the
images in the same window. This is Gameforge's human check — you solve it.
If GTK or a display is unavailable, the images open separately and the
terminal asks for the number. The website login path handles the
proof-of-work challenge itself; an image-drop captcha on the website is
reported by the helper instead of solved.

The last game-token step, `thin/codes`, currently answers `403 Not allowed
to create code` with the browser-generated device. The official client
adds an authorization-ticket (`codeId`) step; the Gfless ecosystem's
supported solution is a 30-value identity captured from a real Gameforge
client, which the `--identity` path uses. A launcher token
(`--token-file`) skips the whole Gameforge login and always works.

```bash
linux/scripts/headless.sh
# Gameforge email: ...
# Password (visible): ...
```

The wrapper always passes `--print-packets --show-password`, so every packet
is printed and the password is echoed while you enter it (easy to verify a
paste). Extra arguments are passed through to `ntpl headless`.

Options: `--login-host/--login-port` (default `79.110.84.75:4000`),
`--region`, `--character <slot>`, `--world-id/--channel`, `--account-id`,
`--locale`, `--identity`, `--print-packets`.

## Limits and next steps

- **Token acquisition**: the v2 email/password login (HTTP 201) and the
  website-login iovation step (HTTP 200) are verified live. The final
  `thin/codes` call returns `403 Not allowed to create code` for
  browser-generated devices; the official client adds an authorization
  ticket (`codeId`) and the Gfless ecosystem uses a captured identity. Use
  a 30-value identity (`--identity`) or a launcher token (`--token-file`)
  to get an actual game token. The image-drop captcha is solved in one GTK
  window (or the terminal); the proof-of-work captcha and OTP are not
  solved.
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
`tests/test_gameforge_auth.py` (identity files, the game1.js browser
runner, the website-login mint path, blackbox encryption, error
classification, UA magic).
