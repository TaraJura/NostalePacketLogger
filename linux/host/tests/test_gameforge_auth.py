import base64
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ntpl import gameforge_auth as gf


def xor_decrypt(value: str, gsid: str, account_id: str) -> bytes:
    key = hashlib.sha512((gsid + "-" + account_id).encode()).hexdigest()
    data = base64.b64decode(value)
    out = bytearray()
    for index, byte in enumerate(data):
        k = index % len(key)
        out.append(byte ^ ord(key[k]) ^ ord(key[len(key) - 1 - k]))
    return bytes(out)


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body

    @property
    def text(self):
        import json

        return json.dumps(self._body)


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response


class GameforgeAuthTests(unittest.TestCase):
    def test_blackbox_is_deterministic_and_prefixed(self):
        identity = [0] * 30
        identity[2] = "Windows"
        first = gf.blackbox(identity)
        second = gf.blackbox(identity)
        self.assertTrue(first.startswith("tra:"))
        self.assertEqual(first, second)

    def test_blackbox_user_agent_round_trip(self):
        identity = [0] * 30
        identity[2] = "Windows"
        identity[26] = "Mozilla/5.0 (Test) Chrome/156.0 Safari/537.36"
        value = gf.blackbox(identity)
        self.assertEqual(len(gf.decode_blackbox(value)), 30)
        self.assertEqual(gf.blackbox_user_agent(value), identity[26])

    def test_blackbox_user_agent_falls_back(self):
        self.assertEqual(
            gf.blackbox_user_agent("tra:not-a-blackbox"),
            gf.BROWSER_USER_AGENT,
        )

    def test_load_identity_reads_a_30_value_list(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "identity.json"
            path.write_text("[0, 1, 2]", encoding="utf-8")
            self.assertIsNone(gf.load_identity(path))
            values = list(range(30))
            path.write_text(
                "[" + ", ".join(str(value) for value in values) + "]",
                encoding="utf-8",
            )
            self.assertEqual(gf.load_identity(path), values)
            self.assertIsNone(gf.load_identity(Path(directory) / "missing"))

    def test_game1_blackbox_prefixes_the_browser_value(self):
        stdout = "<body>BBRESULT:" + "A" * 120 + "</body>"
        with mock.patch.object(
            gf, "find_browser", return_value="/usr/bin/google-chrome"
        ), mock.patch.object(
            gf.subprocess,
            "run",
            return_value=SimpleNamespace(stdout=stdout),
        ) as run:
            value = gf.game1_blackbox()
            self.assertEqual(value, "tra:" + "A" * 120)
            command = run.call_args.args[0]
            self.assertIn("--dump-dom", command)
            self.assertIn("--headless=new", command)

    def test_game1_blackbox_reports_a_failed_page(self):
        with mock.patch.object(
            gf, "find_browser", return_value="/usr/bin/google-chrome"
        ), mock.patch.object(
            gf.subprocess,
            "run",
            return_value=SimpleNamespace(stdout="<body>BBERROR:x</body>"),
        ):
            with self.assertRaises(gf.AuthError):
                gf.game1_blackbox()

    def test_game1_blackbox_needs_a_browser(self):
        with mock.patch.object(gf, "find_browser", return_value=None):
            with self.assertRaises(gf.AuthError) as context:
                gf.game1_blackbox()
        self.assertIn("NTPL_CHROME", str(context.exception))

    def test_js_blackbox_encrypts_the_generated_value(self):
        with mock.patch.object(
            gf, "game1_blackbox", return_value="tra:test-value"
        ) as generate:
            value = gf.JsBlackbox().encrypted(
                "gsid-1234", "install", "account"
            )
            generate.assert_called_once()
            self.assertEqual(
                xor_decrypt(value, "gsid-1234", "account"),
                b"tra:test-value",
            )

    def test_identity_blackbox_matches_the_file_mode(self):
        identity = [0] * 30
        identity[2] = "Windows"
        payload = {
            "features": [7],
            "installation": "install",
            "session": "gsid",
        }
        source = gf.IdentityBlackbox(identity)
        with mock.patch.object(
            gf, "_request_payload", return_value=payload
        ):
            self.assertEqual(source.plain(), gf.blackbox(identity))
            self.assertEqual(
                source.encrypted("gsid-1234", "install", "account"),
                gf._encrypted(identity, "account", "gsid-1234", "install"),
            )

    def test_error_codes_from_dict_and_list(self):
        self.assertEqual(
            gf._codes(
                FakeResponse(409, {"errorTypes": {"A": 1, "B": 2}})
            ),
            ["A", "B"],
        )
        self.assertEqual(
            gf._codes(
                FakeResponse(409, {"errorTypes": ["A", "SERVICE_ERROR"]})
            ),
            ["A", "SERVICE_ERROR"],
        )

    def test_login_rejection_carries_codes(self):
        response = FakeResponse(
            409, {"errorTypes": {"CREDENTIALS_INVALID": 1}}
        )
        with mock.patch.object(
            gf.requests, "Session", return_value=FakeSession(response)
        ):
            with self.assertRaises(gf.AuthError) as context:
                gf.login("email", "password", "tra:abc", "install")
        self.assertIn("CREDENTIALS_INVALID", context.exception.codes)

    def test_login_uses_the_blackbox_user_agent_and_web_host(self):
        identity = [0] * 30
        identity[26] = "Mozilla/5.0 (Test) Chrome/156.0 Safari/537.36"
        response = FakeResponse(201, {"token": "bearer"})
        session = FakeSession(response)
        with mock.patch.object(
            gf.requests, "Session", return_value=session
        ):
            gf.login("email", "password", gf.blackbox(identity), "install")
        url, kwargs = session.calls[0]
        self.assertIn("spark-web.gameforge.com", url[0])
        self.assertEqual(
            kwargs["headers"]["User-Agent"], identity[26]
        )

    def test_login_sends_a_solved_challenge_id(self):
        response = FakeResponse(201, {"token": "bearer"})
        session = FakeSession(response)
        with mock.patch.object(
            gf.requests, "Session", return_value=session
        ):
            gf.login(
                "email", "password", "tra:abc", "install",
                challenge_id="challenge-1",
            )
        headers = session.calls[0][1]["headers"]
        self.assertEqual(headers["gf-challenge-id"], "challenge-1")

    def test_login_sends_the_blackbox(self):
        response = FakeResponse(201, {"token": "bearer"})
        session = FakeSession(response)
        with mock.patch.object(
            gf.requests, "Session", return_value=session
        ):
            gf.login("email", "password", "tra:abc", "install")
        body = session.calls[0][1]["json"]
        self.assertEqual(body["blackbox"], "tra:abc")

    def test_login_returns_the_bearer(self):
        response = FakeResponse(201, {"token": "bearer"})
        with mock.patch.object(
            gf.requests, "Session", return_value=FakeSession(response)
        ):
            self.assertEqual(
                gf.login("email", "password", "tra:abc", "install"),
                "bearer",
            )

    def test_login_raises_challenge_with_the_id(self):
        response = FakeResponse(
            409,
            {
                "errorTypes": ["CHALLENGE_REQUIRED"],
                "challengeId": "challenge-1",
            },
        )
        response.headers = {
            "gf-challenge-id": "challenge-1;https://challenge.gameforge.com"
        }
        with mock.patch.object(
            gf.requests, "Session", return_value=FakeSession(response)
        ):
            with self.assertRaises(gf.ChallengeRequired) as context:
                gf.login("email", "password", "tra:abc", "install")
        self.assertEqual(context.exception.challenge_id, "challenge-1")

    def test_iovation_failure_reports_the_status(self):
        class Source:
            def plain(self):
                return "tra:abc"

        response = FakeResponse(403, {"status": "failed"})
        with mock.patch.object(gf.time, "sleep"), mock.patch.object(
            gf.requests, "post", return_value=response
        ):
            with self.assertRaises(gf.AuthError) as context:
                gf.mint_code("bearer", "account", Source(), "install", b"cert")
        self.assertIn("403", str(context.exception))

    def test_ua_magic_shape(self):
        magic = gf._ua_magic(
            "ab123", "1234-5678", b"cert", "C2.2.19.1700"
        )
        self.assertTrue(magic.startswith("ab"))
        self.assertEqual(len(magic), 10)


if __name__ == "__main__":
    unittest.main()
