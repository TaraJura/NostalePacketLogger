"""Best-effort bridge to the MIT `NosTale-Auth` launcher API.

`ntpl headless --email/--password` uses this module to obtain the hex
Gameforge thin token that `NoS0577` needs. The upstream package is imported
from the clone beside this repository (or ``NTPL_NOSAUTH_PATH``); no code is
copied and no credential or token is ever written or logged.

The upstream implementation is from 2021 and targets Gameforge's original
launcher endpoints. The current launcher adds an iovation blackbox and may
require a captcha/PoW for a fresh password login, so this bridge can be
rejected by today's API. The supported fallback is ``--token``/``--token-file``
with a token obtained by a current launcher (NosBazar, GflessClient) or the
upstream auth path when it still works.

Upstream: https://github.com/morsisko/NosTale-Auth (MIT, Copyright (c) 2019
Michael). Keep its notice when redistributing the package.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


class LauncherAuthError(RuntimeError):
    """The launcher auth path is unavailable or the login was rejected."""


def _default_root() -> Path:
    override = os.environ.get("NTPL_NOSAUTH_PATH")
    if override:
        return Path(override).expanduser()
    return Path.home() / "games" / "gameforge" / "NosTale-Auth"


def _load_launcher(root: Path):
    package = root / "nosauth" / "api.py"
    if not package.exists():
        raise LauncherAuthError(
            f"NosTale-Auth not found at {root}; set NTPL_NOSAUTH_PATH"
        )
    entry = str(root)
    if entry not in sys.path:
        sys.path.insert(0, entry)
    try:
        from nosauth.api import NtLauncher
    except ImportError as exc:
        raise LauncherAuthError(
            f"cannot import {package}: {exc} (requests and the cert bundle "
            "must be present)"
        ) from exc
    return NtLauncher


def obtain_token(
    email: str,
    password: str,
    locale: str = "en_EN",
    account_id: str | None = None,
    root: Path | None = None,
) -> tuple[str, str, str]:
    """Return ``(hex_token, account_id, display_name)`` for one game account.

    The password is used only in this call. Nothing is printed or persisted.
    """
    if not email or not password:
        raise LauncherAuthError("email and password are required")
    launcher_class = _load_launcher(root or _default_root())
    launcher = launcher_class(locale, locale.split("_", 1)[0])
    if not launcher.auth(email, password):
        raise LauncherAuthError(
            "Gameforge login was rejected (the 2021 launcher API may be "
            "outdated); use --token-file with a token from a current launcher"
        )
    accounts = launcher.getAccounts()
    if not accounts:
        raise LauncherAuthError("no game accounts on this Gameforge account")
    mapping = dict(accounts)
    chosen = account_id or accounts[0][0]
    if chosen not in mapping:
        raise LauncherAuthError(f"game account {chosen} not in {list(mapping)}")
    token = launcher.getToken(chosen)
    if not token:
        raise LauncherAuthError(
            "the launcher returned no thin token (a captcha/challenge or an "
            "outdated API is likely)"
        )
    return token, chosen, mapping[chosen]
