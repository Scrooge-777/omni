"""Safe junk-file cleanup: scan first, delete only old files, report only what was really deleted.

Upgrades over ubat 1.x:
- a dry-run scan shows each category's size before anything is deleted;
- files newer than cleanup.min_age_hours are kept, so running apps don't lose their temp files;
- symlinks and junctions are never followed (a junction inside %TEMP% can't lead the cleaner elsewhere);
- a file only counts as freed if deleting it actually succeeded;
- the Windows Update cache is left to Windows (Storage Sense), since deleting it while
  the update service runs can break updates.
"""

import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path

from omni import win


@dataclass
class Category:
    key: str
    label: str
    path: Path
    admin: bool = False
    note: str = ""


def categories() -> list[Category]:
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    return [
        Category("user_temp", "Your temporary files", Path(os.environ.get("TEMP", local / "Temp")),
                 note="Files apps left in %TEMP%"),
        Category("crash_dumps", "App crash dumps", local / "CrashDumps", note="Memory dumps from crashed apps"),
        Category("error_reports", "Windows error reports", local / "Microsoft" / "Windows" / "WER",
                 note="Queued and archived crash reports"),
        Category("thumb_cache", "Old thumbnail cache files", local / "Microsoft" / "Windows" / "Explorer",
                 note="Rebuilt automatically when needed; in-use files are skipped"),
        Category("windows_temp", "Windows temporary files", windir / "Temp", admin=True),
        Category("minidumps", "System crash minidumps", windir / "Minidump", admin=True),
    ]


def _iter_files(root: Path, min_age_s: float, pattern: str | None = None):
    """Yield (path, size) for regular files older than min_age_s. Never follows links or junctions."""
    cutoff = time.time() - min_age_s
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if _is_link(entry):
                continue
            if stat.S_ISDIR(info.st_mode):
                stack.append(Path(entry.path))
            elif stat.S_ISREG(info.st_mode) and info.st_mtime < cutoff:
                if pattern and not entry.name.lower().startswith(pattern):
                    continue
                yield Path(entry.path), info.st_size


def _pattern(category: Category):
    return "thumbcache_" if category.key == "thumb_cache" else None


def scan(min_age_hours: float) -> list[dict]:
    admin = win.is_admin()
    results = []
    for category in categories():
        files = size = 0
        if category.path.is_dir():
            for _, file_size in _iter_files(category.path, min_age_hours * 3600, _pattern(category)):
                files += 1
                size += file_size
        results.append({
            "key": category.key, "label": category.label, "path": str(category.path), "note": category.note,
            "admin": category.admin, "allowed": admin or not category.admin, "files": files, "bytes": size,
        })
    return results


def clean(keys: list[str], min_age_hours: float) -> dict:
    admin = win.is_admin()
    chosen = [c for c in categories() if c.key in keys]
    report = {"deleted_files": 0, "deleted_bytes": 0, "skipped_files": 0, "categories": []}
    for category in chosen:
        if category.admin and not admin:
            report["categories"].append({"key": category.key, "error": "Needs OMNI to run as administrator"})
            continue
        deleted = freed = skipped = 0
        if category.path.is_dir():
            for path, size in _iter_files(category.path, min_age_hours * 3600, _pattern(category)):
                try:
                    path.unlink()
                except OSError:  # in use or access denied: leave it and don't count it
                    skipped += 1
                    continue
                deleted += 1
                freed += size
            _remove_empty_dirs(category.path)
        report["categories"].append({"key": category.key, "deleted_files": deleted, "deleted_bytes": freed,
                                     "skipped_files": skipped})
        report["deleted_files"] += deleted
        report["deleted_bytes"] += freed
        report["skipped_files"] += skipped
    return report


def _is_link(entry: os.DirEntry) -> bool:
    try:
        attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return True
    return entry.is_symlink() or bool(attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _remove_empty_dirs(root: Path, is_root: bool = True):
    """Remove empty sub-folders bottom-up. Never the category root, never through links or junctions."""
    try:
        entries = list(os.scandir(root))
    except OSError:
        return
    for entry in entries:
        if entry.is_dir(follow_symlinks=False) and not _is_link(entry):
            _remove_empty_dirs(Path(entry.path), is_root=False)
    if not is_root:
        try:
            os.rmdir(root)  # fails harmlessly if the folder still has something in it
        except OSError:
            pass
