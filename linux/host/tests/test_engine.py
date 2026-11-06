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
        self.engine.feed(packet(Kind.RECV, "in 3 42"))
        self.assertEqual(len(self.engine.world.entities), 1)

        world = self.engine.feed(packet(Kind.RECV, "c_map 100 0"))
        self.assertEqual(world.map_id, 100)
        self.assertEqual(world.entities, {})

    def test_move_creates_and_updates_entity(self):
        world = self.engine.feed(packet(Kind.RECV, "mv 3 42 10 20 5"))
        entity = world.entities[42]
        self.assertEqual((entity.kind, entity.x, entity.y), (3, 10, 20))

        world = self.engine.feed(packet(Kind.RECV, "mv 3 42 11 21 5"))
        self.assertEqual((world.entities[42].x, world.entities[42].y), (11, 21))

    def test_despawn(self):
        self.engine.feed(packet(Kind.RECV, "in 1 7"))
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
