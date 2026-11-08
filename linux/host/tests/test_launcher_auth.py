import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from ntpl.launcher_auth import LauncherAuthError, obtain_token

FAKE_API = textwrap.dedent(
    '''
    class NtLauncher:
        def __init__(self, locale, gf_lang):
            self.locale = locale
            self.gf_lang = gf_lang

        def auth(self, email, password):
            self.auth_args = (email, password)
            return email == "player@example.com"

        def getAccounts(self):
            return [("account-1", "Main"), ("account-2", "Alt")]

        def getToken(self, account, raw=False):
            self.token_account = account
            return "deadbeef" if account == "account-1" else "cafebabe"
    '''
)


class LauncherAuthTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        package = root / "nosauth"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "api.py").write_text(FAKE_API, encoding="utf-8")
        self.root = root
        self._old_path = os.environ.get("NTPL_NOSAUTH_PATH")
        os.environ["NTPL_NOSAUTH_PATH"] = str(root)
        sys.modules.pop("nosauth", None)
        sys.modules.pop("nosauth.api", None)

    def tearDown(self):
        if self._old_path is None:
            os.environ.pop("NTPL_NOSAUTH_PATH", None)
        else:
            os.environ["NTPL_NOSAUTH_PATH"] = self._old_path
        sys.modules.pop("nosauth", None)
        sys.modules.pop("nosauth.api", None)
        self._temp.cleanup()

    def test_obtains_the_hex_token_and_account(self):
        token, account, name = obtain_token(
            "player@example.com", "secret", root=self.root
        )
        self.assertEqual(token, "deadbeef")
        self.assertEqual(account, "account-1")
        self.assertEqual(name, "Main")

    def test_account_selection(self):
        token, account, name = obtain_token(
            "player@example.com", "secret", account_id="account-2",
            root=self.root,
        )
        self.assertEqual((token, account, name), ("cafebabe", "account-2", "Alt"))

    def test_rejected_login(self):
        with self.assertRaises(LauncherAuthError):
            obtain_token(
                "wrong@example.com", "secret", root=self.root
            )

    def test_unknown_account(self):
        with self.assertRaises(LauncherAuthError):
            obtain_token(
                "player@example.com", "secret", account_id="nope",
                root=self.root,
            )

    def test_missing_package(self):
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaises(LauncherAuthError):
                obtain_token(
                    "player@example.com", "secret", root=Path(empty)
                )


if __name__ == "__main__":
    unittest.main()
