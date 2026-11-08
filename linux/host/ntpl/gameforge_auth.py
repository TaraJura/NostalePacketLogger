"""Current Gameforge login (v2 credentials + iovation + thin code).

Ported from NosBazar's auth flow (MIT, Copyright (c) 2026 NosBazar
contributors): the 30-value device blackbox, the v2 credentials endpoint,
iovation enrollment and the thin/codes call that yields the NoS0577 game
token. The device identity is normally captured from a real Gameforge
client (blackbox extractor); a generated identity is provided as a
fallback but Gameforge can reject it as CREDENTIALS_INVALID. Credentials
and tokens are never written or logged by this module.

The caller supplies email/password at runtime (terminal prompt or env).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

GAME_ID = "dd4e22d6-00d1-44b9-8126-d8b40e0cd7c9"
LOCALE = "en-US"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/72.0.3626.121 Safari/537.36"
)
_SAFE = set(
    "-_!~*.'()"
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
)


class AuthError(RuntimeError):
    """Gameforge refused the login or the token chain."""

    def __init__(self, message: str, codes: list[str] | None = None) -> None:
        super().__init__(message)
        self.codes = codes or []


class ChallengeRequired(AuthError):
    """The account needs Gameforge's human check before the login passes."""

    def __init__(
        self,
        challenge_id: str,
        url: str,
        codes: list[str] | None = None,
    ) -> None:
        super().__init__(
            "Gameforge requires a captcha challenge", codes
        )
        self.challenge_id = challenge_id
        self.url = url
        self.kind = ""
        self.script = ""


def default_prefix() -> Path:
    return Path.home() / "games" / "gameforge"


def default_installation_id() -> str:
    override = os.environ.get("NTPL_GF_INSTALLATION_ID")
    if override:
        return override
    registry = default_prefix() / "prefix" / "user.reg"
    try:
        text = registry.read_text(errors="replace")
    except OSError:
        return str(uuid.uuid4())
    match = re.search(r'"InstallationId"\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else str(uuid.uuid4())


def default_cert_path() -> Path | None:
    override = os.environ.get("NTPL_GF_CERT")
    if override:
        path = Path(override).expanduser()
        return path if path.exists() else None
    candidates = [
        Path.home() / "games/gameforge/nosbazar/backend/assets/cert.pem",
        Path.home() / "games/gameforge/GflessClient/Launcher/resources/"
        "all_certs.pem",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def default_identity_path() -> Path | None:
    override = os.environ.get("NTPL_GF_IDENTITY")
    if override:
        path = Path(override).expanduser()
        return path if path.exists() else None
    path = Path.home() / ".config" / "ntpl" / "identity.json"
    return path if path.exists() else None


def load_identity(path: Path | None = None) -> list:
    """Return the 30 blackbox values.

    A captured identity JSON (a 30-element list, 0600) is used when present.
    Otherwise a structurally valid generated identity is returned; Gameforge
    may reject it, which is reported as an AuthError.
    """
    path = path or default_identity_path()
    if path is not None:
        try:
            values = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            values = None
        if isinstance(values, list) and len(values) == 30:
            return values
    return generated_identity()


def _server_time() -> str:
    try:
        response = requests.get(
            "https://gameforge.com/tra/game1.js", timeout=10
        )
        date = parsedate_to_datetime(response.headers["Date"])
        return date.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )
    except Exception:  # noqa: BLE001 - fall back to the local clock
        return datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )


def generated_identity(extra_payload=None) -> list:
    now_ms = int(time.time() * 1000)
    iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    client_id = base64.b64encode(
        "".join(
            random.choice("abcdefghijklmnopqrstuvwxyz0123456789")
            for _ in range(27)
        ).encode()
    ).decode().lower()
    vector = base64.b64encode(
        (
            "".join(chr(random.randint(33, 126)) for _ in range(100))
            + " "
            + str(now_ms)
        ).encode()
    ).decode()
    hashes = [
        hashlib.sha256(str(random.random()).encode()).hexdigest()[:32]
        for _ in range(8)
    ]
    return [
        12, "60", "Windows", "Chrome", "Google Inc.", 8, 8, "en-US",
        hashes[0], "Google Inc. (NVIDIA)", hashes[1], hashes[2],
        1920, 1080, hashes[3], hashes[4], hashes[5], hashes[6],
        0, hashes[7], 0, iso, client_id, 200, "10", vector,
        BROWSER_USER_AGENT, _server_time(), extra_payload, 0,
    ]


def _percent_encode(value: str) -> str:
    out = []
    for char in value:
        if char in _SAFE:
            out.append(char)
        else:
            for byte in char.encode("utf-8"):
                out.append(f"%{byte:02X}")
    return "".join(out)


def blackbox(identity: list, extra_payload=None) -> str:
    values = list(identity)
    if len(values) == 30:
        values[28] = extra_payload
    raw = json.dumps(values, separators=(",", ":"))
    encoded = _percent_encode(raw)
    data = bytearray()
    total = 0
    for char in encoded:
        total = (total + ord(char)) & 0xFF
        data.append(total)
    return "tra:" + base64.urlsafe_b64encode(bytes(data)).decode().rstrip("=")


def _ua_magic(
    account_id: str, installation: str, cert: bytes, chrome_version: str
) -> str:
    first = next((c for c in installation if c.isdigit()), None)
    if first is None or int(first) % 2 == 0:
        cert_h = hashlib.sha256(cert).hexdigest()
        version_h = hashlib.sha1(chrome_version.encode()).hexdigest()
        install_h = hashlib.sha256(installation.encode()).hexdigest()
        account_h = hashlib.sha1(account_id.encode()).hexdigest()
        total = hashlib.sha256(
            (cert_h + version_h + install_h + account_h).encode()
        ).hexdigest()
        return account_id[:2] + total[:8]
    cert_h = hashlib.sha1(cert).hexdigest()
    version_h = hashlib.sha256(chrome_version.encode()).hexdigest()
    install_h = hashlib.sha1(installation.encode()).hexdigest()
    account_h = hashlib.sha256(account_id.encode()).hexdigest()
    total = hashlib.sha256(
        (cert_h + version_h + install_h + account_h).encode()
    ).hexdigest()
    return account_id[:2] + total[-8:]


def _encrypted(
    identity: list, account_id: str, gsid: str, installation: str
) -> str:
    payload = {
        "features": [random.randint(1, 2**31 - 1)],
        "installation": installation,
        "session": gsid.rsplit("-", 1)[0],
    }
    plain = blackbox(identity, payload).encode()
    key = hashlib.sha512((gsid + "-" + account_id).encode()).hexdigest()
    out = bytearray()
    for index, value in enumerate(plain):
        k = index % len(key)
        out.append(value ^ ord(key[k]) ^ ord(key[len(key) - 1 - k]))
    return base64.b64encode(bytes(out)).decode()


def _codes(response) -> list[str]:
    try:
        body = response.json()
    except ValueError:
        return []
    error_types = body.get("errorTypes")
    if isinstance(error_types, dict):
        return list(error_types.keys())
    if isinstance(error_types, list):
        return [str(item) for item in error_types]
    message = body.get("message")
    return [str(message)] if message else []


def login(
    email: str,
    password: str,
    identity: list,
    installation: str,
    locale: str = LOCALE,
    session: requests.Session | None = None,
) -> str:
    """Return the bearer token, or raise AuthError with Gameforge's codes."""
    session = session or requests.Session()
    headers = {
        "User-Agent": BROWSER_USER_AGENT,
        "TNT-Installation-Id": installation,
        "gf-installation-id": installation,
        "Content-Type": "application/json",
    }
    response = session.post(
        "https://spark.gameforge.com/api/v2/authProviders/"
        "credentials/sessions",
        headers=headers,
        json={
            "blackbox": blackbox(identity),
            "email": email,
            "locale": locale,
            "password": password,
        },
        timeout=30,
    )
    if response.status_code == 201:
        token = response.json().get("token")
        if token:
            return token
    codes = _codes(response)
    if "CHALLENGE_REQUIRED" in codes:
        header = response.headers.get("gf-challenge-id", "")
        challenge_id = ""
        url = "https://challenge.gameforge.com"
        try:
            body = response.json()
        except ValueError:
            body = {}
        challenge_id = body.get("challengeId") or (
            header.split(";", 1)[0] if ";" in header else header
        )
        if ";" in header:
            url = header.split(";", 1)[1]
        raise ChallengeRequired(challenge_id, url, codes)
    raise AuthError(
        "Gameforge login rejected"
        + (f": {', '.join(codes)}" if codes else ""),
        codes,
    )


def game_accounts(bearer: str, installation: str) -> list[str]:
    headers = {
        "User-Agent": BROWSER_USER_AGENT,
        "TNT-Installation-Id": installation,
        "Authorization": "Bearer " + bearer,
    }
    response = requests.get(
        "https://spark.gameforge.com/api/v1/user/accounts",
        headers=headers,
        timeout=30,
    )
    if response.status_code != 200:
        raise AuthError(f"cannot list game accounts ({response.status_code})")
    accounts = response.json()
    nostale = [
        key
        for key, value in accounts.items()
        if value.get("guls", {}).get("game") == "nostale"
    ]
    if not nostale:
        raise AuthError("no NosTale game account on this Gameforge account")
    return nostale


def challenge_info(
    challenge_id: str, session: requests.Session | None = None
) -> dict:
    session = session or requests.Session()
    response = session.get(
        f"https://challenge.gameforge.com/challenge/{challenge_id}",
        headers={
            "User-Agent": BROWSER_USER_AGENT,
            "Origin": "spark://www.gameforge.com",
        },
        timeout=20,
    )
    if response.status_code != 200:
        raise AuthError(
            f"Gameforge challenge info failed ({response.status_code})"
        )
    return response.json()


def _open_image(path: Path) -> None:
    try:
        subprocess.Popen(
            ["xdg-open", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def solve_image_drop(
    challenge_id: str,
    locale: str,
    session: requests.Session,
    ask,
) -> bool:
    """Fetch the text/icons/target images, let the user pick, post the answer.

    ``ask(paths)`` receives the saved images (``{"text", "icons", "target"}``)
    and returns the 1-based icon number the user chose, or None to cancel;
    the API expects the 0-based index (the launcher does ``spinbox - 1``).
    """
    base = (
        "https://image-drop-challenge.gameforge.com/challenge/"
        f"{challenge_id}/{locale}"
    )
    headers = {
        "User-Agent": BROWSER_USER_AGENT,
        "Origin": "spark://www.gameforge.com",
        "Accept": "*/*",
        "Connection": "Keep-Alive",
    }
    directory = (
        Path.home() / ".cache" / "ntpl" / f"captcha-{challenge_id[:8]}"
    )
    directory.mkdir(parents=True, exist_ok=True)

    def fetch(last=None):
        response = session.get(base, headers=headers, timeout=20)
        if response.status_code != 200:
            raise AuthError(
                f"captcha fetch failed ({response.status_code})"
            )
        if last is None:
            last = response.json().get("lastUpdated", "")
        for remote, name in (
            ("text", "text"),
            ("drag-icons", "icons"),
            ("drop-target", "target"),
        ):
            image = session.get(
                f"{base}/{remote}?{last}",
                headers={
                    **headers,
                    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                },
                timeout=20,
            )
            if image.status_code == 200:
                suffix = (
                    "webp"
                    if "webp" in image.headers.get("Content-Type", "")
                    else "png"
                )
                path = directory / f"{name}.{suffix}"
                path.write_bytes(image.content)
                paths[name] = path
        return last

    paths: dict[str, Path] = {}
    fetch()
    for attempt in range(3):
        answer = ask(paths)
        if answer is None:
            return False
        try:
            value = int(answer) - 1
        except (TypeError, ValueError):
            print("enter the icon number")
            continue
        response = session.post(
            base,
            headers={**headers, "Content-Type": "application/json"},
            json={"answer": value},
            timeout=20,
        )
        data = response.json() if response.status_code == 200 else {}
        if data.get("status") == "solved":
            return True
        print("wrong icon; the images refresh")
        fetch(data.get("lastUpdated"))
    return False


def mint_code(
    bearer: str,
    account_id: str,
    identity: list,
    installation: str,
    cert: bytes,
) -> str:
    headers = {
        "User-Agent": BROWSER_USER_AGENT,
        "TNT-Installation-Id": installation,
        "Authorization": "Bearer " + bearer,
        "Content-Type": "application/json",
    }
    time.sleep(1.0)
    response = requests.post(
        "https://spark.gameforge.com/api/v1/auth/iovation",
        headers=headers,
        json={
            "accountId": account_id,
            "blackbox": blackbox(identity),
            "type": "play_now",
        },
        timeout=30,
    )
    if response.status_code != 200 or response.json().get("status") != "ok":
        raise AuthError(f"iovation failed ({response.status_code})")
    time.sleep(2.0)
    try:
        info = requests.get(
            "http://dl.tnt.gameforge.com/tnt/final-ms3/"
            "clientversioninfo.json",
            timeout=10,
        ).json()
        chrome_version = "C" + info["version"]
    except Exception:  # noqa: BLE001 - the magic only needs a version
        chrome_version = "C2.2.19.1700"
    gsid = str(uuid.uuid4()) + "-" + str(random.randint(1000, 9999))
    code_headers = {
        "Content-Type": "application/json",
        "User-Agent": (
            f"Chrome/{chrome_version} "
            f"({_ua_magic(account_id, installation, cert, chrome_version)})"
        ),
        "Authorization": "Bearer " + bearer,
        "tnt-installation-id": installation,
    }
    response = requests.post(
        "https://spark.gameforge.com/api/v1/auth/thin/codes",
        headers=code_headers,
        json={
            "platformGameAccountId": account_id,
            "gsid": gsid,
            "blackbox": _encrypted(identity, account_id, gsid, installation),
            "gameId": GAME_ID,
        },
        timeout=30,
    )
    if response.status_code != 201:
        raise AuthError(f"thin/codes failed ({response.status_code})")
    code = response.json().get("code")
    if not code:
        raise AuthError("thin/codes returned no code")
    return code


def mint_token(
    email: str,
    password: str,
    identity_path: Path | None = None,
    account_id: str | None = None,
    locale: str = LOCALE,
    on_challenge=None,
) -> str:
    """Full chain: email/password -> bearer -> iovation -> thin code (hex).

    ``on_challenge(exc, session)`` may solve Gameforge's human check
    (image-drop captcha) and return True; the login is then retried.
    """
    installation = default_installation_id()
    identity = load_identity(identity_path)
    session = requests.Session()
    for _ in range(3):
        try:
            bearer = login(
                email,
                password,
                identity,
                installation,
                locale=locale,
                session=session,
            )
            break
        except ChallengeRequired as exc:
            if on_challenge is None or not on_challenge(exc, session):
                raise
    else:
        raise AuthError("the Gameforge challenge was not solved")
    accounts = game_accounts(bearer, installation)
    chosen = account_id if account_id in accounts else accounts[0]
    cert_path = default_cert_path()
    if cert_path is None:
        raise AuthError(
            "no Gameforge certificate for the thin/codes check; set "
            "NTPL_GF_CERT"
        )
    code = mint_code(
        bearer, chosen, identity, installation, cert_path.read_bytes()
    )
    return code.encode("latin-1").hex()
