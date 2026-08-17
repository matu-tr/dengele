"""Deciding what a sync should do, without doing any of it.

For every path three things are compared: side A, side B, and the snapshot of
what they last agreed on. That third input is what lets a deletion be told
apart from a creation — which a plain two-pass mirror cannot do, and which is
why the predecessor destroyed files that existed on only one side.
"""

from __future__ import annotations

import time
from pathlib import Path

from .hashing import files_equal
from .models import (
    MTIME_TOLERANCE_MS,
    Conflict,
    ConflictKind,
    ConflictPolicy,
    Entry,
    EntryKind,
    Op,
    OpKind,
    PairConfig,
    Plan,
    PlanStats,
    Side,
    SnapRecord,
)
from .scan import Scan


def plan(cfg: PairConfig, a: Scan, b: Scan, snapshot: dict[str, SnapRecord]) -> Plan:
    """Compute the full set of changes needed to reconcile a pair."""
    result = Plan(pair_id=cfg.id, stats=PlanStats())
    ops: list[Op] = []
    pruned: list[str] = []
    now_ms = int(time.time() * 1000)

    # Sorted so a directory is always considered before its contents; when a
    # directory is removed or replaced wholesale, its subtree is pruned rather
    # than planned separately.
    for rel in sorted(set(a.entries) | set(b.entries) | set(snapshot)):
        if any(_is_under(rel, prefix) for prefix in pruned):
            continue

        entry_a = a.entries.get(rel)
        entry_b = b.entries.get(rel)

        if entry_a is None and entry_b is None:
            ops.append(Op(kind=OpKind.DROP_RECORD, rel=rel))
        elif entry_b is None:
            _decide_one_sided(rel, entry_a, Side.A, snapshot, a, ops, result, pruned)
        elif entry_a is None:
            _decide_one_sided(rel, entry_b, Side.B, snapshot, b, ops, result, pruned)
        else:
            _decide_both_sides(
                cfg, rel, entry_a, entry_b, snapshot.get(rel), ops, result, pruned, now_ms
            )

    # Deletions run deepest-first so directories empty before removal;
    # everything else shallowest-first so parents exist before children.
    ops.sort(key=lambda op: (op.phase, _depth_key(op)))
    result.ops = ops
    result.blocked = _delete_guard(cfg, result.stats, a, b)
    result.placeholders_a = list(a.placeholders)
    result.placeholders_b = list(b.placeholders)
    result.errors_a = list(a.errors)
    result.errors_b = list(b.errors)
    return result


def _decide_one_sided(
    rel: str,
    entry: Entry,
    side: Side,
    snapshot: dict[str, SnapRecord],
    scan: Scan,
    ops: list[Op],
    result: Plan,
    pruned: list[str],
) -> None:
    record = snapshot.get(rel)

    if record is None:
        # Never synced before, so this is new here rather than deleted there.
        _propagate(rel, entry, side, ops, result.stats)
        return

    if entry.kind is EntryKind.DIR:
        # A directory has no content of its own, so "changed" means something
        # inside it changed. This has to be answered before the deletion is
        # planned, because deleting the directory prunes its children from the
        # walk — they would never get a say otherwise.
        changed = _subtree_changed(rel, scan, snapshot)
    else:
        changed = entry_changed(entry, record)

    if not changed:
        # Unchanged here and gone there: a genuine deletion to propagate.
        ops.append(Op(kind=OpKind.DELETE, rel=rel, side=side))
        _count_delete(result.stats, side)
        if entry.kind is EntryKind.DIR:
            pruned.append(rel)
        return

    # Edited here, deleted there. Deleting would throw away the only copy of
    # work that exists, so the edit wins and the user is told.
    result.conflicts.append(
        Conflict(
            rel=rel,
            kind=ConflictKind.MODIFIED_AND_DELETED,
            a_size=entry.size if side is Side.A else None,
            b_size=entry.size if side is Side.B else None,
            a_mtime_ms=entry.mtime_ms if side is Side.A else None,
            b_mtime_ms=entry.mtime_ms if side is Side.B else None,
            resolved_to=side,
        )
    )
    _propagate(rel, entry, side, ops, result.stats)


def _decide_both_sides(
    cfg: PairConfig,
    rel: str,
    entry_a: Entry,
    entry_b: Entry,
    record: SnapRecord | None,
    ops: list[Op],
    result: Plan,
    pruned: list[str],
    now_ms: int,
) -> None:
    # Two directories need no reconciliation of their own; their children carry
    # the content and are planned separately.
    if entry_a.kind is EntryKind.DIR and entry_b.kind is EntryKind.DIR:
        if record is None or record.kind is not EntryKind.DIR:
            ops.append(Op(kind=OpKind.RECORD, rel=rel))
        return

    changed_a = entry_changed(entry_a, record)
    changed_b = entry_changed(entry_b, record)

    if not changed_a and not changed_b:
        if record is None:
            ops.append(Op(kind=OpKind.RECORD, rel=rel))
        return

    if changed_a and not changed_b:
        _overwrite(rel, entry_a, entry_b, Side.A, ops, result.stats, pruned)
        return

    if changed_b and not changed_a:
        _overwrite(rel, entry_b, entry_a, Side.B, ops, result.stats, pruned)
        return

    # Both moved. If they happen to have landed on identical content there is
    # nothing to copy — just re-baseline the snapshot.
    type_mismatch = entry_a.kind is not entry_b.kind
    if not type_mismatch and _contents_identical(cfg, rel, entry_a, entry_b):
        ops.append(Op(kind=OpKind.RECORD, rel=rel))
        return

    winner = _resolve(cfg.conflict_policy, entry_a, entry_b)
    result.conflicts.append(
        Conflict(
            rel=rel,
            kind=ConflictKind.TYPE_MISMATCH if type_mismatch else ConflictKind.BOTH_MODIFIED,
            a_size=entry_a.size,
            b_size=entry_b.size,
            a_mtime_ms=entry_a.mtime_ms,
            b_mtime_ms=entry_b.mtime_ms,
            resolved_to=winner,
        )
    )

    if winner is None:
        # Under ASK nothing is touched until the user decides.
        return

    loser = winner.other
    win_entry, lose_entry = (entry_a, entry_b) if winner is Side.A else (entry_b, entry_a)
    ops.append(
        Op(
            kind=OpKind.PRESERVE_LOSER,
            rel=rel,
            side=loser,
            renamed_to=conflict_name(rel, loser, now_ms),
        )
    )
    _overwrite(rel, win_entry, lose_entry, winner, ops, result.stats, pruned)


def _propagate(rel: str, entry: Entry, from_side: Side, ops: list[Op], stats: PlanStats) -> None:
    """Queue the work to make the other side match ``entry``."""
    if entry.kind is EntryKind.DIR:
        ops.append(Op(kind=OpKind.MKDIR, rel=rel, side=from_side.other))
        stats.mkdir += 1
        return

    ops.append(Op(kind=OpKind.COPY, rel=rel, side=from_side, size=entry.size))
    if from_side is Side.A:
        stats.copy_a_to_b += 1
    else:
        stats.copy_b_to_a += 1
    stats.bytes_to_copy += entry.size


def _overwrite(
    rel: str,
    win_entry: Entry,
    lose_entry: Entry,
    from_side: Side,
    ops: list[Op],
    stats: PlanStats,
    pruned: list[str],
) -> None:
    """Like :func:`_propagate`, but the destination already holds something."""
    # A directory replaced by a file (or the reverse) cannot be written over;
    # the old entry has to be cleared out of the way first.
    if lose_entry.kind is not win_entry.kind:
        ops.append(Op(kind=OpKind.DELETE, rel=rel, side=from_side.other, to_make_way=True))
        _count_delete(stats, from_side.other)
        # Only a discarded *directory* takes descendants with it; a discarded
        # file has none, and the winning directory's children still need planning.
        if lose_entry.kind is EntryKind.DIR:
            pruned.append(rel)

    _propagate(rel, win_entry, from_side, ops, stats)


def entry_changed(entry: Entry, record: SnapRecord | None) -> bool:
    """Whether an entry differs from what the snapshot recorded."""
    if record is None:
        return True
    if entry.kind is not record.kind:
        return True
    if entry.kind is EntryKind.DIR:
        return False
    if entry.kind is EntryKind.SYMLINK:
        return entry.link_target != record.link_target
    return entry.size != record.size or abs(entry.mtime_ms - record.mtime_ms) > MTIME_TOLERANCE_MS


def _subtree_changed(rel: str, scan: Scan, snapshot: dict[str, SnapRecord]) -> bool:
    """Whether anything inside a directory changed relative to the snapshot.

    Used before propagating a directory deletion: if the surviving copy
    contains edited or brand-new work, the deletion is downgraded to a conflict
    rather than erasing it.
    """
    prefix = f"{rel}/"
    return any(
        entry.kind is not EntryKind.DIR and entry_changed(entry, snapshot.get(key))
        for key, entry in scan.entries.items()
        if key.startswith(prefix)
    )


def _contents_identical(cfg: PairConfig, rel: str, entry_a: Entry, entry_b: Entry) -> bool:
    if entry_a.kind is not entry_b.kind:
        return False
    if entry_a.kind is EntryKind.SYMLINK:
        return entry_a.link_target == entry_b.link_target
    if entry_a.kind is EntryKind.DIR:
        return True
    if entry_a.size != entry_b.size:
        return False
    try:
        return files_equal(cfg.path_a / rel, cfg.path_b / rel)
    except OSError:
        # If either side cannot be read, assume they differ so the conflict is
        # surfaced rather than silently swallowed.
        return False


def _resolve(policy: ConflictPolicy, entry_a: Entry, entry_b: Entry) -> Side | None:
    match policy:
        case ConflictPolicy.A_WINS:
            return Side.A
        case ConflictPolicy.B_WINS:
            return Side.B
        case ConflictPolicy.ASK:
            return None
        case ConflictPolicy.NEWEST_WINS:
            return Side.B if entry_b.mtime_ms > entry_a.mtime_ms else Side.A
    raise AssertionError(f"unhandled policy: {policy}")


def conflict_name(rel: str, side: Side, now_ms: int) -> str:
    """``notes/todo.txt`` → ``notes/todo (conflict A 2026-08-17 14-30-05).txt``"""
    directory, _, name = rel.rpartition("/")
    stem, dot, extension = name.rpartition(".")
    if not dot or not stem:
        # A leading dot is part of the name, not an extension separator.
        stem, extension = name, ""

    stamp = format_timestamp(now_ms)
    renamed = f"{stem} (conflict {side.label} {stamp})"
    if extension:
        renamed = f"{renamed}.{extension}"
    return f"{directory}/{renamed}" if directory else renamed


def format_timestamp(ms: int) -> str:
    """``2026-08-17 14-30-05`` — dashes so it is a legal Windows filename."""
    return time.strftime("%Y-%m-%d %H-%M-%S", time.localtime(ms / 1000))


def _delete_guard(cfg: PairConfig, stats: PlanStats, a: Scan, b: Scan) -> str | None:
    """Refuse plans that would delete an implausible share of a side.

    This is the backstop for the failure mode that motivated the rewrite: a
    root that momentarily looks empty — an unmounted disk, a cloud folder still
    populating — otherwise reads as "the user deleted everything".
    """
    for label, deletes, total in (
        ("A", stats.delete_a, len(a.entries)),
        ("B", stats.delete_b, len(b.entries)),
    ):
        if deletes < cfg.delete_threshold_min or total == 0:
            continue
        share = deletes / total
        if share > cfg.delete_threshold_pct:
            return (
                f"would delete {deletes} of {total} entries on side {label} "
                f"({share:.0%}, limit {cfg.delete_threshold_pct:.0%})"
            )
    return None


def _count_delete(stats: PlanStats, side: Side) -> None:
    if side is Side.A:
        stats.delete_a += 1
    else:
        stats.delete_b += 1


def _is_under(path: str, prefix: str) -> bool:
    return path.startswith(f"{prefix}/")


def _depth_key(op: Op) -> tuple[int, str]:
    """Deletions sort deepest-first; everything else shallowest-first."""
    if op.kind is OpKind.DELETE:
        return (-op.rel.count("/"), _reverse(op.rel))
    return (op.rel.count("/"), op.rel)


def _reverse(text: str) -> str:
    # Sorting descending within a tuple that also sorts ascending needs a key
    # that inverts the comparison; inverting each code point does that.
    return "".join(chr(0x10FFFF - ord(ch)) for ch in text)


def validate_roots(cfg: PairConfig) -> None:
    """Verify both roots are usable before any destructive work is planned."""
    from .errors import RootError

    for side in (Side.A, Side.B):
        root = cfg.root(side)
        if not root.exists():
            raise RootError(f"folder {side.label} does not exist: {root}", root)
        if not root.is_dir():
            raise RootError(f"folder {side.label} is not a directory: {root}", root)
        if cfg.require_marker and not (root / ".mt-sync-root").exists():
            raise RootError(
                f"folder {side.label} is missing its .mt-sync-root marker: {root}", root
            )

    # Resolve so symlinked or differently-spelled paths to the same place are
    # still caught.
    resolved_a = Path(cfg.path_a).resolve()
    resolved_b = Path(cfg.path_b).resolve()
    if resolved_a == resolved_b:
        raise RootError("the two folders of a pair must not be the same", resolved_a)
    if resolved_a.is_relative_to(resolved_b) or resolved_b.is_relative_to(resolved_a):
        raise RootError(
            "one folder is inside the other, which would sync a folder into itself",
            resolved_a,
        )
