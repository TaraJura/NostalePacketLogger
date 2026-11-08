"""Clientless (headless) NosTale session.

Protocol work ported from NosBazar (MIT License, Copyright (c) 2026
NosBazar contributors): ``NoS0577`` login packet, ``NsTeST`` world list,
login/world framing and the world handshake. The Python session, engine
integration and CLI are ours.

Flow (matches the real client)::

    Gameforge session token (hex)
      -> login server (login cipher, 0x0A packets)
      -> NoS0577 <token>  NONE_CII <random> 0\\x0B<version> 0 <md5>
      -> NsTeST (session id, account name, world servers)
      -> world server (world cipher, 0xFF packets)
      -> "<session id>", "<account> GF 0", "thisisgfmode"
      -> clist / clist_end -> select <slot>, game_start, lbs 0
      -> ok -> game packets, fed into ntpl.engine.Engine
      -> pulse <seconds> 0 every 60 s; "0" keepalive after 10 s idle

The session token is the *hexlified* Gameforge thin token (the same value
the real client passes in NoS0577). It is read from --token/--token-file or
NTPL_HEADLESS_TOKEN and is never logged. Credentials are not handled here:
obtain the token with the launcher auth path, then hand it to this module.
"""

from __future__ import annotations

import hashlib
import os
import random
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from . import noscrypto
from .engine import Engine
from .protocol import Kind, Packet

#: EU login server, read from the game client's own strings and verified
#: live (79.110.84.75:4000 accepts the login connection; 4001 is closed).
DEFAULT_LOGIN_HOST = "79.110.84.75"
DEFAULT_LOGIN_PORT = 4000
#: The client's installation id in NoS0577 (the live client sends this).
DEFAULT_INSTALLATION_ID = "NONE_CII"
DEFAULT_REGION = "0"
#: World list sentinel that ends the server list.
SERVER_LIST_END = "-1:-1:-1:10000.10000.1"
KEEPALIVE_IDLE_S = 10.0
PULSE_INTERVAL_S = 60.0
LOGIN_TIMEOUT_S = 15.0
WORLD_TIMEOUT_S = 10.0


class LoginError(RuntimeError):
    """The login server refused the session or the token is invalid."""


@dataclass
class WorldServer:
    ip: str
    port: int
    capacity: int
    server_id: int
    channel: int
    name: str

    @classmethod
    def parse(cls, value: str) -> "WorldServer | None":
        try:
            ip, port, capacity, tail = value.split(":", 3)
            server_id, channel, name = tail.split(".", 2)
            return cls(ip, int(port), int(capacity), int(server_id),
                       int(channel), name)
        except ValueError:
            return None


@dataclass
class LoginResult:
    world: WorldServer
    session_id: int
    account_name: str
    servers: list[WorldServer] = field(default_factory=list)


class TokenReader:
    """Mimic NosBazar's strings::token: empty fields are returned, not skipped."""

    def __init__(self, text: str) -> None:
        self.text = text

    def next(self, delimiter: str = " ") -> str:
        index = self.text.find(delimiter)
        if index < 0:
            value, self.text = self.text, ""
            return value
        value = self.text[:index]
        self.text = self.text[index + 1:]
        return value


def parse_nstest(packet: str) -> LoginResult:
    """Parse the login server's NsTeST packet.

    Layout (NosBazar): header, unknown, blank, account name, 121 ignored
    fields, session id, then ``ip:port:capacity:id.channel.name`` entries
    until ``-1:-1:-1:10000.10000.1``.
    """
    reader = TokenReader(packet)
    reader.next()  # NsTeST
    reader.next()  # unknown
    reader.next()  # blank
    account_name = reader.next()
    for _ in range(121):
        reader.next()
    try:
        session_id = int(reader.next())
    except ValueError as exc:
        raise LoginError("NsTeST has no numeric session id") from exc
    servers: list[WorldServer] = []
    while True:
        item = reader.next()
        if not item or item == SERVER_LIST_END:
            break
        server = WorldServer.parse(item)
        if server is not None:
            servers.append(server)
    if not servers:
        raise LoginError("NsTeST contains no world servers")
    return LoginResult(
        world=servers[0],
        session_id=session_id,
        account_name=account_name,
        servers=servers,
    )


def build_no_s0577(
    token_hex: str,
    client_version: str,
    client_hash: str,
    installation_id: str = DEFAULT_INSTALLATION_ID,
    region: str = DEFAULT_REGION,
) -> str:
    """The login packet the real client sends (NosBazar format)."""
    random_hex = "".join(random.choice("0123456789abcdef") for _ in range(8))
    return (
        f"NoS0577 {token_hex}  {installation_id} {random_hex} "
        f"{region}\x0B{client_version} 0 {client_hash}"
    )


class LoginSession:
    """Login channel: XOR/shift transform, newline-delimited packets."""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self._buffer = bytearray()

    def send(self, text: str) -> None:
        self.sock.sendall(noscrypto.login_encrypt(text.encode("latin-1")))

    def packets(self) -> Iterator[str]:
        while True:
            try:
                chunk = self.sock.recv(65536)
            except OSError:
                return
            if not chunk:
                return
            self._buffer += noscrypto.login_decrypt(chunk)
            while True:
                index = self._buffer.find(b"\n")
                if index < 0:
                    break
                line = bytes(self._buffer[:index])
                del self._buffer[:index + 1]
                if line:
                    yield line.decode("latin-1", "replace")


class WorldSession:
    """World channel: packed + session-XOR, 0xFF-delimited packets."""

    def __init__(self, sock: socket.socket, session_id: int) -> None:
        self.sock = sock
        self.session_id = session_id & 0xFFFF
        self.counter = random.randint(0, 0xFFFF)
        self.first = True
        self.last_send = time.monotonic()
        self._buffer = bytearray()

    def send(self, text: str) -> None:
        body = f"{self.counter} {text}".encode("latin-1")
        self.sock.sendall(
            noscrypto.world_encrypt(body, self.session_id, self.first)
        )
        self.first = False
        self.counter = (self.counter + 1) % 0xFFFF
        self.last_send = time.monotonic()

    def packets(self) -> Iterator[str]:
        while True:
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                chunk = None
            except OSError:
                return
            if chunk == b"":
                return
            if chunk:
                self._buffer += chunk
            while True:
                index = self._buffer.find(b"\xFF")
                if index < 0:
                    break
                raw = bytes(self._buffer[:index])
                del self._buffer[:index + 1]
                packet = noscrypto.world_decrypt(raw).decode(
                    "latin-1", "replace"
                )
                if packet.endswith("\n"):
                    packet = packet[:-1]
                yield packet
            if chunk is None:
                # Timeout: let the caller run the keepalive/pulse timers.
                yield ""


def file_version(path: Path) -> str:
    """The PE's FileVersion string (the client sends it in NoS0577)."""
    data = path.read_bytes()
    pattern = "FileVersion".encode("utf-16-le")
    index = data.find(pattern)
    if index < 0:
        return ""
    start = index + len(pattern)
    result = bytearray()
    for value in data[start:start + 60]:
        if value == ord('"'):
            break
        if 32 <= value <= 126:
            result.append(value)
    return result.decode("ascii", "replace")


def file_md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def client_identity(game_dir: Path) -> tuple[str, str]:
    """(client version, NoS0577 client hash) from the installed game files."""
    version = file_version(game_dir / "NostaleClientX.exe")
    combined = file_md5(game_dir / "NostaleClientX.exe") + file_md5(
        game_dir / "NostaleClient.exe"
    )
    login_hash = hashlib.md5(combined.encode("ascii")).hexdigest().upper()
    return version, login_hash


def normalize_token(value: str) -> str:
    """Accept the hex token or a raw launcher code (hexlify the latter).

    The Gameforge thin ``code`` is a UUID-like string; NoS0577 carries its
    hexlified ASCII bytes. A value that is already even-length hex is kept.
    """
    value = value.strip()
    if value and len(value) % 2 == 0 and all(
        character in "0123456789abcdefABCDEF" for character in value
    ):
        return value.lower()
    import binascii

    return binascii.hexlify(value.encode("latin-1")).decode("ascii")


def read_token(token: str | None, token_file: str | None) -> str:
    """Resolve the hex session token without ever logging it."""
    if token:
        return normalize_token(token)
    env = os.environ.get("NTPL_HEADLESS_TOKEN")
    if env:
        return normalize_token(env)
    if token_file:
        return normalize_token(
            Path(token_file).read_text(encoding="ascii")
        )
    raise LoginError(
        "no session token: pass --token, --token-file or "
        "NTPL_HEADLESS_TOKEN (hex Gameforge thin token or raw launcher code)"
    )


class Clientless:
    """One headless game session: login, world, character, packet loop."""

    def __init__(
        self,
        token_hex: str,
        game_dir: Path,
        login_host: str = DEFAULT_LOGIN_HOST,
        login_port: int = DEFAULT_LOGIN_PORT,
        region: str = DEFAULT_REGION,
        character: int | None = None,
        world_id: int | None = None,
        channel: int | None = None,
        on_packet: Callable[[str], None] | None = None,
    ) -> None:
        self.token_hex = token_hex
        self.game_dir = game_dir
        self.login_host = login_host
        self.login_port = login_port
        self.region = region
        self.character = character
        self.world_id = world_id
        self.channel = channel
        self.on_packet = on_packet or (lambda _text: None)
        self.engine = Engine()
        self.version, self.client_hash = client_identity(game_dir)
        self.login_result: LoginResult | None = None
        self._stop = False
        self._first_char = -1
        self._game_started = False
        self._pulse_at = 0.0
        self._pulse_seconds = 0

    # -- transport ------------------------------------------------------

    def login(self) -> LoginResult:
        sock = socket.create_connection(
            (self.login_host, self.login_port), timeout=LOGIN_TIMEOUT_S
        )
        sock.settimeout(LOGIN_TIMEOUT_S)
        session = LoginSession(sock)
        session.send(
            build_no_s0577(
                self.token_hex, self.version, self.client_hash,
                region=self.region,
            )
        )
        result: LoginResult | None = None
        for packet in session.packets():
            if packet.startswith("NsTeST"):
                result = parse_nstest(packet)
                break
            if packet.startswith(("NoS0577", "failc", "error")):
                raise LoginError(f"login server: {packet[:120]}")
        try:
            sock.close()
        except OSError:
            pass
        if result is None:
            raise LoginError("login server closed without NsTeST")
        self.login_result = result
        return result

    def _choose_world(self, result: LoginResult) -> WorldServer:
        for server in result.servers:
            if self.world_id is not None and server.server_id != self.world_id:
                continue
            if self.channel is not None and server.channel != self.channel:
                continue
            return server
        return result.world

    def world(self, result: LoginResult) -> None:
        server = self._choose_world(result)
        sock = socket.create_connection(
            (server.ip, server.port), timeout=WORLD_TIMEOUT_S
        )
        sock.settimeout(1.0)
        session = WorldSession(sock, result.session_id)
        self._world_session = session
        session.send(str(result.session_id))
        time.sleep(1.0)
        session.send(f"{result.account_name} GF 0")
        session.send("thisisgfmode")
        try:
            for packet in session.packets():
                if self._stop:
                    break
                if not packet:
                    self._timers(session)
                    continue
                self.on_packet(packet)
                self._handle(packet, session)
                if self._game_started:
                    self.engine.feed(
                        Packet(Kind.RECV, packet, timestamp=time.time())
                    )
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def _timers(self, session: WorldSession) -> None:
        now = time.monotonic()
        if self._game_started and now - self._pulse_at >= PULSE_INTERVAL_S:
            self._pulse_at = now
            self._pulse_seconds += int(PULSE_INTERVAL_S)
            session.send(f"pulse {self._pulse_seconds} 0")
        if now - session.last_send >= KEEPALIVE_IDLE_S:
            session.send("0")

    def _handle(self, packet: str, session: WorldSession) -> None:
        if packet == "ok":
            self._game_started = True
            self._pulse_at = time.monotonic()
            self._pulse_seconds = 0
            return
        if packet.startswith("clist "):
            reader = TokenReader(packet)
            reader.next()
            try:
                index = int(reader.next())
            except ValueError:
                return
            if self._first_char < 0:
                self._first_char = index
            else:
                self._first_char = min(self._first_char, index)
            return
        if packet == "clist_end":
            slot = self.character
            if slot is None:
                slot = max(0, self._first_char)
            session.send(f"select {slot}")
            session.send("game_start")
            session.send("lbs 0")
            return

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        result = self.login()
        self.world(result)


def run_headless(
    token: str | None,
    token_file: str | None,
    game_dir: str,
    login_host: str = DEFAULT_LOGIN_HOST,
    login_port: int = DEFAULT_LOGIN_PORT,
    region: str = DEFAULT_REGION,
    character: int | None = None,
    world_id: int | None = None,
    channel: int | None = None,
    print_packets: bool = False,
    email: str | None = None,
    password: str | None = None,
    account_id: str | None = None,
    locale: str = "en-US",
    identity: str | None = None,
    show_password: bool = False,
) -> int:
    directory = Path(game_dir).expanduser()
    if not (directory / "NostaleClientX.exe").exists():
        raise LoginError(
            f"{directory} does not contain NostaleClientX.exe; pass --game-dir"
        )
    email = email or os.environ.get("NTPL_EMAIL")
    password = password or os.environ.get("NTPL_PASSWORD")
    token_error: LoginError | None = None
    try:
        session_token = read_token(token, token_file)
    except LoginError as exc:
        token_error = exc
        session_token = ""
    if not session_token and not (email and password):
        if os.isatty(0):
            print("NosTale headless login (credentials are not stored)")
            email = input("Gameforge email: ").strip()
            if email:
                if show_password:
                    password = input("Password (visible): ")
                else:
                    import getpass

                    password = getpass.getpass(
                        "Password (hidden; --show-password shows it): "
                    )
    if not session_token:
        if not (email and password):
            print(f"ntpl headless: {token_error}")
            return 2
        from .gameforge_auth import (
            AuthError,
            _open_image,
            challenge_info,
            mint_token,
            solve_image_drop,
        )

        def on_challenge(exc, session):
            info = challenge_info(exc.challenge_id, session=session)
            exc.kind = info.get("type", "")
            exc.script = info.get("script", "")
            print(f"Gameforge login check: {exc.kind or 'unknown'}")
            if exc.kind == "gf-image-drop-captcha":
                def ask(paths):
                    try:
                        from .captcha_ui import ask_captcha

                        return ask_captcha(paths)
                    except Exception as error:  # noqa: BLE001 - no GTK/display
                        print(
                            f"captcha window unavailable ({error}); "
                            f"images: {paths}"
                        )
                        for path in paths.values():
                            _open_image(path)
                        return input(
                            "Icon number that matches the text "
                            "(1 = first icon): "
                        )

                return solve_image_drop(
                    exc.challenge_id, locale, session, ask
                )
            print(
                "This challenge type needs a browser; use a launcher token."
            )
            return False

        try:
            session_token = mint_token(
                email,
                password,
                Path(identity).expanduser() if identity else None,
                account_id=account_id,
                locale=locale,
                on_challenge=on_challenge,
            )
        except AuthError as exc:
            print(f"ntpl headless: {exc}")
            print(
                "ntpl headless: if the browser check failed, install "
                "Chrome/Chromium or set NTPL_CHROME; a launcher token "
                "always works (--token-file)."
            )
            return 2
        print("ntpl headless: login OK")
    client = Clientless(
        session_token,
        directory,
        login_host=login_host,
        login_port=login_port,
        region=region,
        character=character,
        world_id=world_id,
        channel=channel,
        on_packet=(print if print_packets else None),
    )
    print(
        f"ntpl headless: login {login_host}:{login_port}, "
        f"client {client.version or 'unknown'}"
    )
    try:
        result = client.login()
        print(
            f"ntpl headless: world {result.world.ip}:{result.world.port} "
            f"({result.world.name}, channel {result.world.channel}), "
            f"account {result.account_name}"
        )
        client.world(result)
    except KeyboardInterrupt:
        client.stop()
        return 130
    except (LoginError, OSError) as exc:
        print(f"ntpl headless: {exc}")
        return 1
    return 0
