"""Player DPS meter fed by the server's ``su`` packets.

The server broadcasts one ``su`` per hit. Live 2026-11-23 captures decode as::

    su <attackerType> <attackerId> <targetType> <targetId> <skill>
       <unknown> <unknown> <unknown> <unknown> <unknown> <unknown> <hpPercent>
       <damage> <unknown> <unknown> <targetHp> <targetHpMax>

Example (char 7001 hitting monster 58489)::

    su 1 7001 3 58489 934 60 25 3803 0 0 1 7 5852 5 0 10736 149353

``args[11]`` is the target's HP percentage after the hit (10736/149353 =
7 %), ``args[12]`` the damage dealt (5852 -> the prior HP was 16588), and
``args[15]``/``args[16]`` the target's current/max HP. The neighbouring
packet for the same monster at 10736 HP confirms the arithmetic. Older
2026-11-06 captures used a different tail, so a sample is only accepted when
all 17 fields are numeric.

Only player attackers (``attackerType == 1``) with positive damage are kept:
the meter is a DPS meter for players, not monsters or partners.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

#: Minimum number of numeric fields in the live ``su`` format.
SU_FIELDS = 17
#: Args indices (after the opcode).
ATTACKER_TYPE = 0
ATTACKER_ID = 1
TARGET_TYPE = 2
TARGET_ID = 3
SKILL = 4
HP_PERCENT = 11
DAMAGE = 12
TARGET_HP = 15
TARGET_HP_MAX = 16
#: ``attackerType`` of a player character.
PLAYER_TYPE = 1


@dataclass(frozen=True)
class DamageSample:
    """One damage event attributed to a player."""

    at: float
    attacker_id: int
    attacker_type: int
    target_id: int
    target_type: int
    skill: int
    damage: int
    hp_percent: int | None = None
    target_hp: int | None = None
    target_hp_max: int | None = None


@dataclass(frozen=True)
class DpsRow:
    """One attacker's rolling and session numbers."""

    attacker_id: int
    damage: int
    hits: int
    dps: float
    total: int
    total_hits: int
    last_at: float
    #: Fraction of the window's damage (0..1); 0 when nothing was dealt.
    share: float


def sample_from_packet(packet) -> DamageSample | None:
    """Decode one ``su`` packet into a player sample, or return ``None``.

    Non-``su`` packets, other attacker types (partners, monsters), malformed
    short packets and zero-damage skills (buffs) are ignored.
    """
    if packet.opcode != "su":
        return None
    try:
        numbers = packet.ints(SU_FIELDS)
    except (AttributeError, TypeError, ValueError):
        return None
    if numbers is None:
        return None
    attacker_type = numbers[ATTACKER_TYPE]
    damage = numbers[DAMAGE]
    if attacker_type != PLAYER_TYPE or damage <= 0:
        return None
    return DamageSample(
        at=packet.timestamp,
        attacker_id=numbers[ATTACKER_ID],
        attacker_type=attacker_type,
        target_id=numbers[TARGET_ID],
        target_type=numbers[TARGET_TYPE],
        skill=numbers[SKILL],
        damage=damage,
        hp_percent=numbers[HP_PERCENT],
        target_hp=numbers[TARGET_HP],
        target_hp_max=numbers[TARGET_HP_MAX],
    )


class DpsMeter:
    """Rolling and per-session damage totals for player attackers.

    ``snapshot(now, window)`` sums the samples in the last ``window``
    seconds; ``window=None`` means the whole session since :meth:`reset`.
    Session totals survive the sample ring, so a long session still has
    accurate total damage after old per-hit samples were dropped.
    """

    def __init__(
        self, max_samples: int = 20000, max_age: float = 1800.0
    ) -> None:
        self.max_samples = max_samples
        self.max_age = max_age
        self._samples: deque[DamageSample] = deque(maxlen=max_samples)
        #: attacker id -> [damage, hits, last_at]
        self._totals: dict[int, list[float]] = {}
        self._started_at: float | None = None

    def add(self, sample: DamageSample) -> None:
        if sample.damage <= 0:
            return
        self._samples.append(sample)
        totals = self._totals.setdefault(sample.attacker_id, [0, 0, 0.0])
        totals[0] += sample.damage
        totals[1] += 1
        totals[2] = max(totals[2], sample.at)
        if self._started_at is None or sample.at < self._started_at:
            self._started_at = sample.at

    def reset(self) -> None:
        self._samples.clear()
        self._totals.clear()
        self._started_at = None

    def prune(self, now: float, max_age: float | None = None) -> None:
        age = self.max_age if max_age is None else max_age
        cutoff = now - max(1.0, age)
        while self._samples and self._samples[0].at < cutoff:
            self._samples.popleft()

    def snapshot(
        self, now: float, window: float | None = 10.0
    ) -> list[DpsRow]:
        """Rows sorted by DPS (highest first)."""
        self.prune(now)
        if window is None:
            return self._session_rows(now)
        window = max(0.1, float(window))
        cutoff = now - window
        per: dict[int, list[float]] = {}
        for sample in self._samples:
            if sample.at < cutoff:
                continue
            entry = per.setdefault(sample.attacker_id, [0, 0, 0.0])
            entry[0] += sample.damage
            entry[1] += 1
            entry[2] = max(entry[2], sample.at)
        total = sum(entry[0] for entry in per.values())
        rows = []
        for attacker_id, (damage, hits, last_at) in per.items():
            totals = self._totals.get(attacker_id, [0, 0, 0.0])
            rows.append(
                DpsRow(
                    attacker_id=attacker_id,
                    damage=int(damage),
                    hits=int(hits),
                    dps=damage / window,
                    total=int(totals[0]),
                    total_hits=int(totals[1]),
                    last_at=last_at,
                    share=damage / total if total > 0 else 0.0,
                )
            )
        rows.sort(key=lambda row: (-row.dps, row.attacker_id))
        return rows

    def _session_rows(self, now: float) -> list[DpsRow]:
        span = max(1.0, now - (self._started_at or now))
        total = sum(values[0] for values in self._totals.values())
        rows = []
        for attacker_id, values in self._totals.items():
            damage = int(values[0])
            rows.append(
                DpsRow(
                    attacker_id=attacker_id,
                    damage=damage,
                    hits=int(values[1]),
                    dps=damage / span,
                    total=damage,
                    total_hits=int(values[1]),
                    last_at=values[2],
                    share=damage / total if total > 0 else 0.0,
                )
            )
        rows.sort(key=lambda row: (-row.dps, row.attacker_id))
        return rows
