"""Pet Trainer settings and packets: taming, Miniland training and extraction.

The tab automates the Pet Trainer specialist card end to end:

1. **Tame** the chosen monster (Chicken = 333) with Whip + Tame until the
   server says the pet slots are full (`msgi 0 1887`).
2. **Miniland**: enter the Miniland (a portal on map 1 at ~(49,133) on the
   live server), cast Summon Weak Sparring Monster (slot 2, skill 1788) and
   send the tamed pets at the spawned monster (vnum 1497) with `suctl`.
3. **Extract**: after the training time, cast Extract Essence (slot 3, skill
   1789) on each tamed pet; the server releases it and gives the essence
   (`msgi 0 2627`), which frees the slot, and the loop starts taming again.

Live protocol (2026-11-27, client PID 836) is captured in [taming.md].
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

#: Skills of the Pet Trainer card and the live packet constants.
TAME_SKILL_VNUM = 1787
ADVANCED_TAME_VNUM = 1791
SUMMON_SKILL_VNUM = 1788
EXTRACT_SKILL_VNUM = 1789
SPARRING_VNUM = 1497

TAME_SEARCH_CELLS = 15
TAME_RANGE_CELLS = 2
TAME_COOLDOWN_S = 15.0
TAME_COOLDOWN_PAD_S = 1.0
TAME_CAST_START_TIMEOUT_S = 1.5
TAME_RESULT_TIMEOUT_S = 4.0
TAME_MAX_TRIES = 3
SUMMON_COOLDOWN_S = 30.0
SUMMON_PAD_S = 1.0
EXTRACT_INTERVAL_S = 2.5
EXTRACT_TIMEOUT_S = 4.0
TRAIN_ROUNDS = 3

#: Game18N keys of the answers (live 2026-11-27).
MSG_TAME_SUCCESS = 2614      # Taming successful.
MSG_PET_SLOTS_FULL = 1887    # maximum number of pet slots
MSG_TAME_FAILED = 2631       # Taming failed.
MSG_CANNOT_TAME = 2623       # Monsters cannot be tamed with this skill.
MSG_TRAIN_UP = 2618          # the pet's star rating / training went up
MSG_EXTRACT_OK = 2627        # essence received and the animal released
MSG_EXTRACT_NOT_MAX = 2625   # essence only from max training level pets
MSG_EXTRACT_NOT_MAX2 = 2639  # only max training level pets can be reset

#: The live Miniland entrance: a portal on map 1 at (49,133).
PORTAL_MAP = 1
PORTAL_X = 49
PORTAL_Y = 133


def build_suctl_packet(
    map_id: int, pet_ids: list[int], target_id: int, target_type: int = 3
) -> str:
    """``suctl <map> <count> <petId> <targetType> <targetId> ...``.

    The client sends one pair per attacking pet; the server accepts several
    pairs in one packet (live 2026-11-27: up to 10).
    """
    pairs = " ".join(
        f"{int(pet_id)} {int(target_type)} {int(target_id)}"
        for pet_id in pet_ids
    )
    return f"suctl {int(map_id)} {len(pet_ids)} {pairs}"


@dataclass
class TamerConfig:
    # Taming.
    tame_vnum: int = 333
    tame_slot: int = 1
    tame_hp_pct: float = 25.0
    # End to end.
    e2e: bool = False
    miniland_map: int = 20001
    portal_map: int = PORTAL_MAP
    portal_x: int = PORTAL_X
    portal_y: int = PORTAL_Y
    summon_slot: int = 2
    sparring_vnum: int = SPARRING_VNUM
    extract_slot: int = 3
    train_seconds: int = 480
    extract: bool = True

    def to_dict(self) -> dict:
        return {
            "tame_vnum": self.tame_vnum,
            "tame_slot": self.tame_slot,
            "tame_hp_pct": self.tame_hp_pct,
            "e2e": self.e2e,
            "miniland_map": self.miniland_map,
            "portal_map": self.portal_map,
            "portal_x": self.portal_x,
            "portal_y": self.portal_y,
            "summon_slot": self.summon_slot,
            "sparring_vnum": self.sparring_vnum,
            "extract_slot": self.extract_slot,
            "train_seconds": self.train_seconds,
            "extract": self.extract,
        }

    @classmethod
    def from_dict(cls, data: object) -> "TamerConfig":
        if not isinstance(data, dict):
            return cls()
        def _int(key: str, default: int, low: int, high: int) -> int:
            try:
                value = int(data.get(key, default))
            except (TypeError, ValueError):
                return default
            return max(low, min(high, value))
        def _float(key: str, default: float, low: float, high: float) -> float:
            try:
                value = float(data.get(key, default))
            except (TypeError, ValueError):
                return default
            return max(low, min(high, value))
        def _bool(key: str, default: bool) -> bool:
            value = data.get(key, default)
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return default
        return cls(
            tame_vnum=_int("tame_vnum", 333, 1, 10**9),
            tame_slot=_int("tame_slot", 1, 1, 9),
            tame_hp_pct=_float("tame_hp_pct", 25.0, 5.0, 50.0),
            e2e=_bool("e2e", False),
            miniland_map=_int("miniland_map", 20001, 1, 10**9),
            portal_map=_int("portal_map", PORTAL_MAP, 1, 10**9),
            portal_x=_int("portal_x", PORTAL_X, 0, 32767),
            portal_y=_int("portal_y", PORTAL_Y, 0, 32767),
            summon_slot=_int("summon_slot", 2, 1, 9),
            sparring_vnum=_int("sparring_vnum", SPARRING_VNUM, 1, 10**9),
            extract_slot=_int("extract_slot", 3, 1, 9),
            train_seconds=_int("train_seconds", 480, 30, 7200),
            extract=_bool("extract", True),
        )


def default_config_path(winpid: int | None = None) -> Path:
    override = os.environ.get("NTPL_TAMER_CONFIG")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    name = f"tamer-{winpid}.json" if winpid is not None else "tamer.json"
    return Path(base) / "ntpl" / name


def load_config(path: str | Path) -> TamerConfig:
    try:
        with open(path, encoding="utf-8") as handle:
            return TamerConfig.from_dict(json.load(handle))
    except (OSError, ValueError):
        return TamerConfig()


def save_config(path: str | Path, config: TamerConfig) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=target.name + ".", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(config.to_dict(), handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return target
