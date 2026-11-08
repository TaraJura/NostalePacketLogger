"""Tests for client discovery (injector output, /proc scan, port files).

No Wine process is started: ``/proc`` is faked as a directory tree and the
injector output is a captured sample, so the suite stays stdlib-only.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ntpl.clients import (
    DEFAULT_PORT,
    ClientInfo,
    WineContext,
    acquire_client_lock,
    client_lock_owner,
    client_lock_path,
    parse_injector_list,
    port_file,
    read_port,
    release_client_lock,
    running_wine_contexts,
    windows_path,
)

SAMPLE_LIST = """Found 4 NosTale process(es):

  [1] PID 624     NostaleClientX.exe    NosTale - 624
  [2] PID 1864    NostaleClientX.exe    (no window title)
  [3] PID 2624    NostaleClientX.exe    (no window title)
  [4] PID 2172    NostaleClientX.exe    (no window title)
"""


class ParseInjectorListTest(unittest.TestCase):
    def test_parses_pid_exe_and_title(self) -> None:
        rows = parse_injector_list(SAMPLE_LIST)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0], (624, "NostaleClientX.exe", "NosTale - 624"))
        self.assertEqual(rows[3][0], 2172)

    def test_ignores_noise(self) -> None:
        rows = parse_injector_list(
            "No NosTale client found. Start the game first.\n"
        )
        self.assertEqual(rows, [])


class PortFileTest(unittest.TestCase):
    def test_port_file_lives_on_the_prefix_c_drive(self) -> None:
        path = port_file(Path("/p/prefix"), 624)
        self.assertEqual(path, Path("/p/prefix/drive_c/ntpl-624.port"))

    def test_read_port_accepts_crlf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp)
            (prefix / "drive_c").mkdir()
            port_file(prefix, 624).write_text("47822\r\n", encoding="ascii")
            self.assertEqual(read_port(prefix, 624), 47822)

    def test_read_port_rejects_garbage_and_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp)
            (prefix / "drive_c").mkdir()
            port_file(prefix, 1).write_text("not a port", encoding="ascii")
            self.assertIsNone(read_port(prefix, 1))
            self.assertIsNone(read_port(prefix, 2))

    def test_client_status_text(self) -> None:
        context = WineContext(wine="wine", prefix=Path("/p"), env={})
        self.assertEqual(
            ClientInfo(context, 624, "NosTale", None).status,
            "logger not injected",
        )
        self.assertEqual(
            ClientInfo(context, 624, "NosTale", 47822).status,
            "logger on 127.0.0.1:47822",
        )


class BasePortTest(unittest.TestCase):
    def test_defaults(self) -> None:
        context = WineContext(wine="wine", prefix=Path("/p"), env={})
        self.assertEqual(context.base_port, DEFAULT_PORT)

    def test_reads_the_games_ntpl_port(self) -> None:
        context = WineContext(
            wine="wine", prefix=Path("/p"), env={"NTPL_PORT": "50000"}
        )
        self.assertEqual(context.base_port, 50000)

    def test_ignores_invalid_values(self) -> None:
        context = WineContext(
            wine="wine", prefix=Path("/p"), env={"NTPL_PORT": "nope"}
        )
        self.assertEqual(context.base_port, DEFAULT_PORT)


class ProcScanTest(unittest.TestCase):
    def _make_context_tree(self, root: Path) -> None:
        wine = root / "wine-install"
        preloader = wine / "lib" / "wine" / "x86_64-unix" / "wine-preloader"
        preloader.parent.mkdir(parents=True)
        preloader.write_bytes(b"")
        (wine / "bin").mkdir()
        (wine / "bin" / "wine").write_bytes(b"")
        self.wine = wine

        proc = root / "proc"
        proc.mkdir()
        prefix = root / "prefix"
        self.prefix = prefix

        def add(pid: int, command: list[bytes], environ: bytes) -> None:
            directory = proc / str(pid)
            directory.mkdir()
            (directory / "cmdline").write_bytes(b"\0".join(command) + b"\0")
            (directory / "environ").write_bytes(environ)
            os.symlink(preloader, directory / "exe")

        game = b"C:\\users\\x\\Nostale\\NostaleClientX.exe"
        env = (
            b"WINEPREFIX=" + str(prefix).encode() + b"\0WINEARCH=win64\0"
        )
        add(100, [game, b"gf", b"0"], env)
        add(101, [game, b"gf", b"0"], env)  # second client, same prefix
        add(102, [game, b"--type=gpu-process"], env)  # CEF helper
        add(103, [game], b"WINEARCH=win64\0")  # no WINEPREFIX: leave alone
        wine_home = str(Path.home() / ".wine").encode()
        add(104, [game], b"WINEPREFIX=" + wine_home + b"\0")  # forbidden
        add(105, [b"/usr/bin/python3"], env)  # unrelated process

    def test_one_context_per_prefix_and_filters_noise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._make_context_tree(Path(tmp))
            contexts = running_wine_contexts(str(Path(tmp) / "proc"))
            self.assertEqual(len(contexts), 1)
            self.assertEqual(contexts[0].prefix, self.prefix)
            self.assertEqual(contexts[0].wine, str(self.wine / "bin" / "wine"))
            self.assertEqual(
                contexts[0].env.get("WINEPREFIX"), str(self.prefix)
            )

    def test_missing_proc_tree_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                running_wine_contexts(str(Path(tmp) / "nope")), []
            )


class WindowsPathTest(unittest.TestCase):
    def test_falls_back_to_z_drive_without_wine(self) -> None:
        context = WineContext(
            wine="/nonexistent/wine", prefix=Path("/p"), env={}
        )
        self.assertEqual(
            windows_path(context, Path("/tmp/a b/dll.dll")),
            "Z:\\tmp\\a b\\dll.dll",
        )


class ClientLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher = mock.patch.dict(
            os.environ, {"NTPL_RUNTIME_DIR": self.directory.name}
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_acquire_and_release(self) -> None:
        self.assertTrue(acquire_client_lock(4242))
        self.assertEqual(client_lock_owner(4242), os.getpid())
        self.assertTrue(acquire_client_lock(4242))
        release_client_lock(4242)
        self.assertIsNone(client_lock_owner(4242))

    def test_another_live_gui_blocks(self) -> None:
        path = client_lock_path(4243)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{os.getppid()}\n", encoding="ascii")
        self.assertFalse(acquire_client_lock(4243))
        self.assertEqual(client_lock_owner(4243), os.getppid())
        release_client_lock(4243)
        self.assertTrue(path.exists())
        path.unlink()

    def test_stale_lock_is_removed(self) -> None:
        process = subprocess.Popen(["true"])
        process.wait()
        path = client_lock_path(4244)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{process.pid}\n", encoding="ascii")
        self.assertIsNone(client_lock_owner(4244))
        self.assertTrue(acquire_client_lock(4244))
        release_client_lock(4244)

    def test_malformed_lock_is_removed(self) -> None:
        path = client_lock_path(4245)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not-a-pid\n", encoding="ascii")
        self.assertIsNone(client_lock_owner(4245))
        self.assertTrue(acquire_client_lock(4245))
        release_client_lock(4245)


if __name__ == "__main__":
    unittest.main()
