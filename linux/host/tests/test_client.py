import socket
import threading
import time
import unittest

from ntpl.client import ConnectionLost, PacketClient
from ntpl.protocol import Kind


class FakeServer:
    """Minimal stand-in for the DLL's TCP server."""

    def __init__(self, greeting: bytes = b""):
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]

        self.greeting = greeting
        self.received: list[str] = []
        self.connected = threading.Event()
        self.stop = threading.Event()
        self.connection = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> "FakeServer":
        self.thread.start()
        return self

    def _run(self) -> None:
        try:
            self.connection, _ = self.listener.accept()
        except OSError:
            return

        self.connected.set()

        if self.greeting:
            self.connection.sendall(self.greeting)

        buffer = b""
        while not self.stop.is_set():
            try:
                chunk = self.connection.recv(4096)
            except OSError:
                break
            if not chunk:
                break

            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                self.received.append(line.decode("utf-8"))

    def close(self) -> None:
        self.stop.set()
        if self.connection is not None:
            try:
                # shutdown() forces a FIN and unblocks the server thread's
                # recv(); close() alone can leave the peer waiting.
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        for sock in (self.connection, self.listener):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass


class ClientTests(unittest.TestCase):
    def make_client(self, server, **kwargs):
        client = PacketClient(port=server.port, **kwargs)
        client.connect(retries=1)
        self.addCleanup(client.close)
        return client

    def test_receives_greeting_and_packet(self):
        server = FakeServer(greeting=b"STATUS|ready\nRECV|walk 1 2 3 4\n").start()
        self.addCleanup(server.close)

        client = self.make_client(server)

        status = client.recv(timeout=2.0)
        self.assertIsNotNone(status)
        self.assertIs(status.kind, Kind.STATUS)
        self.assertEqual(status.payload, "ready")

        packet = client.recv(timeout=2.0)
        self.assertIsNotNone(packet)
        self.assertIs(packet.kind, Kind.RECV)
        self.assertEqual(packet.payload, "walk 1 2 3 4")

    def test_reassembles_split_lines(self):
        server = FakeServer().start()
        self.addCleanup(server.close)

        client = self.make_client(server)
        self.assertTrue(server.connected.wait(2.0), "server never accepted")
        server.connection.sendall(b"RECV|walk 1 ")
        time.sleep(0.05)
        server.connection.sendall(b"2 3 4\n")

        message = client.recv(timeout=2.0)
        self.assertIsNotNone(message)
        self.assertEqual(message.payload, "walk 1 2 3 4")

    def test_send_packet_frames_command(self):
        server = FakeServer().start()
        self.addCleanup(server.close)

        client = self.make_client(server)
        client.send_packet("walk 19 146 0 10")

        deadline = time.monotonic() + 2.0
        while not server.received and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertEqual(server.received, ["SEND|walk 19 146 0 10"])

    def test_close_is_safe(self):
        server = FakeServer().start()
        self.addCleanup(server.close)

        client = self.make_client(server)
        client.close()
        client.close()

    def test_peer_close_raises(self):
        server = FakeServer(greeting=b"STATUS|ready\n").start()
        self.addCleanup(server.close)

        client = self.make_client(server)
        self.assertIsNotNone(client.recv(timeout=2.0))

        server.close()

        with self.assertRaises(ConnectionLost):
            for _ in range(50):
                try:
                    client.recv(timeout=0.1)
                except ConnectionLost:
                    raise
            self.fail("ConnectionLost was never raised")


if __name__ == "__main__":
    unittest.main()
