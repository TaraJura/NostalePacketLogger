import hashlib
import socket
import tempfile
import unittest
from pathlib import Path

from ntpl import noscrypto
from ntpl.headless import (
    LoginError,
    LoginSession,
    TokenReader,
    WorldSession,
    build_no_s0577,
    client_identity,
    file_version,
    parse_nstest,
)


def nstest(account: str = "Account1", session_id: str = "4321",
           servers: list[str] | None = None) -> str:
    fields = ["NsTeST", "unknown", "", account]
    fields.extend(str(index) for index in range(121))
    fields.append(session_id)
    fields.extend(
        servers
        if servers is not None
        else ["1.2.3.4:4000:0:1.0.NosTaleEU"]
    )
    fields.append("-1:-1:-1:10000.10000.1")
    return " ".join(fields)


class NsTeSTTests(unittest.TestCase):
    def test_parse_world_list(self):
        result = parse_nstest(nstest())
        self.assertEqual(result.account_name, "Account1")
        self.assertEqual(result.session_id, 4321)
        self.assertEqual(len(result.servers), 1)
        server = result.servers[0]
        self.assertEqual(server.ip, "1.2.3.4")
        self.assertEqual(server.port, 4000)
        self.assertEqual(server.server_id, 1)
        self.assertEqual(server.channel, 0)
        self.assertEqual(server.name, "NosTaleEU")

    def test_multiple_servers_kept(self):
        result = parse_nstest(
            nstest(
                servers=[
                    "1.2.3.4:4000:0:1.0.NosTaleEU",
                    "5.6.7.8:4006:0:2.1.NosTaleEU",
                ]
            )
        )
        self.assertEqual(len(result.servers), 2)
        self.assertEqual(result.world.ip, "1.2.3.4")

    def test_no_servers_raises(self):
        with self.assertRaises(LoginError):
            parse_nstest(nstest(servers=[]))

    def test_token_reader_keeps_empty_fields(self):
        reader = TokenReader("a  b")
        self.assertEqual(reader.next(), "a")
        self.assertEqual(reader.next(), "")
        self.assertEqual(reader.next(), "b")


class PacketFormatTests(unittest.TestCase):
    def test_no_s0577_layout(self):
        packet = build_no_s0577("abcd", "1.2.3.4", "HASH")
        self.assertTrue(packet.startswith("NoS0577 abcd  NONE_CII "))
        self.assertIn("\x0b1.2.3.4 0 HASH", packet)
        random_part = packet.split()[3]
        self.assertEqual(len(random_part), 8)


class SessionTests(unittest.TestCase):
    def test_login_session_encrypts(self):
        left, right = socket.socketpair()
        try:
            session = LoginSession(left)
            session.send("abc")
            raw = right.recv(64)
            self.assertEqual(raw, noscrypto.login_encrypt(b"abc"))
            # The documented receive transform (not the inverse).
            self.assertEqual(
                noscrypto.login_decrypt(raw),
                bytes((0xA2, 0xA1, 0xA0, 0xC9)),
            )
        finally:
            left.close()
            right.close()

    def test_world_session_first_packet_and_counter(self):
        left, right = socket.socketpair()
        try:
            session = WorldSession(left, 0x1234)
            counter = session.counter
            session.send("hello")
            raw = right.recv(4096)
            self.assertEqual(
                raw,
                noscrypto.world_encrypt(
                    f"{counter} hello".encode(), 0x1234, True
                ),
            )
            self.assertFalse(session.first)
            second_counter = session.counter
            session.send("world")
            raw = right.recv(4096)
            self.assertEqual(
                raw,
                noscrypto.world_encrypt(
                    f"{second_counter} world".encode(), 0x1234, False
                ),
            )
        finally:
            left.close()
            right.close()


class GameFileTests(unittest.TestCase):
    def test_file_version_and_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client_x = b"px" * 8 + "FileVersion".encode("utf-16-le")
            client_x += b'1.2.3.4"\x00\x00'
            (root / "NostaleClientX.exe").write_bytes(client_x)
            (root / "NostaleClient.exe").write_bytes(b"pc" * 8)
            self.assertEqual(file_version(root / "NostaleClientX.exe"),
                             "1.2.3.4")
            version, login_hash = client_identity(root)
            self.assertEqual(version, "1.2.3.4")
            expected = hashlib.md5(
                (
                    hashlib.md5(client_x).hexdigest()
                    + hashlib.md5(b"pc" * 8).hexdigest()
                ).encode("ascii")
            ).hexdigest().upper()
            self.assertEqual(login_hash, expected)


if __name__ == "__main__":
    unittest.main()
