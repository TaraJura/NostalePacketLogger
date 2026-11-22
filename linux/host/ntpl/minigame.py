"""Sheep Farm helpers: key spam settings and player-only target picking.

The Sheep Farm raid disguises players among sheep NPCs. The Minigame tab
therefore has to be deliberately blind to entities that are not players:
only ``Entity.kind == 1`` is a character, while kind 2 is a pet/partner/NPC
and kind 3 is a monster or map NPC. This module keeps that rule (and the
persisted settings) pure so it can be tested without GTK or a game client.

The host sends each key press as the DLL command ``KEY|<key>``; see
``docs/minigame.md``.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .engine import Entity

DEFAULT_KEY = "y"
DEFAULT_INTERVAL_MS = 100
MIN_INTERVAL_MS = 40
MAX_INTERVAL_MS = 5000
DEFAULT_RADIUS = 15
MIN_RADIUS = 1
MAX_RADIUS = 50

#: Key names the DLL understands besides a single letter or digit.
_KEY_ALIASES = {
    "return": "enter",
    "escape": "esc",
    "control": "ctrl",
    "spacebar": "space",
    "pgup": "pageup",
    "pgdn": "pagedown",
    "del": "delete",
    "ins": "insert",
}
_NAMED_KEYS = {
    "space",
    "enter",
    "tab",
    "esc",
    "backspace",
    "shift",
    "ctrl",
    "alt",
    "up",
    "down",
    "left",
    "right",
    "home",
    "end",
    "insert",
    "delete",
    "pageup",
    "pagedown",
} | {f"f{number}" for number in range(1, 13)}


def normalize_key(value: object) -> str | None:
    """Canonical key name, or None when the DLL would not understand it.

    One ASCII letter or digit, or one of the named keys above. The game
    receives the key press, not a chat character, so punctuation is not a
    useful binding and is rejected.
    """
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    text = _KEY_ALIASES.get(text, text)
    if len(text) == 1 and text.isascii() and text.isalnum():
        return text
    if text in _NAMED_KEYS:
        return text
    return None


def clamp_interval(value: object) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_MS
    return max(MIN_INTERVAL_MS, min(MAX_INTERVAL_MS, number))


def clamp_radius(value: object) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_RADIUS
    return max(MIN_RADIUS, min(MAX_RADIUS, number))


@dataclass
class MinigameConfig:
    """The per-client settings of the Minigame tab.

    ``focus_players`` is on by default: the raid's whole point is finding
    the players disguised among the sheep. The key and timing are editable
    because the raid's useful key may differ between events.
    """

    key: str = DEFAULT_KEY
    interval_ms: int = DEFAULT_INTERVAL_MS
    focus_players: bool = True
    radius: int = DEFAULT_RADIUS

    def normalized(self) -> "MinigameConfig":
        return MinigameConfig(
            key=normalize_key(self.key) or DEFAULT_KEY,
            interval_ms=clamp_interval(self.interval_ms),
            focus_players=bool(self.focus_players),
            radius=clamp_radius(self.radius),
        )

    def to_dict(self) -> dict[str, object]:
        config = self.normalized()
        return {
            "key": config.key,
            "interval_ms": config.interval_ms,
            "focus_players": config.focus_players,
            "radius": config.radius,
        }

    @classmethod
    def from_dict(cls, data: object) -> "MinigameConfig":
        if not isinstance(data, dict):
            return cls()
        focus = data.get("focus_players", True)
        return cls(
            key=normalize_key(data.get("key")) or DEFAULT_KEY,
            interval_ms=clamp_interval(data.get("interval_ms")),
            # A hand-edited "false"/0 is not a bool; use the safe default.
            focus_players=focus if isinstance(focus, bool) else True,
            radius=clamp_radius(data.get("radius")),
        )


def load_config(path: str | Path) -> MinigameConfig:
    """Read a per-client settings file; a broken file means defaults."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, TypeError, ValueError):
        return MinigameConfig()
    return MinigameConfig.from_dict(data)


def save_config(path: str | Path, config: MinigameConfig) -> Path:
    """Write the settings atomically and return the target path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(config.to_dict(), indent=2) + "\n"
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=target.parent,
        prefix=target.name + ".",
        suffix=".tmp",
        delete=False,
    )
    try:
        with handle:
            handle.write(payload)
        os.replace(handle.name, target)
    except OSError:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise
    return target


def _name(entity: "Entity") -> str:
    return (entity.name or "").strip()


def nearby_players(
    entities: Iterable["Entity"],
    own_id: int | None,
    position: tuple[int, int] | None,
    radius: int,
    exclude: set[int] | None = None,
) -> list[tuple["Entity", int]]:
    """Players within ``radius``, nearest first, with Chebyshev distance.

    Pets, partners and NPCs (kind 2), monsters and map NPCs (kind 3) and the
    local character are never included. Players whose name is unknown are
    still valid targets; the caller displays their char id.
    """
    if position is None:
        return []
    px, py = position
    rows: list[tuple["Entity", int]] = []
    for entity in entities:
        if entity.kind != 1:
            continue
        if own_id is not None and entity.entity_id == own_id:
            continue
        if exclude and entity.entity_id in exclude:
            continue
        distance = max(abs(entity.x - px), abs(entity.y - py))
        if distance <= radius:
            rows.append((entity, distance))
    rows.sort(
        key=lambda row: (
            row[1],
            _name(row[0]).casefold(),
            row[0].entity_id,
        )
    )
    return rows


def nearest_player(
    entities: Iterable["Entity"],
    own_id: int | None,
    position: tuple[int, int] | None,
    radius: int,
    exclude: set[int] | None = None,
) -> tuple["Entity", int] | None:
    """The closest player as ``(entity, distance)``, or None."""
    rows = nearby_players(entities, own_id, position, radius, exclude)
    return rows[0] if rows else None
