"""Tests for the player DPS meter (decoding ``su`` damage)."""

from __future__ import annotations

import unittest

from ntpl.dpsmeter import DamageSample, DpsMeter, sample_from_packet
from ntpl.protocol import Kind, Packet


def _su(
    attacker: int,
    *,
    target: int = 500,
    skill: int = 999,
    damage: int = 100,
    hp_percent: int = 90,
    hp: int = 900,
    hp_max: int = 1000,
    attacker_type: int = 1,
) -> str:
    return (
        f"su {attacker_type} {attacker} 3 {target} {skill} 6 11 3801 "
        f"0 0 1 {hp_percent} {damage} 0 0 {hp} {hp_max}"
    )


class SampleTests(unittest.TestCase):
    def test_decodes_the_live_damage_fields(self) -> None:
        # Captured 2026-11-23: the prior packet for this monster showed
        # 10736 HP; 1683 + 9053 is the same target two lines earlier.
        packet = Packet(
            Kind.RECV,
            "su 1 7001 3 58489 934 60 25 3803 0 0 1 7 5852 5 0 10736 149353",
            100.0,
        )
        sample = sample_from_packet(packet)
        assert sample is not None
        self.assertEqual(sample.attacker_id, 7001)
        self.assertEqual(sample.target_id, 58489)
        self.assertEqual(sample.skill, 934)
        self.assertEqual(sample.damage, 5852)
        self.assertEqual(sample.hp_percent, 7)
        self.assertEqual(
            (sample.target_hp, sample.target_hp_max), (10736, 149353)
        )

    def test_ignores_partners_buffs_short_packets_and_other_opcodes(
        self,
    ) -> None:
        for packet in (
            Packet(Kind.RECV, _su(1102, attacker_type=2), 1.0),
            Packet(Kind.RECV, _su(1102, damage=0), 1.0),
            Packet(Kind.RECV, "su 1 1102 3 500 999", 1.0),
            Packet(Kind.RECV, "stat 1 2 3 4", 1.0),
        ):
            self.assertIsNone(sample_from_packet(packet))


class DpsMeterTests(unittest.TestCase):
    @staticmethod
    def _sample(at: float, attacker: int, damage: int) -> DamageSample:
        return DamageSample(
            at=at,
            attacker_id=attacker,
            attacker_type=1,
            target_id=9,
            target_type=3,
            skill=10,
            damage=damage,
        )

    def test_windowed_dps_share_and_totals(self) -> None:
        meter = DpsMeter()
        meter.add(self._sample(100.0, 1, 500))
        meter.add(self._sample(101.0, 1, 500))
        meter.add(self._sample(100.5, 2, 1000))
        rows = meter.snapshot(105.0, window=10.0)
        self.assertEqual([row.attacker_id for row in rows], [1, 2])
        self.assertAlmostEqual(rows[0].dps, 100.0)
        self.assertAlmostEqual(rows[1].dps, 100.0)
        self.assertAlmostEqual(rows[0].share, 0.5)
        self.assertEqual((rows[0].damage, rows[0].hits), (1000, 2))
        self.assertEqual((rows[0].total, rows[0].total_hits), (1000, 2))

    def test_old_hits_leave_the_window_but_not_the_session(self) -> None:
        meter = DpsMeter(max_age=60.0)
        meter.add(self._sample(100.0, 1, 1000))
        self.assertEqual(meter.snapshot(200.0, window=10.0), [])
        rows = meter.snapshot(200.0, window=None)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].damage, rows[0].total), (1000, 1000))
        self.assertAlmostEqual(rows[0].dps, 10.0)

    def test_zero_damage_and_reset(self) -> None:
        meter = DpsMeter()
        meter.add(self._sample(100.0, 1, 0))
        self.assertEqual(meter.snapshot(101.0, window=10.0), [])
        meter.add(self._sample(100.0, 1, 50))
        self.assertEqual(len(meter.snapshot(101.0, window=10.0)), 1)
        meter.reset()
        self.assertEqual(meter.snapshot(101.0, window=None), [])


if __name__ == "__main__":
    unittest.main()
