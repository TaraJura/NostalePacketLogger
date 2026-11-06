"""Wire protocol between the injected DLL and the host.

Every message is one UTF-8 line.  The first ``|`` separates the type from the
payload; the payload may itself contain ``|``.

    RECV|<packet>      game -> server packet, already decrypted by the game
    SEND|<packet>      client -> game packet, or a host -> game injection
    STATUS|<text>      human readable state, hook addresses, drop counters
    QUIT|              either side asks for a clean disconnect

The SEND ambiguity is inherited from the original named-pipe protocol: the DLL
uses SEND for packets the game itself sent, and the host uses SEND to ask the
DLL to inject one.  Direction is decided by which side you are, not by the
message.

The transport is TCP on 127.0.0.1 (loopback only).  Why not keep named pipes:
a Wine named pipe is not reachable from a native Linux process, while a
Winsock socket inside Wine is an ordinary Linux socket and a native Python
process can connect to it directly.  The same DLL build also works on real
Windows, so one transport covers both platforms.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from enum import Enum

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 47821

#: The DLL never listens on anything else; the host never connects elsewhere.
MAX_LINE_BYTES = 64 * 1024


class ProtocolError(ValueError):
    """A line that is not a well-formed protocol message."""


class Kind(str, Enum):
    RECV = "RECV"
    SEND = "SEND"
    STATUS = "STATUS"
    QUIT = "QUIT"


def default_port() -> int:
    """NTPL_PORT from the environment, or 47821.

    The same variable is read by the DLL, so setting it once in the shell that
    launches both the game and the host keeps them in sync.
    """
    raw = os.environ.get("NTPL_PORT", "")
    try:
        port = int(raw)
    except ValueError:
        return DEFAULT_PORT
    return port if 1 <= port <= 65535 else DEFAULT_PORT


@dataclass(frozen=True, slots=True)
class Message:
    kind: Kind
    payload: str
    raw: str = field(default="", compare=False)

    @classmethod
    def parse(cls, line: str) -> "Message | None":
        """Parse one line. Blank lines return None; malformed lines raise."""
        if isinstance(line, bytes):
            line = line.decode("utf-8", "replace")

        line = line.rstrip("\r\n")
        if not line:
            return None

        kind_text, separator, payload = line.partition("|")
        if not separator:
            raise ProtocolError(f"missing '|' in {line!r}")

        try:
            kind = Kind(kind_text)
        except ValueError as exc:
            raise ProtocolError(f"unknown message type {kind_text!r}") from exc

        return cls(kind=kind, payload=payload, raw=line)

    def encode(self) -> str:
        return f"{self.kind.value}|{self.payload}"


@dataclass(frozen=True, slots=True)
class Packet:
    """One game packet, with the fields the engine and the UI care about."""

    direction: Kind
    text: str
    timestamp: float

    @classmethod
    def from_message(cls, message: Message, timestamp: float | None = None) -> "Packet":
        if message.kind not in (Kind.RECV, Kind.SEND):
            raise ProtocolError(f"{message.kind.value} is not a packet")
        return cls(
            direction=message.kind,
            text=message.payload,
            timestamp=time.time() if timestamp is None else timestamp,
        )

    @property
    def opcode(self) -> str:
        return self.text.split(" ", 1)[0] if self.text else ""

    @property
    def args(self) -> list[str]:
        parts = self.text.split()
        return parts[1:] if parts else []

    def ints(self, count: int) -> list[int] | None:
        """First `count` arguments as ints, or None when the shape is wrong."""
        if len(self.args) < count:
            return None
        try:
            return [int(value) for value in self.args[:count]]
        except ValueError:
            return None

    def __str__(self) -> str:
        stamp = time.strftime("%H:%M:%S", time.localtime(self.timestamp))
        millis = int(self.timestamp * 1000) % 1000
        return f"{stamp}.{millis:03d} [{self.direction.value}] {self.text}"
