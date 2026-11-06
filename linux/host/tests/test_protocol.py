import os
import unittest
from unittest import mock

from ntpl.protocol import (
    DEFAULT_PORT,
    Kind,
    Message,
    Packet,
    ProtocolError,
    default_port,
)


class MessageParseTests(unittest.TestCase):
    def test_recv(self):
        message = Message.parse("RECV|walk 19 146 0 10")
        self.assertIsNotNone(message)
        self.assertIs(message.kind, Kind.RECV)
        self.assertEqual(message.payload, "walk 19 146 0 10")

    def test_payload_may_contain_pipe(self):
        message = Message.parse("STATUS|a | b")
        self.assertEqual(message.payload, "a | b")

    def test_crlf_and_blank(self):
        self.assertEqual(Message.parse("RECV|x\r\n").payload, "x")
        self.assertIsNone(Message.parse("\n"))
        self.assertIsNone(Message.parse(""))

    def test_missing_separator(self):
        with self.assertRaises(ProtocolError):
            Message.parse("RECV walk")

    def test_unknown_kind(self):
        with self.assertRaises(ProtocolError):
            Message.parse("NOPE|x")

    def test_round_trip(self):
        original = Message(Kind.SEND, "u_s 1 2")
        parsed = Message.parse(original.encode())
        self.assertEqual(parsed.kind, original.kind)
        self.assertEqual(parsed.payload, original.payload)

    def test_bytes_input(self):
        self.assertEqual(Message.parse(b"RECV|x").payload, "x")


class PacketTests(unittest.TestCase):
    def test_fields(self):
        message = Message(Kind.RECV, "mv 3 42 10 20 5")
        packet = Packet.from_message(message, timestamp=123.456)
        self.assertEqual(packet.direction, Kind.RECV)
        self.assertEqual(packet.opcode, "mv")
        self.assertEqual(packet.args, ["3", "42", "10", "20", "5"])
        self.assertEqual(packet.ints(2), [3, 42])
        self.assertEqual(packet.timestamp, 123.456)

    def test_ints_rejects_non_numeric(self):
        packet = Packet(Kind.RECV, "say 1 2 hello", 0.0)
        self.assertIsNone(packet.ints(3))

    def test_non_packet_message(self):
        with self.assertRaises(ProtocolError):
            Packet.from_message(Message(Kind.STATUS, "ready"))

    def test_str(self):
        packet = Packet(Kind.SEND, "walk 1 2 3 4", 0.0)
        self.assertIn("[SEND] walk 1 2 3 4", str(packet))


class PortTests(unittest.TestCase):
    def test_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NTPL_PORT", None)
            self.assertEqual(default_port(), DEFAULT_PORT)

    def test_env_override(self):
        with mock.patch.dict(os.environ, {"NTPL_PORT": "50000"}):
            self.assertEqual(default_port(), 50000)

    def test_invalid_env_falls_back(self):
        for value in ("nope", "0", "70000"):
            with mock.patch.dict(os.environ, {"NTPL_PORT": value}):
                self.assertEqual(default_port(), DEFAULT_PORT)


if __name__ == "__main__":
    unittest.main()
