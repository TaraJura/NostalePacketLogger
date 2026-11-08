"""Browser-assisted Gameforge login for the headless client.

Gameforge's iovation device check only accepts a session created by the
real website login (which solves the PoW challenge). ``browser_token``
drives gameforge.com's own sign-in form in headless Chrome through the
Node/CDP helper ``browser_login.mjs`` and returns the bearer plus the two
site-generated blackboxes.

Credentials go to the helper through the environment; the token and
blackboxes live in a 0600 temporary directory that is removed before
returning. Nothing is logged.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .gameforge_auth import AuthError, find_browser

HELPER = Path(__file__).with_name("browser_login.mjs")


@dataclass
class BrowserLogin:
    token: str
    blackbox: str
    extra: str


def browser_token(
    email: str,
    password: str,
    installation: str,
    gsid_base: str,
    feature: int,
    timeout: float = 180.0,
) -> BrowserLogin:
    """Log in through gameforge.com in headless Chrome.

    Returns the bearer token, the plain blackbox and the request blackbox
    (``game1`` with features/installation/session) generated in the same
    page. Raises AuthError when Node, Chrome or the site login is
    unavailable.
    """
    node = shutil.which(os.environ.get("NTPL_NODE", "node"))
    if node is None:
        raise AuthError(
            "the Gameforge website login needs Node.js; install node or "
            "use --identity/--token-file"
        )
    browser = find_browser()
    if browser is None:
        raise AuthError(
            "the Gameforge website login needs Chrome/Chromium; set "
            "NTPL_CHROME or use --identity/--token-file"
        )
    if not HELPER.exists():
        raise AuthError(f"browser login helper {HELPER} is missing")
    directory = Path(tempfile.mkdtemp(prefix="ntpl-browser-"))
    try:
        environment = dict(os.environ)
        environment.update(
            {
                "GF_EMAIL": email,
                "GF_PASSWORD": password,
                "GF_INSTALLATION": installation,
                "GF_GSID": gsid_base,
                "GF_FEATURE": str(feature),
                "GF_OUT": str(directory),
                "GF_CHROME": browser,
            }
        )
        result = subprocess.run(
            [node, str(HELPER)],
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            lines = (result.stdout + result.stderr).strip().splitlines()
            detail = lines[-1][:200] if lines else "no output"
            raise AuthError(f"Gameforge website login failed: {detail}")
        token = (directory / "token").read_text().strip()
        blackbox = (directory / "plain").read_text().strip()
        extra = (directory / "extra").read_text().strip()
        if not (token and blackbox and extra):
            raise AuthError("Gameforge website login returned incomplete data")
        return BrowserLogin(token, blackbox, extra)
    except subprocess.TimeoutExpired as exc:
        raise AuthError("the Gameforge website login timed out") from exc
    finally:
        shutil.rmtree(directory, ignore_errors=True)
