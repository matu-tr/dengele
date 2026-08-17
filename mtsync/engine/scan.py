"""Reading a root directory into the metadata the planner compares."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from .exclude import ExcludeSet
from .models import Entry, EntryKind

# Windows marks not-yet-downloaded cloud files with these attributes.
_FILE_ATTRIBUTE_OFFLINE = 0x0000_1000
_FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x0004_0000
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x0040_0000
_CLOUD_ATTRIBUTES = (
    _FILE_ATTRIBUTE_OFFLINE | _FILE_ATTRIBUTE_RECALL_ON_OPEN | _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
)


@dataclass(slots=True)
class Scan:
    """The contents of one root, keyed by forward-slash relative path.

    Keys are kept sorted so a parent directory is always visited before its
    children; the planner relies on that to prune whole subtrees.
    """

    entries: dict[str, Entry] = field(default_factory=dict)
    #: Paths skipped because they are cloud placeholders, not real content.
    placeholders: list[str] = field(default_factory=list)
    #: Paths that could not be read, as (relative path, reason).
    errors: list[tuple[str, str]] = field(default_factory=list)

    def sorted_keys(self) -> list[str]:
        return sorted(self.entries)


def scan_root(root: Path, excludes: ExcludeSet, skip_cloud_placeholders: bool = True) -> Scan:
    """Walk ``root`` and record every non-excluded entry.

    Unreadable entries are collected into :attr:`Scan.errors` rather than
    raising: one permission-denied folder should not stop an otherwise good
    sync. They are simply absent from ``entries``, and because the planner
    treats "absent from both sides" as nothing to do, a folder we could not
    read is never mistaken for one the user deleted.
    """
    scan = Scan()
    _walk(root, root, excludes, skip_cloud_placeholders, scan)
    return scan


def _walk(root: Path, directory: Path, excludes: ExcludeSet, skip_cloud: bool, scan: Scan) -> None:
    try:
        with os.scandir(directory) as it:
            children = list(it)
    except OSError as err:
        scan.errors.append((_rel(root, directory) or ".", _reason(err)))
        return

    for child in children:
        path = Path(child.path)
        rel = _rel(root, path)
        if rel is None:
            continue

        try:
            is_symlink = child.is_symlink()
            is_dir = child.is_dir(follow_symlinks=False)
        except OSError as err:
            scan.errors.append((rel, _reason(err)))
            continue

        if excludes.is_excluded(rel, is_dir=is_dir and not is_symlink):
            continue

        if is_symlink:
            try:
                target = os.readlink(path)
            except OSError as err:
                scan.errors.append((rel, _reason(err)))
                continue
            scan.entries[rel] = Entry(
                kind=EntryKind.SYMLINK,
                size=0,
                mtime_ms=0,
                link_target=target.replace("\\", "/"),
            )
            continue

        if is_dir:
            scan.entries[rel] = Entry(kind=EntryKind.DIR, size=0, mtime_ms=0)
            _walk(root, path, excludes, skip_cloud, scan)
            continue

        try:
            info = child.stat(follow_symlinks=False)
        except OSError as err:
            scan.errors.append((rel, _reason(err)))
            continue

        if skip_cloud and is_cloud_placeholder(rel, info):
            scan.placeholders.append(rel)
            continue

        scan.entries[rel] = Entry(
            kind=EntryKind.FILE,
            size=info.st_size,
            mtime_ms=mtime_ms(info),
        )


def _rel(root: Path, path: Path) -> str | None:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return None
    text = rel.as_posix()
    return None if text in ("", ".") else text


def _reason(err: OSError) -> str:
    return err.strerror or str(err)


def mtime_ms(info: os.stat_result) -> int:
    """Modification time in milliseconds since the Unix epoch."""
    return int(info.st_mtime * 1000)


def is_cloud_placeholder(rel: str, info: os.stat_result) -> bool:
    """Whether a file is a stub standing in for content that isn't local.

    Copying one of these produces a few-hundred-byte placeholder on the other
    side instead of the real file. macOS iCloud names them
    ``.original-name.icloud``; Windows marks them with reparse attributes.
    """
    name = rel.rsplit("/", 1)[-1]
    if name.startswith(".") and name.endswith(".icloud"):
        return True

    attributes = getattr(info, "st_file_attributes", 0)
    if attributes & _CLOUD_ATTRIBUTES:
        return True

    # macOS dataless files (iCloud "Optimise Storage") carry this flag.
    return bool(getattr(info, "st_flags", 0) & getattr(stat, "SF_DATALESS", 0x40000000))
