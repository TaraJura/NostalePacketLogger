import time
import unittest

from ntpl.engine import Engine, walk_checksum
from ntpl.protocol import Kind, Packet


def packet(direction: Kind, text: str) -> Packet:
    return Packet(direction, text, 0.0)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = Engine()

    def test_walk_sets_position(self):
        world = self.engine.feed(packet(Kind.SEND, "walk 19 146 0 10"))
        self.assertEqual(world.position, (19, 146))

    def test_walk_probe_does_not_move_world(self):
        world = self.engine.feed(packet(Kind.SEND, "walk 19 146 0 10"))
        world = self.engine.feed(packet(Kind.SEND, "walk -1 -1 0 10"))
        self.assertEqual(world.position, (19, 146))

    def test_walk_sets_facing(self):
        self.engine.feed(packet(Kind.SEND, "walk 19 147 0 10"))
        world = self.engine.feed(packet(Kind.SEND, "walk 19 146 0 10"))
        self.assertEqual(world.facing, (0, -1))  # up
        self.engine.feed(packet(Kind.SEND, "walk 20 146 0 10"))
        self.assertEqual(world.facing, (1, 0))   # right
        self.engine.feed(packet(Kind.SEND, "walk 20 147 0 10"))
        self.assertEqual(world.facing, (0, 1))   # down
        self.engine.feed(packet(Kind.SEND, "walk 19 147 0 10"))
        self.assertEqual(world.facing, (-1, 0))  # left

    def test_walk_diagonal_facing(self):
        self.engine.feed(packet(Kind.SEND, "walk 10 10 0 10"))
        world = self.engine.feed(packet(Kind.SEND, "walk 11 11 0 10"))
        self.assertEqual(world.facing, (1, 1))

    def test_probe_keeps_facing(self):
        self.engine.feed(packet(Kind.SEND, "walk 10 10 0 10"))
        world = self.engine.feed(packet(Kind.SEND, "walk 11 10 0 10"))
        world = self.engine.feed(packet(Kind.SEND, "walk -1 -1 0 10"))
        self.assertEqual(world.facing, (1, 0))
        self.assertEqual(world.position, (11, 10))

    def test_walk_checksum_matches_client_builder(self):
        # (x + y) % 3 == 1 -> 1, else 0. Verified live: (19,138) with 1 moved
        # the character; the same target with 0 did not.
        self.assertEqual(walk_checksum(22, 123), 1)
        self.assertEqual(walk_checksum(25, 117), 1)
        self.assertEqual(walk_checksum(19, 138), 1)
        self.assertEqual(walk_checksum(20, 140), 1)
        self.assertEqual(walk_checksum(20, 139), 0)
        self.assertEqual(walk_checksum(20, 138), 0)
        self.assertEqual(walk_checksum(25, 118), 0)

    def test_map_change_clears_entities(self):
        self.engine.feed(packet(Kind.RECV, "in 3 500 42 10 20 2 0"))
        self.assertEqual(len(self.engine.world.entities), 1)

        # Live: c_map <type> <mapId> <state>; the first field is not the id.
        world = self.engine.feed(packet(Kind.RECV, "c_map 0 100 1"))
        self.assertEqual(world.map_id, 100)
        self.assertEqual(world.entities, {})

    def test_live_map_change_sequence_keeps_new_map(self):
        # Captured live when walking through a portal (16:28:42):
        #   c_map 0 20 0 / mapout / at ... 85 ... / c_map 0 85 1
        # The trailing c_map used to overwrite map_id with 0, so the UI never
        # switched to map 85.
        self.engine.feed(packet(Kind.RECV, "c_map 0 20 0"))
        self.engine.feed(packet(Kind.RECV, "mapout"))
        world = self.engine.feed(
            packet(Kind.RECV, "at 14881134 85 6 8 2 0 13 1 -1 0 0")
        )
        world = self.engine.feed(packet(Kind.RECV, "c_map 0 85 1"))
        self.assertEqual(world.map_id, 85)
        self.assertEqual(world.position, (6, 8))

        # And back again.
        self.engine.feed(packet(Kind.RECV, "c_map 0 85 0"))
        self.engine.feed(packet(Kind.RECV, "mapout"))
        self.engine.feed(
            packet(Kind.RECV, "at 14881134 20 18 190 2 0 11 1 -1 0 0")
        )
        world = self.engine.feed(packet(Kind.RECV, "c_map 0 20 1"))
        self.assertEqual(world.map_id, 20)
        self.assertEqual(world.position, (18, 190))

    def test_at_map_change_clears_stale_entities(self):
        self.engine.feed(packet(Kind.RECV, "c_map 0 20 1"))
        self.engine.feed(packet(Kind.RECV, "in 3 500 42 10 20 2 0"))
        world = self.engine.feed(
            packet(Kind.RECV, "at 14881134 85 6 8 2 0 13 1 -1 0 0")
        )
        self.assertEqual(world.map_id, 85)
        self.assertEqual(world.entities, {})

    def test_at_uses_map_id_and_coordinates(self):
        # at <charId> <mapId> <x> <y> ...
        world = self.engine.feed(
            packet(Kind.RECV, "at 4165102 1 105 23 2 0 0 0 0 0 0")
        )
        self.assertEqual(world.map_id, 1)
        self.assertEqual(world.position, (105, 23))
        self.assertEqual(world.self_id, 4165102)

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
        # This short synthetic packet has no family fields: do not guess.
        self.assertFalse(entity.family_known)

    def test_party_list_learns_names_including_the_local_player(self):
        # Live 2026-11-26: rdlst <a> <b> <c> <level>.<x>.<y>.<f>.<name>...
        self.engine.feed(
            packet(Kind.RECV, "at 7001 2628 76 73 2 0 115 1 -1 0 0")
        )
        world = self.engine.feed(
            packet(
                Kind.RECV,
                "rdlst 85 99 17 99.18.2.0.SleepyIRL.1.22.69 "
                "89.14.3.0.root1.0.7001.30 94.24.2.0.Oxygen.1.2035.53",
            )
        )
        self.assertEqual(world.player_names[22], "SleepyIRL")
        self.assertEqual(world.player_names[7001], "root1")
        self.assertEqual(world.self_name, "root1")
        # The same list is the raid-team membership: the local player is in
        # it, which is what blocks the production minigames in the game.
        self.assertEqual(world.raid_members, {22, 7001, 2035})
        self.assertGreater(world.raid_at, 0.0)

    def test_raid_state_packets_track_joining_and_leaving(self):
        # Live 2026-11-26: joining sends `rdlst` + `raid 1 1` + `raid 2
        # <leaderId>`; leaving sends only `raid 1 0` + `raid 2 -1`.
        self.engine.feed(
            packet(Kind.RECV, "at 7001 20001 76 73 2 0 115 1 -1 0 0")
        )
        world = self.engine.feed(
            packet(
                Kind.RECV,
                "rdlst 81 99 7 99.3.1.0.Chinain.1.1168.71 "
                "89.7.3.0.root1.0.7001.30",
            )
        )
        self.assertIsNone(world.raid_active)
        world = self.engine.feed(packet(Kind.RECV, "raid 1 1"))
        self.assertTrue(world.raid_active)
        world = self.engine.feed(packet(Kind.RECV, "raid 2 1168"))
        self.assertEqual(world.raid_leader, 1168)
        self.assertEqual(world.raid_members, {1168, 7001})

        # Leaving: the server sends no new rdlst, only the state; the old
        # membership must be gone immediately.
        world = self.engine.feed(packet(Kind.RECV, "raid 1 0"))
        self.assertFalse(world.raid_active)
        self.assertEqual(world.raid_members, set())
        self.assertEqual(world.raid_at, 0.0)
        world = self.engine.feed(packet(Kind.RECV, "raid 2 -1"))
        self.assertEqual(world.raid_leader, -1)

    def test_status_tracks_monster_hp(self):
        # Live 2026-11-27: st 3 <id> <level> <unknown> <state> <unknown>
        # <hp> <unknown> <hpMax> ... The Pet Trainer tamer stops hitting at
        # the HP limit; HP 0 is the death notice and removes the entity.
        world = self.engine.feed(
            packet(
                Kind.RECV,
                "in 3 333 3039 21 151 2 100 100 0 0 0 -1 0 0 -1 - 0 -1 "
                "0 0 0 0 0 0 0 0 0 0 0 1 0",
            )
        )
        world = self.engine.feed(
            packet(Kind.RECV, "st 3 3039 1 0 1 100 20 10 100 10 0")
        )
        entity = world.entities[3039]
        self.assertEqual((entity.hp, entity.hp_max), (20, 100))
        world = self.engine.feed(
            packet(Kind.RECV, "st 3 3039 1 0 1 100 0 10 100 10 0")
        )
        self.assertNotIn(3039, world.entities)

    def test_pet_slots_and_system_messages(self):
        # sc_p <slot> <vnum> <entityId> ... and p_clear (the server
        # repopulates the pet panel after a tame).
        world = self.engine.feed(packet(Kind.RECV, "p_clear"))
        world = self.engine.feed(
            packet(Kind.RECV, "sc_p 4 333 1452945 1 3000 0 0 0 32")
        )
        world = self.engine.feed(
            packet(Kind.RECV, "sc_p 5 333 1452978 1 3000 0 0 0 32")
        )
        self.assertEqual(world.pets, {4: 333, 5: 333})
        self.assertEqual(
            world.pet_entities, {4: 1452945, 5: 1452978}
        )

        # msgi <type> <id>: the Game18N key a feature reacts to.
        world = self.engine.feed(packet(Kind.RECV, "msgi 0 2614 0 0 0 0 0"))
        self.assertEqual(world.last_message_id, 2614)
        seq = world.message_seq
        world = self.engine.feed(packet(Kind.RECV, "msgi 0 1887 0 0 0 0 0"))
        self.assertEqual(world.last_message_id, 1887)
        self.assertEqual(world.message_seq, seq + 1)

        world = self.engine.feed(packet(Kind.RECV, "p_clear"))
        self.assertEqual(world.pets, {})
        self.assertEqual(world.pet_entities, {})

    def test_cast_start_is_recorded(self):
        # ct <casterType> <casterId> <targetType> <targetId> <x> <y> <skill>
        world = self.engine.feed(
            packet(Kind.RECV, "ct 1 7001 3 3039 20 8153 1787")
        )
        self.assertEqual(world.last_cast, (1, 7001, 3039, 1787))
        seq = world.cast_seq
        world = self.engine.feed(
            packet(Kind.RECV, "ct 1 7001 3 3038 20 8153 1787")
        )
        self.assertEqual(world.last_cast, (1, 7001, 3038, 1787))
        self.assertEqual(world.cast_seq, seq + 1)
        # A malformed/short line must not change the last cast.
        world = self.engine.feed(packet(Kind.RECV, "ct 1 7001"))
        self.assertEqual(world.last_cast, (1, 7001, 3038, 1787))
        self.assertEqual(world.cast_seq, seq + 1)
        # The log keeps the sequence so a busy map cannot hide our cast.
        self.assertEqual(world.cast_log[-1], (seq + 1, 1, 7001, 3038, 1787))

    def test_player_spawn_reads_family(self):
        # Live map 20 line: family id 70.918 and family name at the end.
        world = self.engine.feed(
            packet(
                Kind.RECV,
                "in 1 S3nat - 26 34 52 2 0 1 0 9 2 "
                "-1.4478.4450.4466.-1.885.4577.4579.-1.8568.8908 "
                "100 100 0 -1 0 2 0 0 0 1000009 75 75 "
                "70.918 6ix7even 14 0 0 0 0 85 1 0|0|0 0 0 100 10 0",
            )
        )
        entity = world.entities[26]
        self.assertTrue(entity.family_known)
        self.assertEqual(entity.family_id, 70)
        self.assertEqual(entity.family_name, "6ix7even")

    def test_player_spawn_without_family(self):
        world = self.engine.feed(
            packet(
                Kind.RECV,
                "in 1 Retye - 3913 25 43 2 0 1 0 9 2 "
                "-1.769.404.763.-1.885.-1.-1.-1.-1.-1 "
                "100 100 0 -1 0 2 0 0 0 1000009 75 75 "
                "-1 - -2 0 0 0 0 81 0 0|0|0 0 0 100 0 0",
            )
        )
        entity = world.entities[3913]
        self.assertTrue(entity.family_known)
        self.assertEqual(entity.family_id, -1)
        self.assertEqual(entity.family_name, "")

    def test_gidx_updates_player_family(self):
        self.engine.feed(
            packet(
                Kind.RECV,
                "in 1 Retye - 3913 25 43 2 0 1 0 9 2 "
                "-1.769.404.763.-1.885.-1.-1.-1.-1.-1 "
                "100 100 0 -1 0 2 0 0 0 1000009 75 75 "
                "-1 - -2 0 0 0 0 81 0 0|0|0 0 0 100 0 0",
            )
        )
        world = self.engine.feed(
            packet(Kind.RECV, "gidx 1 3913 163.918 Kartushka 1 0|0|0")
        )
        entity = world.entities[3913]
        self.assertTrue(entity.family_known)
        self.assertEqual(entity.family_id, 163)
        self.assertEqual(entity.family_name, "Kartushka")
        world = self.engine.feed(
            packet(Kind.RECV, "gidx 1 3913 -1 - 0 0|0|0")
        )
        self.assertEqual(world.entities[3913].family_id, -1)
        self.assertEqual(world.entities[3913].family_name, "")

    def test_player_spawn_reads_fairy_from_equipment(self):
        world = self.engine.feed(
            packet(
                Kind.RECV,
                "in 1 FairyGuy - 5002 25 119 2 0 1 0 9 2 "
                "-1.0.0.-1.-1.885.-1.-1.-1.-1 "
                "100 100 0 -1 0 2 0 0 0 1000009 75 75 "
                "70.918 SomeFam 14 0 0 0 0 85 1 0|0|0 0 0 100 10 0",
            )
        )
        entity = world.entities[5002]
        self.assertTrue(entity.fairy_known)
        self.assertEqual(entity.fairy_vnum, 885)
        self.assertTrue(entity.has_fairy)

    def test_player_spawn_without_fairy(self):
        world = self.engine.feed(
            packet(
                Kind.RECV,
                "in 1 NoFairy - 5004 25 121 2 0 1 0 9 2 "
                "-1.0.0.-1.-1.-1.-1.-1.-1.-1 "
                "100 100 0 -1 0 2 0 0 0 1000009 75 75 "
                "-1 - -2 0 0 0 0 81 0 0|0|0 0 0 100 0 0",
            )
        )
        entity = world.entities[5004]
        self.assertTrue(entity.fairy_known)
        self.assertEqual(entity.fairy_vnum, -1)
        self.assertFalse(entity.has_fairy)

    def test_pairy_marks_fairy_element(self):
        self.engine.feed(
            packet(
                Kind.RECV,
                "in 1 NoFairy - 5004 25 121 2 0 1 0 9 2 "
                "-1.0.0.-1.-1.-1.-1.-1.-1.-1 "
                "100 100 0 -1 0 2 0 0 0 1000009 75 75 "
                "-1 - -2 0 0 0 0 81 0 0|0|0 0 0 100 0 0",
            )
        )
        world = self.engine.feed(
            packet(Kind.RECV, "pairy 1 5004 4 3 30 0")
        )
        entity = world.entities[5004]
        self.assertEqual(entity.fairy_element, 3)
        self.assertTrue(entity.has_fairy)

    def test_gidx_does_not_create_unknown_entities(self):
        world = self.engine.feed(
            packet(Kind.RECV, "gidx 1 9999 -1 - 0 0|0|0")
        )
        self.assertNotIn(9999, world.entities)

    def test_skill_list_maps_u_s_slots(self):
        # Live: ski 0 866 866 866 867 ... 876; u_s 10 -> server su 876,
        # u_s 7 -> 873, u_s 2 -> 868. The slot is the index into the
        # deduplicated list.
        world = self.engine.feed(
            packet(
                Kind.RECV,
                "ski 0 866 866 866 867 868 869 870 871 872 873 874 "
                "875 876 30 37 42",
            )
        )
        self.assertEqual(world.skill_list[:11], list(range(866, 877)))
        self.assertEqual(world.skill_slot(876), 10)
        self.assertEqual(world.skill_vnum(7), 873)
        self.assertEqual(world.skill_slot(999), None)

    def test_monster_spawn_records_template_vnum(self):
        # Live: in 3 206 5122 132 182 ...
        world = self.engine.feed(
            packet(
                Kind.RECV,
                "in 3 206 5122 132 182 2 100 100 0 0 0 -1 0 0 -1 - 0 -1 0",
            )
        )
        entity = world.entities[5122]
        self.assertEqual(
            (entity.kind, entity.vnum, entity.x, entity.y), (3, 206, 132, 182)
        )

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

    def test_death_status_removes_the_entity(self):
        # Live: the server never sends `out` for a killed monster; `st` with
        # zero current HP is the death notice.
        self.engine.feed(packet(Kind.RECV, "in 3 500 7 10 20 2 0"))
        world = self.engine.feed(
            packet(Kind.RECV, "st 3 7 14 0 1 100 0 108 481 108 0")
        )
        self.assertNotIn(7, world.entities)

    def test_alive_status_keeps_the_entity(self):
        self.engine.feed(packet(Kind.RECV, "in 3 500 7 10 20 2 0"))
        world = self.engine.feed(
            packet(Kind.RECV, "st 3 7 14 0 100 100 481 108 481 108 0")
        )
        self.assertIn(7, world.entities)

    def test_short_or_bad_status_is_ignored(self):
        self.engine.feed(packet(Kind.RECV, "in 3 500 7 10 20 2 0"))
        world = self.engine.feed(packet(Kind.RECV, "st 3 7 14"))
        self.assertIn(7, world.entities)
        world = self.engine.feed(packet(Kind.RECV, "st x 7 14 0 1 100 0 1 2"))
        self.assertIn(7, world.entities)

    def test_cancel_counts(self):
        self.engine.feed(packet(Kind.RECV, "cancel 0 0 -1"))
        world = self.engine.feed(packet(Kind.RECV, "cancel 0 0 -1"))
        self.assertEqual(world.cancel_count, 2)

    def test_skill_ready_tracks_plain_sr_only(self):
        # sr <castingId>: the server's cooldown for that skill ended. The
        # stored time is monotonic, like the GUI's bot clock.
        before = time.monotonic()
        world = self.engine.feed(Packet(Kind.RECV, "sr 2", 12.5))
        ready = world.skill_ready_at.get(2)
        self.assertIsNotNone(ready)
        self.assertGreaterEqual(ready, before)
        # The custom cooldown-start form is not a readiness signal.
        world = self.engine.feed(
            Packet(Kind.RECV, "sr -10 876 900 0", 13.0)
        )
        self.assertEqual(world.skill_ready_at, {2: ready})

    def test_drop_and_get(self):
        # Live: drop 1046 13961541 29 180 635 0 14881134
        world = self.engine.feed(
            packet(Kind.RECV, "drop 1046 13961541 29 180 635 0 14881134")
        )
        drop = world.drops[13961541]
        self.assertEqual(
            (drop.item_vnum, drop.x, drop.y, drop.amount, drop.owner_id),
            (1046, 29, 180, 635, 14881134),
        )
        world = self.engine.feed(
            packet(Kind.RECV, "get 1 14881134 13961541 0")
        )
        self.assertNotIn(13961541, world.drops)

    def test_get_send_does_not_remove_drop(self):
        self.engine.feed(
            packet(Kind.RECV, "drop 5962 13962833 27 180 1 0 14881134")
        )
        world = self.engine.feed(
            packet(Kind.SEND, "get 1 14881134 13962833")
        )
        # Only the server's four-field `get` confirmation removes it.
        self.assertIn(13962833, world.drops)

    def test_map_change_clears_drops(self):
        self.engine.feed(
            packet(Kind.RECV, "drop 5962 13962833 27 180 1 0 14881134")
        )
        world = self.engine.feed(packet(Kind.RECV, "c_map 0 100 1"))
        self.assertEqual(world.drops, {})

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
        self.assertEqual(world.self_id, 14881134)
        world = self.engine.feed(packet(Kind.RECV, "rest 1 14881134 0"))
        self.assertFalse(world.resting)

    def test_condition_sets_speed_without_creating_ghost(self):
        world = self.engine.feed(packet(Kind.RECV, "cond 1 14881134 0 0 10"))
        self.assertNotIn(14881134, world.entities)
        # The live client sends cond only for the local player.
        self.assertEqual(world.self_id, 14881134)
        self.engine.feed(packet(Kind.RECV, "tp 1 14881134 10 10 0"))
        world = self.engine.feed(packet(Kind.RECV, "cond 1 14881134 0 0 10"))
        self.assertEqual(world.entities[14881134].speed, 10)

    def test_teleport_for_self_updates_world_position(self):
        self.engine.feed(packet(Kind.RECV, "cond 1 14881134 0 0 10"))
        world = self.engine.feed(packet(Kind.RECV, "tp 1 14881134 25 117 0"))
        self.assertEqual(world.position, (25, 117))

    def test_move_for_self_updates_world_position(self):
        self.engine.feed(packet(Kind.RECV, "cond 1 14881134 0 0 10"))
        world = self.engine.feed(packet(Kind.RECV, "mv 1 14881134 26 118 10"))
        self.assertEqual(world.position, (26, 118))

    def test_skill_used_marks_the_attacker(self):
        self.engine.feed(packet(Kind.RECV, "rest 1 14881134 0"))
        world = self.engine.feed(
            packet(Kind.RECV, "su 3 500 1 14881134 10 9 11 4401")
        )
        self.assertEqual(world.last_attacker_id, 500)
        self.assertIn(500, world.self_attackers)
        self.assertEqual(world.self_hit_count, 0)
        # Our own attack on a monster must not be treated as retaliation.
        world = self.engine.feed(
            packet(Kind.RECV, "su 1 14881134 3 501 0 9 11 4401")
        )
        self.assertEqual(world.last_attacker_id, 500)
        self.assertNotIn(501, world.self_attackers)
        # ... but it counts as a landed hit, so the server's trailing
        # `cancel 0 0 -1` is not mistaken for a refused attack.
        self.assertEqual(world.self_hit_count, 1)

    def test_zero_hp_marks_dead_and_revive_clears_it(self):
        self.engine.feed(
            packet(Kind.RECV, "at 14881134 20 25 117 0 0 0 0 0 0 0")
        )
        world = self.engine.feed(
            packet(Kind.RECV, "stat 0 2791 100 3827 0 0")
        )
        self.assertTrue(world.dead)
        world = self.engine.feed(
            packet(Kind.RECV, "revive 1 14881134 0")
        )
        self.assertFalse(world.dead)

    def test_revival_dialog_is_detected_and_cleared_by_revive(self):
        self.engine.feed(
            packet(Kind.RECV, "at 14881134 20 25 117 0 0 0 0 0 0 0")
        )
        self.engine.feed(packet(Kind.RECV, "stat 0 2791 100 3827 0 0"))
        world = self.engine.feed(
            packet(Kind.RECV, "dlgi #revival^0 #revival^1 571 0 0")
        )
        self.assertTrue(world.revival_offered)
        world = self.engine.feed(
            packet(Kind.RECV, "revive 1 14881134 0")
        )
        self.assertFalse(world.revival_offered)
        self.assertFalse(world.dead)

    def test_revive_for_another_character_keeps_dead(self):
        self.engine.feed(
            packet(Kind.RECV, "at 14881134 20 25 117 0 0 0 0 0 0 0")
        )
        self.engine.feed(packet(Kind.RECV, "stat 0 2791 100 3827 0 0"))
        world = self.engine.feed(
            packet(Kind.RECV, "revive 1 14825946 0")
        )
        self.assertTrue(world.dead)
        world = self.engine.feed(
            packet(Kind.RECV, "stat 1 2791 100 3827 0 0")
        )
        self.assertFalse(world.dead)

    def test_skill_used_event(self):
        # Real packet observed in the live log.
        world = self.engine.feed(
            packet(
                Kind.RECV,
                "su 1 14877915 3 5180 866 9 11 4401 0 0 0 0 6507 0 0 0 4140",
            )
        )
        event = world.combat[-1]
        self.assertEqual(event["attacker_id"], 14877915)
        self.assertEqual(event["target_id"], 5180)
        self.assertEqual(event["skill"], 866)
        self.assertEqual(world.last_skill, 866)

    def test_target_selection(self):
        world = self.engine.feed(packet(Kind.SEND, "ncif 3 5180"))
        self.assertEqual(world.target_id, 5180)

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


class SkillSlotLearningTests(unittest.TestCase):
    """SP-card basic skill and `u_s` slot mapping without ``ski``.

    A host that attaches after login never sees the skill list. Pairing the
    client's own `u_s <slot>` with the answering `su ... <vnum>` still maps
    the used slots, and slot 0 is the equipped card's basic attack.
    """

    def setUp(self):
        self.engine = Engine()
        # `at` teaches self_id: at <charId> <mapId> <x> <y> ...
        self.engine.feed(
            packet(Kind.RECV, "at 14881134 2 110 141 2 0 1 1 -1 0 0")
        )

    def test_basic_attack_learns_the_sp_card_skill(self):
        self.engine.feed(packet(Kind.SEND, "u_s 0 3 3161"))
        world = self.engine.feed(
            packet(Kind.RECV, "su 1 14881134 3 3161 260 5 11 594")
        )
        self.assertEqual(world.basic_skill, 260)
        self.assertEqual(world.skill_learned, {0: 260})
        self.assertEqual(world.skill_slot(260), 0)
        self.assertEqual(world.skill_vnum(0), 260)

    def test_spell_slot_learns_without_ski(self):
        self.engine.feed(packet(Kind.SEND, "u_s 5 3 3161"))
        world = self.engine.feed(
            packet(Kind.RECV, "su 1 14881134 3 3161 855 5 11 594")
        )
        self.assertEqual(world.skill_learned, {5: 855})
        self.assertIsNone(world.basic_skill)
        self.assertEqual(world.skill_slot(855), 5)

    def test_another_players_hit_does_not_teach(self):
        self.engine.feed(packet(Kind.SEND, "u_s 0 3 3161"))
        world = self.engine.feed(
            packet(Kind.RECV, "su 1 14877915 3 3161 866 5 11 594")
        )
        self.assertIsNone(world.basic_skill)
        self.assertEqual(world.skill_learned, {})

    def test_stale_send_is_not_paired(self):
        self.engine.feed(packet(Kind.SEND, "u_s 0 3 3161"))
        world = self.engine.feed(
            Packet(Kind.RECV, "su 1 14881134 3 3161 260 5 11 594", 10.0)
        )
        self.assertIsNone(world.basic_skill)
        self.assertEqual(world.skill_learned, {})

    def test_ski_slot_zero_is_the_basic_skill(self):
        world = self.engine.feed(packet(Kind.RECV, "ski 0 260 855 262"))
        self.assertEqual(world.basic_skill, 260)
        self.assertEqual(world.skill_slot(855), 1)

    def test_another_players_rest_does_not_take_over_self(self):
        world = self.engine.feed(packet(Kind.RECV, "rest 1 11361583 0"))
        self.assertEqual(world.self_id, 14881134)
        self.assertIsNone(world.resting)


class PortalLearningTests(unittest.TestCase):
    """A portal is a server-side warp tile, learned from an actual crossing."""

    def setUp(self):
        self.engine = Engine()
        self.engine.feed(
            packet(Kind.RECV, "at 14881134 103 20 20 2 0 24 1 -1 0 0")
        )

    def test_crossing_records_both_sides(self):
        self.engine.feed(packet(Kind.SEND, "walk 20 20 1 10"))
        self.engine.feed(packet(Kind.RECV, "c_map 0 103 0"))
        self.engine.feed(packet(Kind.RECV, "mapout"))
        world = self.engine.feed(
            packet(Kind.RECV, "at 14881134 104 11 9 2 0 25 1 -1 0 0")
        )
        # The departure cell is the portal on the old map; the arrival cell is
        # the return portal on the new one, so both maps show a marker.
        self.assertEqual(
            world.portals,
            {(103, 20, 20): 104, (104, 11, 9): 103},
        )

    def test_plain_teleport_without_leaving_map_is_not_a_portal(self):
        world = self.engine.feed(
            packet(Kind.RECV, "at 14881134 104 11 9 2 0 25 1 -1 0 0")
        )
        self.assertEqual(world.portals, {})

    def test_same_map_at_clears_the_pending_departure(self):
        self.engine.feed(packet(Kind.RECV, "c_map 0 103 0"))
        self.engine.feed(
            packet(Kind.RECV, "at 14881134 103 21 21 2 0 24 1 -1 0 0")
        )
        self.assertEqual(self.engine.world.portals, {})

    def test_waypoint_list_adds_every_timespace(self):
        # `wp` also carries no map id: it follows `at` on map entry.
        self.engine.feed(
            packet(Kind.RECV, "at 14881134 4 28 178 2 0 2 1 -1 0 0")
        )
        for line in ("wp 35 142 16 4 26 99", "wp 229 33 704 0 90 99"):
            world = self.engine.feed(packet(Kind.RECV, line))
        entry = world.timespaces[(4, 35, 142)]
        self.assertEqual(
            (entry.ts_id, entry.min_level, entry.max_level), (16, 26, 99)
        )
        entry = world.timespaces[(4, 229, 33)]
        self.assertEqual(
            (entry.ts_id, entry.min_level, entry.max_level), (704, 90, 99)
        )

    def test_client_shortcut_pairs_with_the_bazaar_window(self):
        # FrosTale: a bare client `c_skill` is answered by `wopen 32`.
        self.engine.feed(Packet(Kind.SEND, "c_skill", 100.0))
        self.engine.feed(Packet(Kind.RECV, "wopen 32 2 626", 100.2))
        self.assertTrue(self.engine.world.bazar.open_skill)

    def test_seller_list_tracks_own_listings(self):
        world = self.engine.feed(
            Packet(
                Kind.RECV,
                "rc_slist 0  1 60891|7001|5370|0|5|0|84449|1|1341|2|1|0|0|0|0|^0",
                0.0,
            )
        )
        self.assertEqual(world.bazar.seller_count, 1)
        entry = world.bazar.seller_entries[0]
        self.assertEqual(
            (entry.listing_id, entry.owner_id, entry.sold, entry.price),
            (60891, 7001, 0, 84449),
        )
        self.assertTrue(entry.changeable)

    def test_client_shortcut_needs_a_fresh_window(self):
        # A `c_skill` long before the window, or a non-bazaar window, is
        # not the bazaar opening.
        self.engine.feed(Packet(Kind.SEND, "c_skill", 100.0))
        self.engine.feed(Packet(Kind.RECV, "wopen 28 2 626", 100.2))
        self.engine.feed(Packet(Kind.RECV, "wopen 32 2 626", 110.0))
        self.assertFalse(self.engine.world.bazar.open_skill)

    def test_gate_list_adds_every_portal_on_the_map(self):
        # `gp` carries no map id: it arrives after `at` on map entry.
        self.engine.feed(
            packet(Kind.RECV, "at 14881134 3 45 12 2 0 2 1 -1 0 0")
        )
        for line in (
            "gp 10 208 6 -1 0 0",
            "gp 19 7 7 -1 1 0",
            "gp 45 12 103 -1 2 0",
            "gp 156 2 4 -1 3 0",
            "gp 178 189 2 -1 4 0",
        ):
            world = self.engine.feed(packet(Kind.RECV, line))
        self.assertEqual(
            world.gates,
            {
                (3, 10, 208): 6,
                (3, 19, 7): 7,
                (3, 45, 12): 103,
                (3, 156, 2): 4,
                (3, 178, 189): 2,
            },
        )


if __name__ == "__main__":
    unittest.main()
