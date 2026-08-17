"""Data types shared by every stage of the sync engine.

Nothing here imports Qt: the engine is deliberately usable — and testable —
without a GUI.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path

# Marker file a root may be required to carry before the engine will touch it.
#
# This guards the failure mode that makes any deleting mirror dangerous: an
# external disk is unmounted, its mount point is an empty directory, and every
# file looks deleted.
ROOT_MARKER = ".mt-sync-root"

# Directory, relative to each root, that deleted items are moved into.
RECYCLE_DIR = ".mt-sync-trash"

# Suffix of the temporary file a copy is written to before being renamed
# into place.
TEMP_SUFFIX = ".mt-sync-tmp"

# How far two modification times may drift and still count as "the same".
#
# FAT/exFAT stores timestamps with two-second granularity, so an exact
# comparison would report every file on an external disk as changed on every
# run — which, in a mirror that deletes, is how data gets lost.
MTIME_TOLERANCE_MS = 2_000


class Side(enum.Enum):
    A = "a"
    B = "b"

    @property
    def other(self) -> Side:
        return Side.B if self is Side.A else Side.A

    @property
    def label(self) -> str:
        return self.value.upper()


class EntryKind(enum.Enum):
    FILE = "file"
    DIR = "dir"
    SYMLINK = "symlink"


class ConflictPolicy(enum.Enum):
    """What to do when both sides changed a path since the last sync."""

    NEWEST_WINS = "newest-wins"
    A_WINS = "a-wins"
    B_WINS = "b-wins"
    #: Leave the path alone and report it for the user to resolve.
    ASK = "ask"


class ConflictKind(enum.Enum):
    BOTH_MODIFIED = "both_modified"
    MODIFIED_AND_DELETED = "modified_and_deleted"
    TYPE_MISMATCH = "type_mismatch"


@dataclass(frozen=True, slots=True)
class Entry:
    """One path as it exists on disk right now."""

    kind: EntryKind
    size: int
    mtime_ms: int
    #: Literal target of a symlink, preserved rather than followed.
    link_target: str | None = None


@dataclass(frozen=True, slots=True)
class SnapRecord:
    """What both sides agreed on at the end of the last successful sync."""

    kind: EntryKind
    size: int
    mtime_ms: int
    link_target: str | None = None

    @classmethod
    def from_entry(cls, entry: Entry) -> SnapRecord:
        return cls(
            kind=entry.kind,
            size=entry.size,
            mtime_ms=entry.mtime_ms,
            link_target=entry.link_target,
        )


class OpKind(enum.Enum):
    MKDIR = "mkdir"
    COPY = "copy"
    DELETE = "delete"
    PRESERVE_LOSER = "preserve_loser"
    #: Contents already agree; only the snapshot needs updating.
    RECORD = "record"
    #: Path is gone from both sides; drop its snapshot row.
    DROP_RECORD = "drop_record"


@dataclass(frozen=True, slots=True)
class Op:
    """A single filesystem change the planner decided on."""

    kind: OpKind
    rel: str
    #: For MKDIR/DELETE/PRESERVE_LOSER: the side acted on.
    #: For COPY: the side copied *from*.
    side: Side | None = None
    #: For PRESERVE_LOSER: where the losing version is moved to.
    renamed_to: str | None = None
    #: True for a deletion that exists only so a replacement can be written in
    #: its place (a file becoming a directory, or the reverse). Those run
    #: *before* copies; ordinary deletions run after, so a rename-shaped change
    #: always writes the new copy before dropping the old one.
    to_make_way: bool = False
    #: Bytes this operation transfers, for progress reporting.
    size: int = 0

    @property
    def phase(self) -> int:
        """Sort bucket. Order matters: see :attr:`to_make_way`."""
        match self.kind:
            case OpKind.DROP_RECORD:
                return 0
            case OpKind.DELETE:
                return 1 if self.to_make_way else 5
            case OpKind.MKDIR:
                return 2
            case OpKind.PRESERVE_LOSER:
                return 3
            case OpKind.COPY:
                return 4
            case OpKind.RECORD:
                return 6
        raise AssertionError(f"unhandled op kind: {self.kind}")


@dataclass(frozen=True, slots=True)
class Conflict:
    """A disagreement the planner could not resolve on its own."""

    rel: str
    kind: ConflictKind
    a_size: int | None = None
    b_size: int | None = None
    a_mtime_ms: int | None = None
    b_mtime_ms: int | None = None
    #: Side the configured policy picked, or None under ConflictPolicy.ASK.
    resolved_to: Side | None = None


@dataclass(slots=True)
class PlanStats:
    copy_a_to_b: int = 0
    copy_b_to_a: int = 0
    delete_a: int = 0
    delete_b: int = 0
    mkdir: int = 0
    bytes_to_copy: int = 0

    @property
    def copies(self) -> int:
        return self.copy_a_to_b + self.copy_b_to_a

    @property
    def deletes(self) -> int:
        return self.delete_a + self.delete_b


@dataclass(slots=True)
class Plan:
    """Everything a sync would change, computed without touching anything."""

    pair_id: str
    ops: list[Op] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    stats: PlanStats = field(default_factory=PlanStats)
    #: Set when the delete guard tripped; apply() refuses unless forced.
    blocked: str | None = None
    #: Cloud placeholders skipped during the scan, per side.
    placeholders_a: list[str] = field(default_factory=list)
    placeholders_b: list[str] = field(default_factory=list)
    #: Unreadable paths, as (relative path, reason), per side.
    errors_a: list[tuple[str, str]] = field(default_factory=list)
    errors_b: list[tuple[str, str]] = field(default_factory=list)

    @property
    def effective_ops(self) -> list[Op]:
        """Operations that actually touch the filesystem."""
        bookkeeping = (OpKind.RECORD, OpKind.DROP_RECORD)
        return [op for op in self.ops if op.kind not in bookkeeping]

    @property
    def is_noop(self) -> bool:
        return not self.effective_ops


def default_excludes() -> list[str]:
    """Noise that should never be synchronized, offered as the default list."""
    return [
        ".DS_Store",
        "Thumbs.db",
        "desktop.ini",
        ".Spotlight-V100/",
        ".fseventsd/",
        ".TemporaryItems/",
        ".Trashes/",
        "$RECYCLE.BIN/",
        "System Volume Information/",
        "node_modules/",
        "venv/",
        "__pycache__/",
    ]


@dataclass(slots=True)
class PairConfig:
    """Everything the engine needs to know about one synchronized pair."""

    id: str
    path_a: Path
    path_b: Path
    #: gitignore-style patterns, matched against the relative path.
    excludes: list[str] = field(default_factory=default_excludes)
    conflict_policy: ConflictPolicy = ConflictPolicy.NEWEST_WINS
    #: Require ROOT_MARKER in both roots before syncing.
    require_marker: bool = False
    #: Block a plan that would delete more than this share of a side's entries.
    delete_threshold_pct: float = 0.20
    #: ...but never block a plan deleting fewer than this many entries.
    delete_threshold_min: int = 50
    #: Skip iCloud placeholders for files that aren't downloaded locally.
    skip_cloud_placeholders: bool = True
    #: Days to keep recycled items before pruning. 0 keeps them forever.
    recycle_retention_days: int = 30

    def root(self, side: Side) -> Path:
        return self.path_a if side is Side.A else self.path_b
