"""Input loaders for fixtures, directories, archives, and mixed paths."""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import contextmanager, ExitStack
import bz2
import base64
import gzip
import lzma
from datetime import datetime
import json
from pathlib import Path
import stat
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


class ArchivePolicyError(ArchiveLoadError):
    """A safety rejection, which directory fallback must not silently ignore."""


# These limits apply to the uncompressed archive view.  Keeping them centralized
# preserves the public loader API and lets tests exercise each gate with tiny
# fixtures.
MAX_ARCHIVE_MEMBERS = 10_000
MAX_ARCHIVE_MEMBER_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 2 * 1024 * 1024 * 1024


def load_package_from_fixture(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_package_from_directory(
    path: str | Path,
    package_name: str | None = None,
    event_anchor_dt: datetime | None = None,
    *,
    capture_originals: bool = False,
) -> dict[str, Any]:
    root = Path(path)
    if event_anchor_dt is None:
        event_anchor_dt = find_event_anr_timestamp_by_command(root, package_name)
    # Try smart Monkey-test discovery first (System_log/ directory with
    # System_MT_logcat* files). Falls back to full recursive traversal.
    smart_entries = None if capture_originals else try_smart_monkey_discovery(root, package_name=package_name)
    if smart_entries is not None:
        smart_package = build_package_from_entries(
            root.name,
            smart_entries,
            package_name=package_name,
            event_anchor_dt=event_anchor_dt,
        )
        if _has_core_anr_sources(smart_package):
            return smart_package
    # Original fallback: load everything recursively.
    entries = []
    for file_path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        original = {}
        if capture_originals:
            raw = file_path.read_bytes()
            try:
                content = raw.decode("utf-8")
                readable = True
            except UnicodeDecodeError:
                content = raw.decode("utf-8", errors="replace")
                readable = False
            # Keep the legacy text view's universal-newline behavior.
            content = content.replace("\r\n", "\n").replace("\r", "\n")
            original = {
                "originalBase64": base64.b64encode(raw).decode("ascii"),
                "origin": {"type": "file", "path": str(file_path.absolute())},
            }
        else:
            try:
                content = file_path.read_text(encoding="utf-8")
                readable = True
            except UnicodeDecodeError:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                readable = False
        entries.append(
            {
                **original,
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
    *,
    capture_originals: bool = False,
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
        original = {}
        if capture_originals:
            raw = file_path.read_bytes()
            try:
                content = raw.decode("utf-8")
                readable = True
            except UnicodeDecodeError:
                content = raw.decode("utf-8", errors="replace")
                readable = False
            # Keep the legacy text view's universal-newline behavior.
            content = content.replace("\r\n", "\n").replace("\r", "\n")
            original = {
                "originalBase64": base64.b64encode(raw).decode("ascii"),
                "origin": {"type": "file", "path": str(file_path.absolute())},
            }
        else:
            try:
                content = file_path.read_text(encoding="utf-8")
                readable = True
            except UnicodeDecodeError:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                readable = False
        entries.append(
            {
                **original,
                "path": str(file_path.relative_to(path)),
                "content": content,
                "readable": readable,
            }
        )
    if not entries:
        return None
    package = build_package_from_entries(path.name, entries, package_name=package_name, event_anchor_dt=event_anchor_dt)
    return package if package.get("sources") else None


def load_package_from_archive(path: str | Path, package_name: str | None = None, *,
                              capture_originals: bool = False) -> dict[str, Any]:
    archive_path = Path(path)
    suffixes = [suffix.lower() for suffix in archive_path.suffixes]
    entries: list[dict[str, Any]] = []
    if suffixes and suffixes[-1] == ".zip":
        try:
            with zipfile.ZipFile(archive_path) as archive:
                members = archive.infolist()
                _validate_archive_member_count(archive_path, len(members))
                for member in members:
                    _validate_archive_member_name(archive_path, member.filename)
                    if _zip_member_is_link(member):
                        raise ArchivePolicyError(
                            f"Archive `{archive_path.name}` contains unsupported link member `{member.filename}`."
                        )
                files = [member for member in members if not member.is_dir()]
                _validate_archive_metadata_sizes(
                    archive_path,
                    ((member.filename, member.file_size) for member in files),
                )
                actual_total = 0
                for member in sorted(files, key=lambda item: item.filename):
                    original = {}
                    try:
                        with archive.open(member) as extracted:
                            content, readable, actual_size = _read_archive_member(
                                extracted,
                                member.filename,
                                max_bytes=min(MAX_ARCHIVE_MEMBER_BYTES, MAX_ARCHIVE_TOTAL_BYTES - actual_total),
                                original_out=original if capture_originals else None,
                            )
                    except OSError as exc:
                        raise ArchiveLoadError(f"Failed to read zip member `{member.filename}` from `{archive_path.name}`: {exc}") from exc
                    actual_total += actual_size
                    if capture_originals:
                        original["origin"] = {"type": "archive_member", "container": str(archive_path.absolute()),
                                              "member": member.filename, "memberOffset": str(member.header_offset)}
                    entries.append({"path": member.filename, "content": content, "readable": readable, **original})
        except zipfile.BadZipFile as exc:
            raise ArchiveLoadError(f"Archive `{archive_path.name}` is not a readable zip file or is corrupted.") from exc
    elif any(suffix in {".tar", ".gz", ".tgz", ".bz2", ".xz"} for suffix in suffixes):
        try:
            with _open_bounded_tar(archive_path) as archive:
                actual_total = 0
                declared_total = 0
                for count, member in enumerate(archive, 1):
                    _validate_archive_member_count(archive_path, count)
                    _validate_archive_member_name(archive_path, member.name)
                    if member.issym() or member.islnk():
                        raise ArchivePolicyError(
                            f"Archive `{archive_path.name}` contains unsupported link member `{member.name}`."
                        )
                    if not member.isfile() and not member.isdir():
                        raise ArchivePolicyError(
                            f"Archive `{archive_path.name}` contains unsupported special member `{member.name}`."
                        )
                    if not member.isfile():
                        continue
                    # Check before advancing to the next header: in compressed
                    # TARs, skipping an unchecked payload still decompresses it.
                    _validate_archive_metadata_sizes(archive_path, [(member.name, member.size)])
                    declared_total += member.size
                    _validate_archive_total_size(archive_path, declared_total)
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        raise ArchiveLoadError("Unable to read declared TAR file member")
                    original = {}
                    try:
                        with extracted:
                            content, readable, actual_size = _read_archive_member(
                                extracted,
                                member.name,
                                max_bytes=min(MAX_ARCHIVE_MEMBER_BYTES, MAX_ARCHIVE_TOTAL_BYTES - actual_total),
                                original_out=original if capture_originals else None,
                            )
                    except OSError as exc:
                        raise ArchiveLoadError(f"Failed to read tar member `{member.name}` from `{archive_path.name}`: {exc}") from exc
                    actual_total += actual_size
                    if capture_originals:
                        original["origin"] = {"type": "archive_member", "container": str(archive_path.absolute()),
                                              "member": member.name, "memberOffset": str(member.offset)}
                    entries.append({"path": member.name, "content": content, "readable": readable, **original})
                entries.sort(key=lambda entry: entry["path"])
        except (tarfile.TarError, OSError, EOFError, lzma.LZMAError) as exc:
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
    entries = try_smart_monkey_discovery(path, package_name=package_name)
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


def load_package_from_path(input_path: str | Path, package_name: str | None = None, *,
                           capture_originals: bool = False) -> dict[str, Any]:
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
            return load_package_from_archive(path, package_name=package_name, capture_originals=capture_originals)
        raise ArchiveLoadError(f"Unsupported file type: `{path.name}`")

    if path.is_dir():
        archives = find_archives_in_directory(path)
        event_anchor_dt = find_event_anr_timestamp_by_command(path, package_name)
        # Try smart Monkey-test discovery regardless of whether archives
        # exist — the directory may contain System_log/ files that are more
        # complete than what's in the archive (especially for Monkey test
        # results where the zip only contains a partial bugreport snapshot).
        smart_package = None if capture_originals else try_load_smart_package(path, package_name=package_name, event_anchor_dt=event_anchor_dt)
        if smart_package is not None and _has_core_anr_sources(smart_package):
            return smart_package
        if not archives:
            if smart_package is not None:
                return smart_package
            # No archives — load directory directly
            return load_package_from_directory(path, package_name=package_name, event_anchor_dt=event_anchor_dt, capture_originals=capture_originals)

        # Extract all archives found and merge into a single package.
        all_entries: list[dict[str, Any]] = []
        for archive_path in archives:
            try:
                package = load_package_from_archive(archive_path, package_name=package_name, capture_originals=capture_originals)
            except ArchivePolicyError:
                raise
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
                        "originalArtifacts": src.get("originalArtifacts", []),
                    })

        if not all_entries and smart_package is not None:
            return smart_package
        if not all_entries:
            # Fallback: try loading directory directly
            return load_package_from_directory(path, package_name=package_name, event_anchor_dt=event_anchor_dt, capture_originals=capture_originals)

        archive_pkg = build_package_from_entries(
            path.name,
            all_entries,
            package_name=package_name,
            event_anchor_dt=event_anchor_dt,
        )
        loose_package = _load_loose_package_from_directory(path, package_name=package_name, event_anchor_dt=event_anchor_dt, capture_originals=capture_originals)
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


def _has_core_anr_sources(package: dict[str, Any]) -> bool:
    """Return True when a package has enough evidence to build ANR context.

    Kernel logs are valuable but not required by the AI-context completeness
    gate.  Treating trace/EventLog/logcat as the core set lets Monkey result
    directories with complete loose evidence avoid loading duplicate archive
    snapshots that are often much larger and staler.
    """

    sources = package.get("sources", {}) or {}
    return all(sources.get(kind, {}).get("content") for kind in ("trace", "event_log", "logcat"))


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


class _BoundedTarStream:
    """Limit decompressed bytes including PAX/GNU headers, not only file data."""

    def __init__(self, source, limit: int):
        self.source = source
        self.remaining = limit

    def read(self, size: int) -> bytes:
        data = self.source.read(min(size, self.remaining + 1))
        self.remaining -= len(data)
        if self.remaining < 0:
            raise ArchivePolicyError("TAR decompressed stream exceeds byte budget (including metadata)")
        return data


@contextmanager
def _open_bounded_tar(path: Path):
    with ExitStack() as stack:
        raw = stack.enter_context(path.open("rb"))
        magic = raw.read(6)
        raw.seek(0)
        if magic.startswith(b"\x1f\x8b"):
            source = stack.enter_context(gzip.GzipFile(fileobj=raw))
        elif magic.startswith(b"BZh"):
            source = stack.enter_context(bz2.BZ2File(raw))
        elif magic.startswith(b"\xfd7zXZ\x00"):
            source = stack.enter_context(lzma.LZMAFile(raw))
        else:
            source = raw
        # Allow normal headers/padding in addition to the declared data budget.
        # Oversized extension metadata is rejected even before TarInfo exists.
        limit = MAX_ARCHIVE_TOTAL_BYTES + MAX_ARCHIVE_MEMBERS * 1024 + 10240
        bounded = _BoundedTarStream(source, limit)
        archive = stack.enter_context(tarfile.open(fileobj=bounded, mode="r|"))
        yield archive


def _validate_archive_member_count(archive_path: Path, count: int) -> None:
    if count > MAX_ARCHIVE_MEMBERS:
        raise ArchivePolicyError(
            f"Archive `{archive_path.name}` member count {count} exceeds limit {MAX_ARCHIVE_MEMBERS}."
        )


def _validate_archive_member_name(archive_path: Path, name: str) -> None:
    normalized = name.replace("\\", "/")
    parts = normalized.split("/")
    has_drive_prefix = bool(parts and len(parts[0]) >= 2 and parts[0][1] == ":")
    if (
        not normalized
        or "\x00" in normalized
        or normalized.startswith("/")
        or has_drive_prefix
        or any(part == ".." for part in parts)
    ):
        raise ArchivePolicyError(
            f"Archive `{archive_path.name}` contains unsafe member name `{name}`."
        )


def _zip_member_is_link(member: zipfile.ZipInfo) -> bool:
    if member.create_system != 3:
        return False
    return stat.S_IFMT(member.external_attr >> 16) == stat.S_IFLNK


def _validate_archive_metadata_sizes(archive_path: Path, members: Iterable[tuple[str, int]]) -> None:
    total = 0
    for name, size in members:
        if size < 0:
            raise ArchivePolicyError(
                f"Archive `{archive_path.name}` member `{name}` has invalid negative uncompressed size {size}."
            )
        if size > MAX_ARCHIVE_MEMBER_BYTES:
            raise ArchivePolicyError(
                f"Archive `{archive_path.name}` member `{name}` is {size} bytes uncompressed, "
                f"exceeding per-member limit {MAX_ARCHIVE_MEMBER_BYTES}."
            )
        total += size
        _validate_archive_total_size(archive_path, total)


def _validate_archive_total_size(archive_path: Path, total: int) -> None:
    if total > MAX_ARCHIVE_TOTAL_BYTES:
        raise ArchivePolicyError(
            f"Archive `{archive_path.name}` total uncompressed size {total} bytes exceeds limit "
            f"{MAX_ARCHIVE_TOTAL_BYTES}."
        )


def _read_archive_member(handle, name: str, *, max_bytes: int,
                         original_out: dict[str, Any] | None = None) -> tuple[str, bool, int]:
    chunks: list[bytes] = []
    remaining = max_bytes + 1
    while remaining > 0:
        chunk = handle.read(min(1024 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    data = b"".join(chunks)
    if len(data) > max_bytes:
        raise ArchivePolicyError(
            f"Archive member `{name}` actual data exceeds bounded-read limit {max_bytes}."
        )
    if original_out is not None:
        original_out["originalBase64"] = base64.b64encode(data).decode("ascii")
    try:
        return data.decode("utf-8"), True, len(data)
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace"), False, len(data)
