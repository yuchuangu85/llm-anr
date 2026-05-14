"""Input loaders for fixtures, directories, archives, and mixed paths."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
from typing import Any
import zipfile

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
        if smart_package is None:
            return archive_pkg
        # Merge: use smart discovery to fill in sources that are missing or
        # appear stale in the archive package. Prefer the archive for trace
        # (it's the canonical ANR trace) unless the archive trace is empty.
        merged_sources = dict(archive_pkg.get("sources", {}))
        smart_sources = smart_package.get("sources", {})
        for source_kind in SOURCE_KINDS + OPTIONAL_SOURCE_KINDS:
            archive_src = merged_sources.get(source_kind)
            smart_src = smart_sources.get(source_kind)
            if smart_src is None or not smart_src.get("content"):
                continue
            if archive_src is None or not archive_src.get("content"):
                # Archive is missing this source — use smart discovery.
                merged_sources[source_kind] = smart_src
            elif source_kind in ("event_log", "logcat"):
                # For log data, prefer the smart discovery (it has the
                # time-aligned Monkey test log files rather than stale
                # archive snapshots).
                merged_sources[source_kind] = smart_src
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
    """Return the first package-matching ``am_anr`` timestamp using rg/grep.

    This is an optional directory fast path.  It deliberately falls back to the
    in-memory Python scanner when no supported command is available, when the
    command finds no usable timestamp, or when command execution fails.
    """

    if not package_name:
        return None

    command = _event_anr_search_command(root)
    if command is None:
        return None

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
        return None

    if completed.returncode not in (0, 1):
        return None

    for line in completed.stdout.splitlines():
        if package_name not in line:
            continue
        timestamp = parse_log_timestamp(line)
        if timestamp is not None:
            return timestamp
    return None


def _event_anr_search_command(root: Path) -> list[str] | None:
    rg = shutil.which("rg")
    if rg:
        return [rg, "-n", "--fixed-strings", "am_anr", str(root)]

    grep = shutil.which("grep")
    if grep:
        return [grep, "-R", "-n", "-F", "-I", "am_anr", str(root)]

    return None


def _read_archive_member(handle, name: str) -> tuple[str, bool]:
    data = handle.read()
    try:
        return data.decode("utf-8"), True
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace"), False
