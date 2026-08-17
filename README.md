# MT Sync

Keeps two folders identical, in both directions, on macOS and Windows.

Built for mirroring an iCloud Drive folder onto an external disk, but nothing
in it is iCloud-specific: any two folders will do.

## What it does

- **Real two-way sync.** It remembers the state both folders agreed on at the
  end of the last sync, so it can tell a deletion apart from a creation. A file
  you make on one side is copied to the other, not treated as something the
  other side deleted.
- **Conflicts are surfaced, not swallowed.** If both sides changed the same
  file, one wins according to your policy and the other is kept next to it —
  never discarded.
- **Deletions go to a recycle bin**, one folder per sync run, pruned after a
  number of days you choose.
- **It refuses to do something drastic without asking.** If a plan would delete
  an implausible share of a folder — the signature of an unmounted drive — it
  stops and shows you the list first.
- **Runs in the background.** Lives in the menu bar / system tray, watches for
  changes, and can start with the computer.

## Install

Download the build for your platform from
[Releases](https://github.com/matu-tr/mt-sync-py/releases). Builds are
currently **unsigned**, so both systems object the first time:

- **macOS** — after moving the app to Applications, run
  `xattr -dr com.apple.quarantine "/Applications/MT Sync.app"`.
- **Windows** — SmartScreen shows an "unknown publisher" warning; choose
  *More info* → *Run anyway*.

### macOS folder permissions

macOS gates Desktop, Documents, Downloads and external drives behind its
privacy system. The first sync touching one of those asks for permission. If no
prompt appears, allow MT Sync under **System Settings › Privacy & Security ›
Files and Folders** — or grant Full Disk Access, which covers everything.

MT Sync checks it can actually read each folder before scanning, so a denial
shows up as a clear message rather than a sync that never finishes.

## Using it

1. **Add pair** and pick two folders. On macOS the iCloud Drive folder is
   offered as a shortcut; on Windows, `%USERPROFILE%\iCloudDrive` when the
   iCloud client is installed.
2. Hit **Preview** to see exactly what the first sync would copy and remove.
   Worth doing once for any pair holding real data.
3. **Sync**. After that, leave it in the tray and it keeps up on its own.

### Safety settings worth knowing about

Under a pair's ⚙ → **Safety**:

- **Require a marker file** — put a `.mt-sync-root` file in both folders and
  turn this on, and a sync refuses to run when a drive is not mounted.
  Recommended for external disks.
- **Hold a sync deleting over X%** — the plan is shown to you instead of being
  applied. Defaults to 20%, with a floor of 50 files so small cleanups are not
  interrupted.
- **Skip files that are not downloaded** — iCloud leaves placeholder stubs for
  files kept only in the cloud. Copying one produces a stub, not the file.

## How it decides

For every path, three things are compared: side A, side B, and the snapshot of
what they last agreed on.

| A | B | Snapshot | Result |
|---|---|---|---|
| unchanged | unchanged | present | nothing |
| changed | unchanged | present | copy A → B |
| unchanged | changed | present | copy B → A |
| changed | changed | present | conflict — winner copied, loser kept alongside |
| present | absent | absent | new on A → copy to B |
| present | absent | present | deleted on B → recycle from A |
| edited | absent | present | conflict — the edit is kept |

Change detection is metadata-first (size and modification time, with a
two-second tolerance for FAT-formatted disks). Hashing runs only when metadata
cannot decide, which is what makes repeat syncs cheap.

## Development

Requires Python 3.11 or newer.

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m mtsync
```

The sync engine is a self-contained package with no Qt dependency, so it can be
worked on and tested without a GUI:

```bash
pytest tests/test_sync.py tests/test_units.py
```

The UI tests run headless through Qt's offscreen platform:

```bash
QT_QPA_PLATFORM=offscreen pytest
```

On Linux they need Qt's graphics libraries (`libgl1`, `libxkbcommon-x11-0`,
`libegl1`, `libdbus-1-3` and the `libxcb-*` set); without them the UI tests
skip themselves and the engine tests still run.

### Layout

| Path | What lives there |
|---|---|
| `mtsync/engine` | Scanning, planning, applying, the snapshot database — all the sync logic, no Qt |
| `mtsync/app` | Configuration, the sync controller and its threads, file watching, autostart |
| `mtsync/ui` | The window, its screens, the tray, and the theme |
| `packaging` | PyInstaller spec, the Windows installer script, icon generation |

## License

MIT — see [LICENSE](LICENSE).

### Third-party components

The published builds bundle:

| Component | License |
|---|---|
| [Qt](https://www.qt.io/) via [PySide6](https://doc.qt.io/qtforpython/) | LGPL-3.0 |
| [watchdog](https://github.com/gorakhargosh/watchdog) | Apache-2.0 |
| [pathspec](https://github.com/cpburnz/python-pathspec) | MPL-2.0 |

Qt is used under the LGPL, which is why it is bundled as shared libraries
rather than linked statically: you are free to replace them with your own build
of Qt. Qt's source is available from [qt.io](https://www.qt.io/download-open-source).
