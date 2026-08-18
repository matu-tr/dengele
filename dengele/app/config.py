"""Reading and writing the user's settings."""

from __future__ import annotations

import contextlib
import enum
import json
import logging
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from dengele.engine import ConflictPolicy, PairConfig, default_excludes

from . import paths

log = logging.getLogger(__name__)


class WatchMode(enum.Enum):
    """How a pair decides when to sync on its own."""

    MANUAL = "manual"
    ON_CHANGE = "on-change"
    INTERVAL = "interval"


class Theme(enum.Enum):
    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"


@dataclass(slots=True)
class Pair:
    """One folder pair, as stored on disk and shown in the UI.

    The engine's own settings live in :attr:`engine` rather than being
    duplicated here, so there is exactly one definition of things like the
    exclude list.
    """

    engine: PairConfig
    name: str
    enabled: bool = True
    watch: WatchMode = WatchMode.ON_CHANGE
    interval_minutes: int = 30

    @property
    def id(self) -> str:
        return self.engine.id

    @classmethod
    def create(cls, name: str, path_a: Path, path_b: Path) -> Pair:
        return cls(
            engine=PairConfig(
                id=str(uuid.uuid4()),
                path_a=Path(path_a),
                path_b=Path(path_b),
                excludes=default_excludes(),
            ),
            name=name,
        )

    def to_json(self) -> dict[str, Any]:
        engine = self.engine
        return {
            "id": engine.id,
            "path_a": str(engine.path_a),
            "path_b": str(engine.path_b),
            "excludes": list(engine.excludes),
            "conflict_policy": engine.conflict_policy.value,
            "require_marker": engine.require_marker,
            "delete_threshold_pct": engine.delete_threshold_pct,
            "delete_threshold_min": engine.delete_threshold_min,
            "skip_cloud_placeholders": engine.skip_cloud_placeholders,
            "recycle_retention_days": engine.recycle_retention_days,
            "name": self.name,
            "enabled": self.enabled,
            "watch": self.watch.value,
            "interval_minutes": self.interval_minutes,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Pair:
        engine = PairConfig(
            id=str(data.get("id") or uuid.uuid4()),
            path_a=Path(data["path_a"]),
            path_b=Path(data["path_b"]),
            excludes=list(data.get("excludes") or default_excludes()),
            conflict_policy=_enum(
                ConflictPolicy, data.get("conflict_policy"), ConflictPolicy.NEWEST_WINS
            ),
            require_marker=bool(data.get("require_marker", False)),
            delete_threshold_pct=float(data.get("delete_threshold_pct", 0.20)),
            delete_threshold_min=int(data.get("delete_threshold_min", 50)),
            skip_cloud_placeholders=bool(data.get("skip_cloud_placeholders", True)),
            recycle_retention_days=int(data.get("recycle_retention_days", 30)),
        )
        return cls(
            engine=engine,
            name=str(data.get("name") or engine.path_a.name),
            enabled=bool(data.get("enabled", True)),
            watch=_enum(WatchMode, data.get("watch"), WatchMode.ON_CHANGE),
            interval_minutes=int(data.get("interval_minutes", 30)),
        )

    def copy(self) -> Pair:
        return replace(self, engine=replace(self.engine, excludes=list(self.engine.excludes)))


@dataclass(slots=True)
class Config:
    pairs: list[Pair] = field(default_factory=list)
    theme: Theme = Theme.SYSTEM
    autostart: bool = False
    notifications: bool = True
    #: Start hidden in the tray instead of showing the window.
    start_minimized: bool = False
    #: Close the window to the tray rather than quitting.
    close_to_tray: bool = True

    def pair(self, pair_id: str) -> Pair | None:
        return next((p for p in self.pairs if p.id == pair_id), None)

    def to_json(self) -> dict[str, Any]:
        return {
            "pairs": [p.to_json() for p in self.pairs],
            "theme": self.theme.value,
            "autostart": self.autostart,
            "notifications": self.notifications,
            "start_minimized": self.start_minimized,
            "close_to_tray": self.close_to_tray,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Config:
        pairs = []
        for raw in data.get("pairs") or []:
            try:
                pairs.append(Pair.from_json(raw))
            except (KeyError, ValueError, TypeError) as err:
                # One malformed pair must not cost the user the rest of them.
                log.error("skipping unreadable pair entry: %s", err)
        return cls(
            pairs=pairs,
            theme=_enum(Theme, data.get("theme"), Theme.SYSTEM),
            autostart=bool(data.get("autostart", False)),
            notifications=bool(data.get("notifications", True)),
            start_minimized=bool(data.get("start_minimized", False)),
            close_to_tray=bool(data.get("close_to_tray", True)),
        )


def load(path: Path | None = None) -> Config:
    path = path or paths.config_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Config()
    except (OSError, json.JSONDecodeError) as err:
        # A corrupt config must not cost the user their pair list silently, and
        # must not start syncing with defaults either — keep it and say so.
        log.error("config is unreadable (%s); keeping a backup and starting fresh", err)
        with contextlib.suppress(OSError):
            path.replace(path.with_suffix(".json.corrupt"))
        return Config()

    if not isinstance(data, dict):
        log.error("config is not an object; starting fresh")
        return Config()
    return Config.from_json(data)


def save(config: Config, path: Path | None = None) -> None:
    path = path or paths.config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write-then-rename so an interrupted save cannot truncate the config.
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(config.to_json(), indent=2), encoding="utf-8")
    temp.replace(path)


def _enum(cls, value, fallback):
    try:
        return cls(value)
    except ValueError:
        return fallback
