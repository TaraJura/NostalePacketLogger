"""Headless smoke test for the Pet Trainer tab (tame → train → extract).

The fake server starts the character in the Miniland (map 20001, so no portal
walk). The tamer must:

1. Whip and Tame three Chickens; the third Tame answers
   ``msgi 0 1887`` (maximum pet slots), which switches to training.
2. Cast Summon Weak Sparring Monster (slot 2), see the sparring monster
   spawn (vnum 1497) and send the two tamed pets at it with ``suctl``.
3. After the (shortened) training time, cast Extract Essence (slot 3,
   target type 2) on each pet; ``msgi 0 2627`` releases it and the panel
   shrinks; with no pets left the tamer goes back to taming.

Run through scripts/dev-test-bot.sh (Xvfb).
"""

from __future__ import annotations

import os
import socket
import tempfile
import threading

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from ntpl.gui import Application

CHAR_ID = 14881134
CHICKEN_A = 3039
CHICKEN_B = 3040
CHICKEN_C = 3041
PET_A = 1452945
PET_B = 1452978
SPARRING = 1506236


class TamerServer:
    def __init__(self) -> None:
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.received: list[str] = []
        self.connection = None
        self.stop = threading.Event()
        self.tames = 0
        self.suctl: list[str] = []
        self.pets: dict[int, tuple[int, int]] = {}
        self.extracted: set[int] = set()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            self.connection, _ = self.listener.accept()
            self.connection.sendall(
                (
                    "STATUS|ready\n"
                    f"RECV|at {CHAR_ID} 20001 5 8 2 0 0 0 0 0 0\n"
                    "RECV|stat 1000 1000 500 500 0 0\n"
                    f"RECV|in 3 333 {CHICKEN_A} 6 8 2 100 100 0 0 0 "
                    "-1 0 0 -1 - 0 -1 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
                    f"RECV|st 3 {CHICKEN_A} 1 0 100 100 100 10 100 10 0\n"
                    f"RECV|in 3 333 {CHICKEN_B} 7 9 2 100 100 0 0 0 "
                    "-1 0 0 -1 - 0 -1 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
                    f"RECV|st 3 {CHICKEN_B} 1 0 100 100 100 10 100 10 0\n"
                    f"RECV|in 3 333 {CHICKEN_C} 6 9 2 100 100 0 0 0 "
                    "-1 0 0 -1 - 0 -1 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
                    f"RECV|st 3 {CHICKEN_C} 1 0 100 100 100 10 100 10 0\n"
                    "RECV|p_clear\n"
                    "RECV|sc_p 0 806 822161 1 3000 2 0 0 80 158 60 24 110 "
                    "0 20 43 24 43 20 2 60 80 50 50 477 481\n"
                ).encode()
            )
        except OSError:
            return
        buffer = b""
        while not self.stop.is_set():
            try:
                chunk = self.connection.recv(4096)
            except OSError:
                return
            if not chunk:
                return
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                text = line.decode("utf-8", "replace")
                self.received.append(text)
                self._answer(text)

    def _scp(self) -> str:
        return (
            "RECV|p_clear\n"
            + "".join(
                f"RECV|sc_p {slot} 333 {entity} 1 3000 0 0 0 32 40\n"
                for slot, (_vnum, entity) in sorted(self.pets.items())
            )
        )

    def _answer(self, text: str) -> None:
        if text.startswith("TARGET|"):
            try:
                entity_id = int(text.split("|", 1)[1])
            except ValueError:
                return
            self.send(
                f"RECV|st 3 {entity_id} 1 0 100 100 100 10 100 10 0\n"
            )
            return
        if not text.startswith("SEND|"):
            return
        line = text[len("SEND|"):]
        parts = line.split()
        if not parts:
            return
        if parts[0] == "u_s":
            self._answer_skill(parts)
        elif parts[0] == "suctl":
            self.suctl.append(line)

    def _answer_skill(self, parts: list[str]) -> None:
        try:
            slot = int(parts[1])
            target_type = int(parts[2])
            target_id = int(parts[3])
        except (IndexError, ValueError):
            return
        if slot == 0:
            self.send(
                f"RECV|st 3 {target_id} 1 0 1 100 20 10 100 10 0\n"
            )
        elif slot == 1 and target_type == 3:
            self.tames += 1
            self.send(f"RECV|ct 1 {CHAR_ID} 3 {target_id} 20 8153 1787\n")
            if self.tames <= 2:
                pet = PET_A if self.tames == 1 else PET_B
                slot_index = 3 + self.tames
                self.pets[slot_index] = (333, pet)
                self.send(
                    "RECV|msgi 0 2614 0 0 0 0 0\n"
                    f"RECV|out 3 {target_id}\n" + self._scp()
                )
            else:
                self.send("RECV|msgi 0 1887 0 0 0 0 0\n")
        elif slot == 2 and target_type == 1:
            self.send(f"RECV|ct 1 {CHAR_ID} 1 {CHAR_ID} 14 8153 1788\n")
            self.send(
                f"RECV|in 3 1497 {SPARRING} 12 8 2 100 100 0 0 0 "
                "-1 0 0 -1 - 0 -1 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
            )
        elif slot == 3 and target_type == 2:
            self.send(f"RECV|ct 1 {CHAR_ID} 2 {target_id} 21 8156 1789\n")
            if target_id in self.extracted:
                return
            self.extracted.add(target_id)
            for slot_index, (_vnum, entity) in list(self.pets.items()):
                if entity == target_id:
                    del self.pets[slot_index]
            self.send(
                "RECV|msgi 0 2627 0 0 0 0 0\n" + self._scp()
            )

    def send(self, text: str) -> None:
        try:
            self.connection.sendall(text.encode())
        except OSError:
            pass

    def close(self) -> None:
        self.stop.set()
        for sock in (self.connection, self.listener):
            if sock is None:
                continue
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass


def main() -> int:
    log = tempfile.NamedTemporaryFile(
        "w", suffix=".txt", delete=False, encoding="latin1"
    )
    log.write("")  # never seed from the running client's own log
    log.close()
    tamer_config = tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    )
    tamer_config.write("{}")
    tamer_config.close()
    os.environ["NTPL_TAMER_CONFIG"] = tamer_config.name
    server = TamerServer()
    app = Application(
        "127.0.0.1", server.port, unique=False, log_file=log.name
    )
    result: dict[str, object] = {"ok": False, "detail": "not checked"}
    state = {"budget": 60}

    def sent_skills() -> list[str]:
        return [
            line[len("SEND|"):]
            for line in server.received
            if line.startswith("SEND|u_s ")
        ]

    def wait_ready() -> bool:
        state["budget"] -= 1
        if app.engine.world.position is None:
            if state["budget"] <= 0:
                result["detail"] = "the position never reached the engine"
                app.quit()
                return False
            return True
        if app.client is None or not app.client.connected:
            return True
        if app.tamer_vnum_spin is None:
            result["detail"] = "the Pet Trainer tab was not built"
            app.quit()
            return False
        if len(app.engine.world.entities) < 3:
            if state["budget"] <= 0:
                result["detail"] = "the chickens never spawned"
                app.quit()
                return False
            return True
        app.notebook.set_current_page(8)  # Pet Trainer
        app.tamer_e2e_toggle.set_active(True)
        app._tamer_start()
        # Test-only shortcuts: a 1 s Tame cooldown and a 1 s training.
        app._tamer_cooldown = lambda slot, default: 1.0
        if app._tamer_config is not None:
            app._tamer_config.train_seconds = 1
        GLib.timeout_add(9000, check_train)
        GLib.timeout_add(20000, check_final)
        return False

    def check_train() -> bool:
        try:
            result["suctl"] = list(server.suctl)
            result["train"] = string_has_suctl()
        except Exception as exc:  # noqa: BLE001
            result["train"] = False
            result["detail"] = f"train exception: {exc!r}"
        return False

    def string_has_suctl() -> bool:
        if not server.suctl:
            return False
        line = server.suctl[0]
        return (
            line.startswith("suctl 20001 2 ")
            and str(PET_A) in line
            and str(PET_B) in line
            and f"3 {SPARRING}" in line
        )

    def check_final() -> bool:
        try:
            skills = sent_skills()
            status = app.status.get_text() if app.status is not None else ""
            tamer_status = (
                app.tamer_status.get_text()
                if app.tamer_status is not None
                else ""
            )
            result["skills"] = skills
            result["status"] = status
            result["tamer_status"] = tamer_status
            result["phase"] = app.tamer_phase
            result["pets"] = dict(app.engine.world.pets)
            result["tamed"] = app.tamer_tamed
            result["extracted"] = app.tamer_extracted
            extract_a = f"u_s 3 2 {PET_A}" in skills
            extract_b = f"u_s 3 2 {PET_B}" in skills
            result["extract"] = extract_a and extract_b
            result["back_to_tame"] = app.tamer_phase == "tame"
            result["ok"] = bool(
                result.get("train")
                and result["extract"]
                and result["extracted"] == 2
                and app.tamer_tamed == 2
            )
            result["detail"] = (
                f"train={result.get('train')} "
                f"extract={result['extract']} "
                f"extracted={result['extracted']} "
                f"tamed={result['tamed']} "
                f"phase={result['phase']} "
                f"skills={skills} "
                f"suctl={server.suctl} "
                f"pets={result['pets']}"
            )
        except Exception as exc:  # noqa: BLE001
            result["ok"] = False
            result["detail"] = f"final exception: {exc!r}"
        app.quit()
        return False

    GLib.timeout_add(200, wait_ready)
    app.run([])
    server.close()
    for path in (log.name, tamer_config.name):
        try:
            os.unlink(path)
        except OSError:
            pass
    print(
        "TAMER SMOKE:",
        "PASS" if result["ok"] else "FAIL",
        result["detail"],
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
