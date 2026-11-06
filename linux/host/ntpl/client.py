"""TCP client for the injected PacketLogger DLL.

The DLL listens on loopback and accepts one host at a time. This client can be
used in two ways:

* pull style -- call :meth:`PacketClient.recv` in your own loop;
* push style -- call :meth:`PacketClient.messages`, which starts a background
  reader thread and yields :class:`~ntpl.protocol.Message` objects (used by the
  CLI and the GTK GUI).

Only the standard library is used.
"""

from __future__ import annotations

import queue
import select
import socket
import threading
import time
from collections.abc import Iterator

from .protocol import (
    DEFAULT_HOST,
    MAX_LINE_BYTES,
    Kind,
    Message,
    ProtocolError,
    default_port,
)


class ConnectionLost(ConnectionError):
    """The DLL closed the socket, or the connection never came up."""


class PacketClient:
    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int | None = None,
        connect_timeout: float = 2.0,
    ) -> None:
        self.host = host
        self.port = default_port() if port is None else port
        self.connect_timeout = connect_timeout

        self._socket: socket.socket | None = None
        self._buffer = bytearray()
        self._send_lock = threading.Lock()
        self._inbox: queue.Queue[Message | None] = queue.Queue()
        self._reader: threading.Thread | None = None
        self._closed = threading.Event()

    # -- connection -----------------------------------------------------

    def connect(self, retries: int = 30, delay: float = 0.2) -> None:
        """Connect, retrying while the game/DLL is still starting.

        The DLL starts listening a moment after injection; the default retry
        budget is six seconds. Raise :class:`ConnectionLost` when it never
        appears.
        """
        last_error: OSError | None = None
        attempts = max(1, retries)

        for attempt in range(attempts):
            try:
                sock = socket.create_connection(
                    (self.host, self.port), timeout=self.connect_timeout
                )
            except OSError as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    time.sleep(delay)
                continue

            sock.settimeout(None)
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass

            self._socket = sock
            self._buffer.clear()
            self._closed.clear()
            return

        raise ConnectionLost(
            f"cannot connect to {self.host}:{self.port} after {attempts} "
            f"attempt(s): {last_error}"
        )

    @property
    def connected(self) -> bool:
        return self._socket is not None and not self._closed.is_set()

    def close(self) -> None:
        self._closed.set()

        sock, self._socket = self._socket, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

        self._inbox.put(None)

    def __enter__(self) -> "PacketClient":
        if not self.connected:
            self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- sending --------------------------------------------------------

    def send_raw(self, line: str) -> None:
        """Send one already-framed protocol line (newline added here)."""
        sock = self._socket
        if sock is None or self._closed.is_set():
            raise ConnectionLost("not connected")

        framed = (line.rstrip("\r\n") + "\n").encode("utf-8")
        if len(framed) > MAX_LINE_BYTES:
            raise ProtocolError("line too long")

        try:
            with self._send_lock:
                sock.sendall(framed)
        except OSError as exc:
            self.close()
            raise ConnectionLost(f"send failed: {exc}") from exc

    def send_packet(self, packet: str) -> None:
        """Ask the DLL to push one packet into the game."""
        text = packet.strip()
        if not text:
            raise ValueError("empty packet")
        self.send_raw(f"SEND|{text}")

    def request_status(self) -> None:
        self.send_raw("STATUS")

    def quit(self) -> None:
        """Ask the DLL to unhook, stop and unload itself."""
        self.send_raw("QUIT|")

    # -- receiving ------------------------------------------------------

    def _take_line(self) -> str | None:
        newline = self._buffer.find(b"\n")
        if newline < 0:
            if len(self._buffer) > MAX_LINE_BYTES:
                self.close()
                raise ConnectionLost("peer sent more than 64 KiB without a newline")
            return None

        raw = bytes(self._buffer[:newline])
        del self._buffer[: newline + 1]
        return raw.decode("utf-8", "replace").rstrip("\r")

    def recv(self, timeout: float | None = None) -> Message | None:
        """Return the next message, or None on timeout.

        Raises :class:`ConnectionLost` when the peer closes the socket.
        """
        sock = self._socket
        if sock is None or self._closed.is_set():
            raise ConnectionLost("not connected")

        deadline = None if timeout is None else time.monotonic() + timeout

        while True:
            line = self._take_line()
            if line is not None:
                message = Message.parse(line)
                if message is not None:
                    return message
                continue

            remaining = None
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None

            try:
                readable, _, _ = select.select([sock], [], [], remaining)
            except (OSError, ValueError) as exc:
                self.close()
                raise ConnectionLost(f"select failed: {exc}") from exc

            if not readable:
                return None

            try:
                chunk = sock.recv(65536)
            except OSError as exc:
                self.close()
                raise ConnectionLost(f"recv failed: {exc}") from exc

            if not chunk:
                self.close()
                raise ConnectionLost("peer closed the connection")

            self._buffer.extend(chunk)

    # -- background reader ---------------------------------------------

    def _reader_loop(self) -> None:
        try:
            while not self._closed.is_set():
                message = self.recv(timeout=0.5)
                if message is not None:
                    self._inbox.put(message)
        except (ConnectionLost, ProtocolError):
            pass
        finally:
            self._inbox.put(None)

    def messages(self) -> Iterator[Message]:
        """Yield messages until the connection closes.

        Starts the reader thread on first use. The iterator ends with ``None``
        when the DLL disconnects or :meth:`close` is called.
        """
        self.start_reader()
        while True:
            message = self._inbox.get()
            if message is None:
                return
            yield message

    def start_reader(self) -> None:
        if self._reader is not None and self._reader.is_alive():
            return
        if not self.connected:
            raise ConnectionLost("not connected")
        self._reader = threading.Thread(
            target=self._reader_loop, name="ntpl-reader", daemon=True
        )
        self._reader.start()
