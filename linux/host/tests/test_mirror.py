"""Tests for the cross-client mirror bus (``ntpl.mirror``)."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from ntpl.mirror import (
    EVENT_TTL_S,
    KIND_MOVE,
    KIND_SPELL,
    MirrorEvent,
    MirrorReader,
    MirrorWriter,
    mirror_dir,
)


class MirrorEventTest(unittest.TestCase):
    def test_matches_by_name_case_insensitive(self):
        event = MirrorEvent(kind=KIND_MOVE, at=1.0, name="Follower Friend")
        self.assertTrue(event.matches("follower friend"))
        self.assertFalse(event.matches("someone else"))

    def test_matches_by_char_id(self):
        event = MirrorEvent(kind=KIND_MOVE, at=1.0, char_id=777)
        self.assertTrue(event.matches(char_id=777))
        self.assertFalse(event.matches(char_id=778))
        # A numeric follow entry still matches when the publisher had no name.
        self.assertFalse(event.matches("Follower Friend"))

    def test_event_without_identity_never_matches(self):
        event = MirrorEvent(kind=KIND_MOVE, at=1.0)
        self.assertFalse(event.matches("anyone", 1))


class MirrorBusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.path = self.directory / "client-1-1.jsonl"
        self.writer = MirrorWriter(path=self.path)
        self.reader = MirrorReader(self.directory)
        # Drain whatever a previous writer left behind (there should be none:
        # the writer truncates its file).
        self.reader.poll()

    def tearDown(self):
        self.writer.close()
        self.reader.close()
        self.tmp.cleanup()

    def _round_trip(self):
        return self.reader.poll()

    def test_move_round_trip(self):
        self.writer.move(
            25, 117, speed=10, char_id=777, name="Leader", map_id=20
        )
        events = self._round_trip()
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.kind, KIND_MOVE)
        self.assertEqual((event.x, event.y, event.speed), (25, 117, 10))
        self.assertEqual(event.char_id, 777)
        self.assertEqual(event.name, "Leader")
        self.assertEqual(event.map_id, 20)

    def test_spell_round_trip(self):
        self.writer.spell(
            skill=866,
            slot=0,
            target_type=3,
            target_id=500,
            char_id=777,
            name="Leader",
            map_id=20,
        )
        events = self._round_trip()
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.kind, KIND_SPELL)
        self.assertEqual(event.skill, 866)
        self.assertEqual(event.slot, 0)
        self.assertEqual(event.target_type, 3)
        self.assertEqual(event.target_id, 500)

    def test_self_target_keeps_the_publisher_id(self):
        # The follower decides the substitution; the bus is untouched.
        self.writer.spell(skill=10, slot=1, target_type=1, target_id=777)
        event = self._round_trip()[0]
        self.assertEqual(event.target_type, 1)
        self.assertEqual(event.target_id, 777)

    def test_reader_only_returns_new_lines(self):
        self.writer.move(1, 2)
        self.assertEqual(len(self.reader.poll()), 1)
        self.assertEqual(self.reader.poll(), [])
        self.writer.move(3, 4)
        events = self.reader.poll()
        self.assertEqual([(e.x, e.y) for e in events], [(3, 4)])

    def test_old_events_are_ignored(self):
        old = {
            "v": 1,
            "kind": KIND_MOVE,
            "at": time.time() - EVENT_TTL_S - 5,
            "char": 777,
            "name": "Leader",
            "map": 20,
            "x": 1,
            "y": 2,
        }
        self.path.write_text(json.dumps(old) + "\n", encoding="utf-8")
        reader = MirrorReader(self.directory)
        self.assertEqual(reader.poll(), [])

    def test_malformed_lines_are_skipped(self):
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write("not json\n")
            handle.write('{"kind": "other", "at": 1}\n')
            handle.write('{"kind": "move", "at": %f, "x": 5, "y": 6}\n'
                         % time.time())
        events = self.reader.poll()
        self.assertEqual([(e.x, e.y) for e in events], [(5, 6)])

    def test_partial_line_is_carried_until_complete(self):
        payload = json.dumps(
            {"v": 1, "kind": KIND_MOVE, "at": time.time(), "x": 7, "y": 8}
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(payload[:10])
        self.assertEqual(self.reader.poll(), [])
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(payload[10:] + "\n")
        events = self.reader.poll()
        self.assertEqual([(e.x, e.y) for e in events], [(7, 8)])

    def test_truncated_file_restarts_from_zero(self):
        self.writer.move(1, 1)
        self.assertEqual(len(self.reader.poll()), 1)
        self.writer.close()
        # A new publisher process reuses the path and truncates it.
        writer = MirrorWriter(path=self.path)
        try:
            writer.move(9, 9)
            events = self.reader.poll()
            self.assertEqual([(e.x, e.y) for e in events], [(9, 9)])
        finally:
            writer.close()

    def test_writer_recreates_a_pruned_file(self):
        # A pruning writer elsewhere deletes an idle publisher's file; the
        # next action must recreate the path rather than append to an
        # unlinked inode.
        self.path.unlink()
        self.writer.move(5, 6)
        self.assertTrue(self.path.is_file())
        events = self.reader.poll()
        self.assertEqual([(e.x, e.y) for e in events], [(5, 6)])

    def test_writer_default_path_is_inside_the_directory(self):
        writer = MirrorWriter(directory=self.directory)
        try:
            self.assertEqual(writer.path.parent, self.directory)
            self.assertTrue(writer.path.name.startswith("client-"))
            self.assertTrue(writer.available)
        finally:
            writer.close()

    def test_env_override_changes_the_directory(self):
        old = os.environ.get("NTPL_MIRROR_DIR")
        os.environ["NTPL_MIRROR_DIR"] = str(self.directory)
        try:
            self.assertEqual(mirror_dir(), self.directory)
        finally:
            if old is None:
                os.environ.pop("NTPL_MIRROR_DIR", None)
            else:
                os.environ["NTPL_MIRROR_DIR"] = old


if __name__ == "__main__":
    unittest.main()
