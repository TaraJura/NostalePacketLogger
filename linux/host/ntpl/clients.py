"""Find running NosTale clients and the logger port injected into each.

The GTK GUI is a native Linux process, so it cannot enumerate Windows PIDs by
itself.  Two sources are combined:

* ``Injector.exe --list``, run with the game's *own* Wine binary and prefix,
  reports the Windows PID and window title of every client.  Using the Wine
  from ``/proc/<pid>/exe`` matters: the game may run on a newer Wine than the
  one in ``PATH``, and two Wine versions must not share a prefix.
* Each injected DLL writes ``C:\\ntpl-<pid>.port`` with the port it listens
  on, so several clients can run at once without fighting over one port.  The
  GUI matches that file to the WinPID from the injector list.

Everything here is read-only until :func:`inject_client` is called; discovery
never starts, stops or injects anything.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

# Must match linux/src/injector/processes.cpp.
TARGET_EXE_NAMES = {"nostaleclientx.exe", "nostalex.dat", "customclient.exe"}

# Must match linux/src/dll/config.h.
DEFAULT_PORT = 47821
PORT_FILE_TEMPLATE = "ntpl-{pid}.port"

INJECTOR_TIMEOUT = 25.0
WINEPATH_TIMEOUT = 15.0
LIST_TIMEOUT = 30.0
INJECT_TIMEOUT = 30.0
PORT_WAIT = 12.0

# linux/host/ntpl/clients.py -> linux/
BUILD_DIR = Path(__file__).resolve().parents[2] / "build"
INJECTOR_PATH = BUILD_DIR / "Injector.exe"
DLL_PATH = BUILD_DIR / "PacketLogger.dll"

_LIST_ROW = re.compile(r"^\s*\[\d+\]\s+PID\s+(\d+)\s+(\S+)\s+(.*)$")


@dataclass
class WineContext:
    """The Wine installation and prefix a particular client runs under."""

    wine: str
    prefix: Path
    env: dict[str, str] = field(repr=False)

    @property
    def base_port(self) -> int:
        value = self.env.get("NTPL_PORT", "")
        try:
            port = int(value)
        except ValueError:
            return DEFAULT_PORT
        return port if 1 <= port <= 65535 else DEFAULT_PORT


@dataclass
class ClientInfo:
    """One running game window and the logger port it listens on, if any."""

    context: WineContext
    winpid: int
    title: str
    port: int | None = None

    @property
    def label(self) -> str:
        return f"{self.title} (PID {self.winpid})"

    @property
    def status(self) -> str:
        if self.port:
            return f"logger on 127.0.0.1:{self.port}"
        return "logger not injected"


@dataclass
class Discovery:
    """What a client scan found, plus why parts of it may have failed."""

    clients: list[ClientInfo] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    injector: Path | None = None
    dll: Path | None = None

    @property
    def injectable(self) -> bool:
        return (
            self.injector is not None
            and self.dll is not None
            and self.injector.exists()
            and self.dll.exists()
        )


# ------------------------------------------------------------------ /proc

def _proc_entries(proc_root: str) -> list[str]:
    try:
        return sorted(os.listdir(proc_root))
    except OSError:
        return []


def _read_environ(proc_root: str, pid: str) -> dict[str, str]:
    raw = Path(proc_root, pid, "environ").read_bytes()
    result: dict[str, str] = {}
    for item in raw.split(b"\0"):
        key, separator, value = item.partition(b"=")
        if separator:
            result[key.decode("utf-8", "replace")] = value.decode(
                "utf-8", "replace"
            )
    return result


def _wine_binary(proc_root: str, pid: str) -> str | None:
    """The Wine install that launched the client, not whatever ``PATH`` has.

    A process started by ``.../opt/wine-stable/bin/wine`` runs through
    ``.../lib/wine/x86_64-unix/wine-preloader``; the matching executable is the
    first ``bin/wine`` found by walking up from the preloader.
    """
    try:
        exe = Path(os.readlink(Path(proc_root, pid, "exe")))
    except OSError:
        return None

    if exe.name in {"wine-preloader", "wine64-preloader"}:
        for parent in exe.parents:
            candidate = parent / "bin" / "wine"
            if candidate.is_file():
                return str(candidate)
    return None


def _wine_env(proc_env: dict[str, str], wine: str) -> dict[str, str]:
    env = dict(os.environ)
    for key in ("WINEPREFIX", "WINEARCH", "WINEDLLOVERRIDES", "NTPL_PORT"):
        if key in proc_env:
            env[key] = proc_env[key]
    # The injector is a short-lived helper; its Wine chatter is never useful.
    env["WINEDEBUG"] = "-all"
    env["PATH"] = str(Path(wine).parent) + os.pathsep + env.get("PATH", "")
    return env


def running_wine_contexts(proc_root: str = "/proc") -> list[WineContext]:
    """One context per Wine prefix that has a game window, in /proc order."""
    contexts: dict[str, WineContext] = {}
    # Never guess: a game in ~/.wine (the WarCraft III prefix on this machine)
    # has no WINEPREFIX in its environment and must be left alone.
    forbidden = {Path.home() / ".wine"}

    for entry in _proc_entries(proc_root):
        if not entry.isdigit():
            continue
        try:
            raw = Path(proc_root, entry, "cmdline").read_bytes()
            args = [part for part in raw.split(b"\0") if part]
        except OSError:
            continue
        if not args:
            continue

        name = re.split(r"[\\/]", args[0].decode("utf-8", "replace"))[-1]
        if name.lower() not in TARGET_EXE_NAMES:
            continue
        # Chromium helpers share the client's executable name; only the main
        # process has a game window and a usable command line.
        if any(part.startswith(b"--type=") for part in args[1:]):
            continue

        try:
            proc_env = _read_environ(proc_root, entry)
        except OSError:
            continue
        prefix_text = proc_env.get("WINEPREFIX", "")
        if not prefix_text:
            continue
        prefix = Path(prefix_text)
        if prefix in forbidden:
            continue
        key = str(prefix)
        if key in contexts:
            continue

        wine = _wine_binary(proc_root, entry) or shutil.which("wine")
        if not wine:
            continue
        contexts[key] = WineContext(
            wine=wine,
            prefix=prefix,
            env=_wine_env(dict(proc_env), wine),
        )
    return list(contexts.values())


# ------------------------------------------------------------- port files

def port_file(prefix: Path, winpid: int) -> Path:
    """``C:\\ntpl-<pid>.port`` as a Linux path inside the prefix."""
    return prefix / "drive_c" / PORT_FILE_TEMPLATE.format(pid=winpid)


def read_port(prefix: Path, winpid: int) -> int | None:
    try:
        text = port_file(prefix, winpid).read_text(
            encoding="ascii", errors="replace"
        )
    except OSError:
        return None
    match = re.search(r"\d+", text)
    if match is None:
        return None
    port = int(match.group())
    return port if 1 <= port <= 65535 else None


def wait_for_port(
    context: WineContext, winpid: int, timeout: float = PORT_WAIT
) -> int | None:
    """Poll for the DLL's port file after an injection."""
    deadline = time.monotonic() + timeout
    while True:
        port = read_port(context.prefix, winpid)
        if port is not None:
            return port
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.2)


# --------------------------------------------------------------- injector

def parse_injector_list(text: str) -> list[tuple[int, str, str]]:
    """Rows of ``Injector.exe --list``: (WinPID, exe name, window title)."""
    rows: list[tuple[int, str, str]] = []
    for line in text.splitlines():
        match = _LIST_ROW.match(line)
        if match is not None:
            rows.append(
                (int(match.group(1)), match.group(2), match.group(3).strip())
            )
    return rows


def _run_injector(
    context: WineContext,
    injector: Path,
    arguments: list[str],
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [context.wine, str(injector), *arguments],
        env=context.env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def list_clients(
    context: WineContext, injector: Path, timeout: float = LIST_TIMEOUT
) -> list[ClientInfo]:
    result = _run_injector(context, injector, ["--list", "--no-tag"], timeout)
    clients: list[ClientInfo] = []
    for winpid, _exe, title in parse_injector_list(result.stdout):
        # The CEF helpers (gpu/renderer/utility) share the .exe name and have
        # no visible window; they cannot be injected usefully.
        if not title or title.startswith("(no window"):
            continue
        clients.append(
            ClientInfo(
                context=context,
                winpid=winpid,
                title=title,
                port=read_port(context.prefix, winpid),
            )
        )
    return clients


def windows_path(context: WineContext, path: Path) -> str:
    """Convert a Linux path into the ``Z:`` form Wine will accept."""
    try:
        result = subprocess.run(
            [context.wine, "winepath", "-w", str(path)],
            env=context.env,
            capture_output=True,
            text=True,
            timeout=WINEPATH_TIMEOUT,
            check=False,
        )
        converted = result.stdout.strip().splitlines()
        if converted:
            return converted[-1]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "Z:" + str(path).replace("/", "\\")


def inject_client(
    client: ClientInfo,
    injector: Path | None = None,
    dll: Path | None = None,
) -> tuple[bool, str]:
    """Inject the logger into one client by its Windows PID."""
    injector = injector or INJECTOR_PATH
    dll = dll or DLL_PATH
    if not injector.is_file() or not dll.is_file():
        return False, (
            "build/PacketLogger.dll or build/Injector.exe is missing; run "
            "make -C linux all"
        )

    result = _run_injector(
        client.context,
        injector,
        [
            "--pid",
            str(client.winpid),
            "--dll",
            windows_path(client.context, dll),
            "--no-tag",
        ],
        INJECT_TIMEOUT,
    )
    output = (result.stdout + result.stderr).strip()
    printed = output or f"exit code {result.returncode}"
    return "[+] injected" in output, printed


def discover_clients(proc_root: str = "/proc") -> Discovery:
    """Scan every game window in every reachable Wine prefix."""
    discovery = Discovery(injector=INJECTOR_PATH, dll=DLL_PATH)
    contexts = running_wine_contexts(proc_root)
    if not contexts:
        discovery.errors.append(
            "no running NosTale client found (start the game first)"
        )
        return discovery
    if not discovery.injectable:
        discovery.errors.append(
            "the injector is not built; run make -C linux all"
        )
        return discovery

    for context in contexts:
        try:
            clients = list_clients(context, discovery.injector)
        except (OSError, subprocess.SubprocessError) as exc:
            discovery.errors.append(
                f"cannot list clients in {context.prefix}: {exc}"
            )
            continue
        discovery.clients.extend(clients)

    if contexts and not discovery.clients:
        discovery.errors.append("no game window found in the running client")
    return discovery
