import io
import json
import os
import socket
import threading
import time
import unittest

from ntpl.mcp_server import Broker, GameService, McpServer


class FakeBroker:
    def __init__(self, connected: bool = True) -> None:
        self.connected = connected
        self.sent: list[str] = []
        self.last_status = "test"

    def send_upstream(self, line: str) -> bool:
        if not self.connected:
            return False
        self.sent.append(line)
        return True


class McpProtocolTests(unittest.TestCase):
    def setUp(self):
        self.portals_env = os.environ.get("NTPL_PORTALS")
        os.environ["NTPL_PORTALS"] = "/nonexistent/portals.json"
        self.broker = FakeBroker()
        self.service = GameService(
            self.broker,
            map_file="/nonexistent/NStcData.NOS",
            log_file="/nonexistent/NosTalePacketLog.txt",
        )
        self.server = McpServer(self.service)

    def tearDown(self):
        if self.portals_env is None:
            os.environ.pop("NTPL_PORTALS", None)
        else:
            os.environ["NTPL_PORTALS"] = self.portals_env

    def call(self, request_id, method, params=None):
        request = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            request["params"] = params
        return self.server.handle(request)

    def test_initialize(self):
        response = self.call(
            1,
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        )
        result = response["result"]
        self.assertEqual(result["protocolVersion"], "2024-11-05")
        self.assertEqual(result["serverInfo"]["name"], "ntpl")
        self.assertIn("tools", result["capabilities"])

    def test_tools_list(self):
        response = self.call(2, "tools/list")
        names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertIn("game_state", names)
        self.assertIn("game_move", names)
        self.assertIn("game_travel", names)
        self.assertIn("game_attack", names)
        self.assertIn("game_portals", names)

    def test_state_tool(self):
        response = self.call(
            3, "tools/call", {"name": "game_state", "arguments": {}}
        )
        result = response["result"]
        self.assertFalse(result["isError"])
        payload = json.loads(result["content"][0]["text"])
        self.assertTrue(payload["connected"])

    def test_move_against_a_dead_connection_is_an_error(self):
        self.broker.connected = False
        response = self.call(
            4,
            "tools/call",
            {"name": "game_move", "arguments": {"x": 1, "y": 2}},
        )
        result = response["result"]
        self.assertTrue(result["isError"])
        self.assertIn("not connected", result["content"][0]["text"])

    def test_unknown_tool_is_a_protocol_error(self):
        response = self.call(
            5, "tools/call", {"name": "nope", "arguments": {}}
        )
        self.assertEqual(response["error"]["code"], -32602)

    def test_serve_frames_two_requests(self):
        reader = io.StringIO(
            '{"jsonrpc":"2.0","id":1,"method":"ping"}\n'
            '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'
        )
        writer = io.StringIO()
        self.server.serve(reader, writer)
        lines = [
            json.loads(line) for line in writer.getvalue().splitlines()
        ]
        self.assertEqual([line["id"] for line in lines], [1, 2])


class BrokerTests(unittest.TestCase):
    def test_broadcast_command_forwarding_and_quit_blocking(self):
        dll = socket.socket()
        dll.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        dll.bind(("127.0.0.1", 0))
        dll.listen(1)
        dll_port = dll.getsockname()[1]
        received: list[str] = []

        def serve_dll() -> None:
            connection, _ = dll.accept()
            connection.sendall(
                b"STATUS|fake-dll\n"
                b"RECV|at 14881134 4 33 143 2 0 2 1 -1 0 0\n"
            )
            try:
                while True:
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    received.append(chunk.decode("utf-8", "replace"))
            except OSError:
                pass
            finally:
                connection.close()

        threading.Thread(target=serve_dll, daemon=True).start()
        broker = Broker("127.0.0.1", dll_port, listen_port=0)
        packets = []
        broker.on_packet = packets.append
        broker.start()
        self.addCleanup(broker.close)
        self.addCleanup(dll.close)

        deadline = time.time() + 5
        while time.time() < deadline and not (
            broker.bound_port and broker.connected
        ):
            time.sleep(0.05)
        self.assertTrue(broker.bound_port)
        self.assertTrue(broker.connected)

        client = socket.create_connection(
            ("127.0.0.1", broker.bound_port), timeout=3
        )
        self.addCleanup(client.close)
        client.settimeout(3)
        data = b""
        deadline = time.time() + 3
        while time.time() < deadline and b"RECV|at " not in data:
            data += client.recv(65536)
        self.assertIn(b"STATUS|fake-dll", data)
        self.assertIn(b"RECV|at 14881134 4 33 143", data)
        self.assertEqual(packets[0].opcode, "at")

        client.sendall(b"SEND|walk 1 2 0 10\n")
        deadline = time.time() + 3
        while time.time() < deadline and not any(
            "SEND|walk 1 2 0 10" in chunk for chunk in received
        ):
            time.sleep(0.05)
        self.assertTrue(
            any("SEND|walk 1 2 0 10" in chunk for chunk in received)
        )

        client.sendall(b"QUIT|\n")
        time.sleep(0.3)
        self.assertFalse(any("QUIT" in chunk for chunk in received))


if __name__ == "__main__":
    unittest.main()
