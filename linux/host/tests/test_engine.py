import unittest

from ntpl.engine import Engine
from ntpl.protocol import Kind, Packet


def packet(direction: Kind, text: str) -> Packet:
    return Packet(direction, text, 0.0)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = Engine()

    def test_walk_sets_position(self):
        world = self.engine.feed(packet(Kind.SEND, "walk 19 146 0 10"))
        self.assertEqual(world.position, (19, 146))

    def test_map_change_clears_entities(self):
        self.engine.feed(packet(Kind.RECV, "in 3 500 42 10 20 2 0"))
        self.assertEqual(len(self.engine.world.entities), 1)

        world = self.engine.feed(packet(Kind.RECV, "c_map 100 0"))
        self.assertEqual(world.map_id, 100)
        self.assertEqual(world.entities, {})

    def test_at_uses_map_id_and_coordinates(self):
        # at <charId> <mapId> <x> <y> ...
        world = self.engine.feed(
            packet(Kind.RECV, "at 4165102 1 105 23 2 0 0 0 0 0 0")
        )
        self.assertEqual(world.map_id, 1)
        self.assertEqual(world.position, (105, 23))

    def test_player_spawn_uses_char_id_name_and_position(self):
        # Documented format: in 1 <name> - <charId> <x> <y> ...
        world = self.engine.feed(
            packet(
                Kind.RECV,
                "in 1 Guder - 4165102 105 23 2 0 1 2 42 1 0 100 100 0 0 0 1",
            )
        )
        entity = world.entities[4165102]
        self.assertEqual(entity.name, "Guder")
        self.assertEqual((entity.kind, entity.x, entity.y), (1, 105, 23))

    def test_npc_spawn_is_keyed_by_entity_id_not_vnum(self):
        # Two NPCs may share a vnum; mv/out use the entity id.
        line = "in 2 2557 2230354 104 24 2 96 100 0 0 3 4165102 1 0 2379 Graham"
        world = self.engine.feed(packet(Kind.RECV, line))
        self.assertIn(2230354, world.entities)
        self.assertNotIn(2557, world.entities)
        entity = world.entities[2230354]
        self.assertEqual((entity.kind, entity.x, entity.y), (2, 104, 24))

    def test_move_creates_and_updates_entity(self):
        world = self.engine.feed(packet(Kind.RECV, "mv 3 42 10 20 5"))
        entity = world.entities[42]
        self.assertEqual((entity.kind, entity.x, entity.y), (3, 10, 20))

        world = self.engine.feed(packet(Kind.RECV, "mv 3 42 11 21 5"))
        self.assertEqual((world.entities[42].x, world.entities[42].y), (11, 21))

    def test_despawn(self):
        self.engine.feed(packet(Kind.RECV, "in 3 500 7 10 20 2 0"))
        world = self.engine.feed(packet(Kind.RECV, "out 1 7"))
        self.assertNotIn(7, world.entities)

    def test_stats(self):
        world = self.engine.feed(packet(Kind.RECV, "stat 100 200 50 120"))
        self.assertEqual(
            (world.stats.hp, world.stats.hp_max, world.stats.mp, world.stats.mp_max),
            (100, 200, 50, 120),
        )

    def test_chat_keeps_message_whole(self):
        world = self.engine.feed(packet(Kind.RECV, "say 1 7 hello world"))
        self.assertEqual(world.chat[-1], (1, 7, "hello world"))

    def test_teleport(self):
        world = self.engine.feed(packet(Kind.RECV, "tp 1 7 30 40"))
        self.assertEqual((world.entities[7].x, world.entities[7].y), (30, 40))

    def test_live_tp_updates_player_position(self):
        # Real packet observed when the server accepted an injected walk.
        world = self.engine.feed(packet(Kind.RECV, "tp 1 14881134 25 117 0"))
        entity = world.entities[14881134]
        self.assertEqual((entity.kind, entity.x, entity.y), (1, 25, 117))

    def test_rest_state(self):
        world = self.engine.feed(packet(Kind.RECV, "rest 1 14881134 1"))
        self.assertTrue(world.resting)
        world = self.engine.feed(packet(Kind.RECV, "rest 1 14881134 0"))
        self.assertFalse(world.resting)

    def test_condition_sets_speed(self):
        world = self.engine.feed(packet(Kind.RECV, "cond 1 14881134 0 0 10"))
        self.assertEqual(world.entities[14881134].speed, 10)

    def test_malformed_packets_do_not_raise(self):
        for text in ("stat x y z", "mv", "in", "say only-two", "walk"):
            self.engine.feed(packet(Kind.RECV, text))

    def test_unknown_opcode_is_ignored(self):
        world = self.engine.feed(packet(Kind.RECV, "completely_new 1 2 3"))
        self.assertIsNotNone(world.last_packet)

    def test_custom_handler(self):
        seen = []
        self.engine.on("custom", lambda world, pkt: seen.append(pkt.args))
        self.engine.feed(packet(Kind.RECV, "custom a b"))
        self.assertEqual(seen, [["a", "b"]])


if __name__ == "__main__":
    unittest.main()
