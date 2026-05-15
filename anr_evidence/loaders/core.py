"""Input loaders for fixtures, directories, archives, and mixed paths."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
from typing import Any
import zipfile

from ..am_anr import package_name_from_am_anr_line
from ..constants import OPTIONAL_SOURCE_KINDS, SOURCE_KINDS
from ..discovery import try_smart_monkey_discovery
from ..log_filter import parse_log_timestamp
from .package import build_package_from_entries


class ArchiveLoadError(ValueError):
    """Raised when an archive cannot be read or does not contain usable log sources."""


def load_package_from_fixture(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_package_from_directory(
    path: str | Path,
    package_name: str | None = None,
    event_anchor_dt: datetime | None = None,
) -> dict[str, Any]:
    root = Path(path)
    if event_anchor_dt is None:
        event_anchor_dt = find_event_anr_timestamp_by_command(root, package_name)
    # Try smart Monkey-test discovery first (System_log/ directory with
    # System_MT_logcat* files). Falls back to full recursive traversal.
    smart_entries = try_smart_monkey_discovery(root)
    if smart_entries is not None:
        smart_package = build_package_from_entries(
            root.name,
            smart_entries,
            package_name=package_name,
            event_anchor_dt=event_anchor_dt,
        )
        if all(kind in smart_package.get("sources", {}) for kind in SOURCE_KINDS):
            return smart_package
    # Original fallback: load everything recursively.
    entries = []
    for file_path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        try:
            content = file_path.read_text(encoding="utf-8")
            readable = True
        except UnicodeDecodeError:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            readable = False
        entries.append(
            {
                "path": str(file_path.relative_to(root)),
                "content": content,
                "readable": readable,
            }
        )
    return build_package_from_entries(root.name, entries, package_name=package_name, event_anchor_dt=event_anchor_dt)


def _load_loose_package_from_directory(
    path: Path,
    package_name: str | None = None,
    event_anchor_dt: datetime | None = None,
) -> dict[str, Any] | None:
    """Load non-archive files that sit beside bugreport archives.

    Monkey/bugreport result directories often contain both a partial zip and
    richer loose files (event logs, System_MT_logcat shards, ``anr/`` traces,
    meminfo).  When archives are present the top-level loader cannot simply
    ignore those loose files, otherwise the generated AI context may fall back
    to a timeless or stale ``anr-unanchored`` group.
    """

    entries = []
    for file_path in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        if is_archive_path(file_path):
            continue
        try:
            content = file_path.read_text(encoding="utf-8")
            readable = True
        except UnicodeDecodeError:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            readable = False
        entries.append(
            {
                "path": str(file_path.relative_to(path)),
                "content": content,
                "readable": readable,
            }
        )
    if not entries:
        return None
    package = build_package_from_entries(path.name, entries, package_name=package_name, event_anchor_dt=event_anchor_dt)
    return package if package.get("sources") else None


def load_package_from_archive(path: str | Path, package_name: str | None = None) -> dict[str, Any]:
    archive_path = Path(path)
    suffixes = [suffix.lower() for suffix in archive_path.suffixes]
    entries: list[dict[str, Any]] = []
    if suffixes and suffixes[-1] == ".zip":
        try:
            with zipfile.ZipFile(archive_path) as archive:
                for member in sorted(archive.infolist(), key=lambda item: item.filename):
                    if member.is_dir():
                        continue
                    try:
                        content, readable = _read_archive_member(archive.open(member), member.filename)
                    except OSError as exc:
                        raise ArchiveLoadError(f"Failed to read zip member `{member.filename}` from `{archive_path.name}`: {exc}") from exc
                    entries.append({"path": member.filename, "content": content, "readable": readable})
        except zipfile.BadZipFile as exc:
            raise ArchiveLoadError(f"Archive `{archive_path.name}` is not a readable zip file or is corrupted.") from exc
    elif any(suffix in {".tar", ".gz", ".tgz", ".bz2", ".xz"} for suffix in suffixes):
        try:
            with tarfile.open(archive_path) as archive:
                members = [member for member in archive.getmembers() if member.isfile()]
                for member in sorted(members, key=lambda item: item.name):
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        continue
                    try:
                        content, readable = _read_archive_member(extracted, member.name)
                    except OSError as exc:
                        raise ArchiveLoadError(f"Failed to read tar member `{member.name}` from `{archive_path.name}`: {exc}") from exc
                    entries.append({"path": member.name, "content": content, "readable": readable})
        except tarfile.TarError as exc:
            raise ArchiveLoadError(f"Archive `{archive_path.name}` is not a readable tar archive or is corrupted.") from exc
    else:
        raise ArchiveLoadError(f"Unsupported archive type: `{archive_path.name}`. Supported formats: .zip, .tar, .tar.gz, .tgz, .tar.bz2, .tar.xz.")
    package = build_package_from_entries(archive_path.stem, entries, package_name=package_name)
    if not package["sources"]:
        raise ArchiveLoadError(f"Archive `{archive_path.name}` was readable, but no recognizable ANR log sources were found.")
    return package


def try_load_smart_package(
    path: Path,
    package_name: str | None = None,
    event_anchor_dt: datetime | None = None,
) -> dict[str, Any] | None:
    """Try smart Monkey-test discovery; return a package dict or None."""
    entries = try_smart_monkey_discovery(path)
    if entries is None:
        return None
    if event_anchor_dt is None:
        event_anchor_dt = find_event_anr_timestamp_by_command(path, package_name)
    return build_package_from_entries(path.name, entries, package_name=package_name, event_anchor_dt=event_anchor_dt)


def find_archives_in_directory(directory: Path) -> list[Path]:
    """Find archive files in a directory (non-recursive)."""
    archives: list[Path] = []
    for child in sorted(directory.iterdir()):
        if child.is_file() and is_archive_path(child):
            archives.append(child)
    return archives


def load_package_from_path(input_path: str | Path, package_name: str | None = None) -> dict[str, Any]:
    """Universal loader that auto-detects the input type.

    Handles:
    - A JSON fixture file → loads directly
    - An archive (.zip/.tar/.tar.gz/.tgz/.tar.bz2/.tar.xz) → extracts and loads
    - A directory containing extracted bugreport files → loads directory
    - A directory containing one or more archives → auto-extracts all, merges
    """
    path = Path(input_path)
    if not path.exists():
        raise ArchiveLoadError(f"Path does not exist: `{path}`")

    if path.is_file():
        if path.suffix.lower() == ".json":
            return load_package_from_fixture(path)
        if is_archive_path(path):
            return load_package_from_archive(path, package_name=package_name)
        raise ArchiveLoadError(f"Unsupported file type: `{path.name}`")

    if path.is_dir():
        archives = find_archives_in_directory(path)
        event_anchor_dt = find_event_anr_timestamp_by_command(path, package_name)
        # Try smart Monkey-test discovery regardless of whether archives
        # exist — the directory may contain System_log/ files that are more
        # complete than what's in the archive (especially for Monkey test
        # results where the zip only contains a partial bugreport snapshot).
        smart_package = try_load_smart_package(path, package_name=package_name, event_anchor_dt=event_anchor_dt)
        if not archives:
            if smart_package is not None:
                return smart_package
            # No archives — load directory directly
            return load_package_from_directory(path, package_name=package_name, event_anchor_dt=event_anchor_dt)

        # Extract all archives found and merge into a single package.
        all_entries: list[dict[str, Any]] = []
        for archive_path in archives:
            try:
                package = load_package_from_archive(archive_path, package_name=package_name)
            except ArchiveLoadError:
                continue
            sources = package.get("sources", {})
            for source_kind in SOURCE_KINDS + OPTIONAL_SOURCE_KINDS:
                src = sources.get(source_kind)
                if src and src.get("content"):
                    all_entries.append({
                        "path": f"{archive_path.name}/{src.get('path', source_kind)}",
                        "content": src["content"],
                        "readable": src.get("readable", True),
                    })

        if not all_entries and smart_package is not None:
            return smart_package
        if not all_entries:
            # Fallback: try loading directory directly
            return load_package_from_directory(path, package_name=package_name, event_anchor_dt=event_anchor_dt)

        archive_pkg = build_package_from_entries(
            path.name,
            all_entries,
            package_name=package_name,
            event_anchor_dt=event_anchor_dt,
        )
        loose_package = _load_loose_package_from_directory(path, package_name=package_name, event_anchor_dt=event_anchor_dt)
        if smart_package is None:
            if loose_package is None:
                return archive_pkg
            smart_package = loose_package
        # Merge: use smart/loose discovery to fill in sources that are missing
        # or appear stale in the archive package. Prefer time-aligned loose or
        # smart trace evidence when an EventLog am_anr anchor was found; this
        # keeps ANR1-style loose ``anr/anr_YYYY...`` traces from being shadowed
        # by a partial archive containing only later traces.
        merged_sources = dict(archive_pkg.get("sources", {}))
        overlay_packages = [pkg for pkg in (loose_package, smart_package) if pkg is not None]
        if smart_package is loose_package:
            overlay_packages = [loose_package]
        for source_kind in SOURCE_KINDS + OPTIONAL_SOURCE_KINDS:
            for overlay_pkg in overlay_packages:
                overlay_src = (overlay_pkg.get("sources", {}) or {}).get(source_kind)
                if overlay_src is None or not overlay_src.get("content"):
                    continue
                archive_src = merged_sources.get(source_kind)
                if archive_src is None or not archive_src.get("content"):
                    merged_sources[source_kind] = overlay_src
                elif source_kind in ("event_log", "logcat", "meminfo"):
                    # For log/memory data, prefer time-aligned loose/smart
                    # files over stale archive snapshots.
                    merged_sources[source_kind] = overlay_src
                elif source_kind == "trace" and event_anchor_dt is not None:
                    merged_sources[source_kind] = overlay_src
        return {
            "package_id": archive_pkg.get("package_id", path.name),
            "provided_type": archive_pkg.get("provided_type"),
            "sources": merged_sources,
        }

    raise ArchiveLoadError(f"Unsupported input type: `{path}`")


def is_archive_path(path: Path) -> bool:
    suffixes = [suffix.lower() for suffix in path.suffixes]
    if not suffixes:
        return False
    if suffixes[-1] == ".zip":
        return True
    return any(suffix in {".tar", ".gz", ".tgz", ".bz2", ".xz"} for suffix in suffixes)


def find_event_anr_timestamp_by_command(root: Path, package_name: str | None) -> datetime | None:
    """Return the first usable ``am_anr`` timestamp using fg/rg/grep.

    This is an optional directory fast path used before creating
    ``anr_ai_context``.  When *package_name* is provided, only matching
    ``am_anr`` lines are considered.  Without a package filter, the first
    timestamped ``am_anr`` line with an identifiable package/process is used
    as the EventLog anchor.  Command
    failures deliberately fall through to the next command so the caller can
    still fall back to the in-memory Python scanner.
    """

    for command in _event_anr_search_commands(root):
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue

        # grep-style tools return 1 for no matches.  Other non-zero statuses
        # usually mean unsupported flags or I/O errors; try the next command.
        if completed.returncode not in (0, 1):
            continue

        timestamp = _event_anr_timestamp_from_command_output(completed.stdout.splitlines(), package_name)
        if timestamp is not None:
            return timestamp
    return None


def _event_anr_timestamp_from_command_output(lines: Iterable[str], package_name: str | None) -> datetime | None:
    timestamps: list[datetime] = []
    for line in lines:
        if "am_anr" not in line.lower():
            continue
        if package_name:
            if package_name not in line:
                continue
        elif package_name_from_am_anr_line(line) is None:
            continue
        timestamp = parse_log_timestamp(line)
        if timestamp is not None:
            timestamps.append(timestamp)
    return min(timestamps) if timestamps else None


def _event_anr_search_commands(root: Path) -> list[list[str]]:
    commands: list[list[str]] = []

    fg = shutil.which("fg")
    if fg:
        commands.append([fg, "-n", "--fixed-strings", "am_anr", str(root)])

    rg = shutil.which("rg")
    if rg:
        commands.append([rg, "-n", "--fixed-strings", "am_anr", str(root)])

    grep = shutil.which("grep")
    if grep:
        commands.append([grep, "-R", "-n", "-F", "-I", "am_anr", str(root)])

    return commands


def _event_anr_search_command(root: Path) -> list[str] | None:
    """Backward-compatible single-command helper for older tests/callers."""

    commands = _event_anr_search_commands(root)
    return commands[0] if commands else None


def _read_archive_member(handle, name: str) -> tuple[str, bool]:
    data = handle.read()
    try:
        return data.decode("utf-8"), True
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace"), False
