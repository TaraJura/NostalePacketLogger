import unittest
from unittest import mock

from ntpl import gameforge_auth as gf


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, response):
        self.response = response

    def post(self, *args, **kwargs):
        return self.response


class GameforgeAuthTests(unittest.TestCase):
    def test_generated_identity_shape(self):
        identity = gf.generated_identity()
        self.assertEqual(len(identity), 30)
        self.assertIsInstance(identity[0], int)
        self.assertEqual(identity[2], "Windows")
        self.assertEqual(identity[26], gf.BROWSER_USER_AGENT)

    def test_blackbox_is_deterministic_and_prefixed(self):
        identity = [0] * 30
        identity[2] = "Windows"
        first = gf.blackbox(identity)
        second = gf.blackbox(identity)
        self.assertTrue(first.startswith("tra:"))
        self.assertEqual(first, second)

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
                gf.login("email", "password", [0] * 30, "install")
        self.assertIn("CREDENTIALS_INVALID", context.exception.codes)

    def test_login_returns_the_bearer(self):
        response = FakeResponse(201, {"token": "bearer"})
        with mock.patch.object(
            gf.requests, "Session", return_value=FakeSession(response)
        ):
            self.assertEqual(
                gf.login("email", "password", [0] * 30, "install"),
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
                gf.login("email", "password", [0] * 30, "install")
        self.assertEqual(context.exception.challenge_id, "challenge-1")

    def test_ua_magic_shape(self):
        magic = gf._ua_magic(
            "ab123", "1234-5678", b"cert", "C2.2.19.1700"
        )
        self.assertTrue(magic.startswith("ab"))
        self.assertEqual(len(magic), 10)


if __name__ == "__main__":
    unittest.main()
