from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from dengele.engine import ConflictPolicy, PairConfig, Side, Snapshot, sync_pair


class Pair:
    """Two temporary folders plus the snapshot that ties them together."""

    def __init__(self, tmp_path: Path, policy: ConflictPolicy = ConflictPolicy.NEWEST_WINS):
        self.root_a = tmp_path / "a"
        self.root_b = tmp_path / "b"
        self.root_a.mkdir()
        self.root_b.mkdir()

        self.cfg = PairConfig(
            id="test-pair",
            path_a=self.root_a,
            path_b=self.root_b,
            excludes=[],
            conflict_policy=policy,
            # Tests deliberately delete a large share of very small trees.
            delete_threshold_min=10**9,
        )
        self.snapshot = Snapshot(":memory:")

    def root(self, side: Side) -> Path:
        return self.root_a if side is Side.A else self.root_b

    def write(self, side: Side, rel: str, text: str) -> Path:
        path = self.root(side) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def read(self, side: Side, rel: str) -> str:
        return (self.root(side) / rel).read_text()

    def exists(self, side: Side, rel: str) -> bool:
        return (self.root(side) / rel).exists()

    def touch(self, side: Side, rel: str, offset_seconds: float) -> None:
        """Set an explicit mtime so conflict ordering is deterministic."""
        when = time.time() + offset_seconds
        os.utime(self.root(side) / rel, (when, when))

    def sync(self, force: bool = False):
        plan, outcome = sync_pair(self.cfg, self.snapshot, force=force)
        assert not outcome.failures, f"sync reported failures: {outcome.failures}"
        return plan, outcome

    def files_under(self, path: Path) -> list[Path]:
        return sorted(p for p in path.rglob("*") if p.is_file()) if path.exists() else []


@pytest.fixture
def pair(tmp_path: Path) -> Pair:
    return Pair(tmp_path)


@pytest.fixture
def make_pair(tmp_path: Path):
    def factory(policy: ConflictPolicy = ConflictPolicy.NEWEST_WINS) -> Pair:
        directory = tmp_path / policy.value
        directory.mkdir()
        return Pair(directory, policy)

    return factory


@pytest.fixture
def pair_in():
    """Build a pair rooted anywhere, optionally sharing a snapshot database.

    Exposed as a fixture rather than by importing `Pair` from this module:
    `tests` is not an installed package, so `from tests.conftest import ...`
    only resolves when pytest happens to have put the repository root on
    sys.path — which it does not do the same way on every platform.
    """

    def factory(directory: Path, snapshot: Snapshot | None = None) -> Pair:
        directory.mkdir(parents=True, exist_ok=True)
        built = Pair(directory)
        if snapshot is not None:
            built.snapshot = snapshot
        return built

    return factory
