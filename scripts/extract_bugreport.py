#!/usr/bin/env python3
"""Extract a bugreport archive into a directory named after the archive file."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import sys
import tarfile
import zipfile


def _archive_suffixes(path: Path) -> tuple[str, ...]:
    lowered = [s.lower() for s in path.suffixes]
    # Handle multi-part suffixes like .tar.gz, .tar.bz2, .tar.xz
    if len(lowered) >= 2 and lowered[-2] == ".tar":
        return tuple(lowered[-2:])
    if len(lowered) >= 2 and lowered[-1] in (".gz", ".bz2", ".xz") and lowered[-2] == ".tar":
        return tuple(lowered[-2:])
    return tuple(lowered[-1:])


def extract_dir_for(path: Path) -> str:
    """Derive output directory name by stripping the archive extension(s)."""
    name = path.name
    suffixes = [s.lower() for s in path.suffixes]
    if len(suffixes) >= 2 and suffixes[-2] == ".tar" and suffixes[-1] in (".gz", ".bz2", ".xz"):
        return name[: -len(suffixes[-2]) - len(suffixes[-1])]
    if suffixes and suffixes[-1] in (".zip", ".tgz", ".tar"):
        return name[: -len(suffixes[-1])]
    return name + "_extracted"


def extract_bugreport(archive_path: Path, out_root: Path) -> Path:
    suffixes = [s.lower() for s in archive_path.suffixes]
    out_dir = out_root / extract_dir_for(archive_path)

    if suffixes[-1] == ".zip":
        with zipfile.ZipFile(archive_path) as zf:
            _extract_zip_safely(zf, out_dir)
    elif suffixes[-1] in (".tar", ".gz", ".bz2", ".xz", ".tgz") or (len(suffixes) >= 2 and suffixes[-2] == ".tar"):
        with tarfile.open(archive_path) as tf:
            _extract_tar_safely(tf, out_dir)
    else:
        print(f"Unsupported archive: {archive_path.name}", file=sys.stderr)
        sys.exit(1)

    return out_dir


def _safe_member_destination(out_dir: Path, member_name: str) -> Path:
    """Resolve an archive member to a safe destination under *out_dir*.

    Archive member names are POSIX by convention, but real-world archives may
    contain Windows backslashes. Normalize both forms and reject absolute
    paths, drive-prefixed paths, and ``..`` traversal before writing files.
    """

    normalized = member_name.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if (
        not parts
        or normalized.startswith("/")
        or any(part == ".." for part in parts)
        or (len(parts[0]) == 2 and parts[0][1] == ":")
    ):
        raise ValueError(f"unsafe archive member path: {member_name!r}")

    destination = out_dir.joinpath(*parts)
    root = out_dir.resolve()
    resolved_destination = destination.resolve(strict=False)
    if os.path.commonpath([str(root), str(resolved_destination)]) != str(root):
        raise ValueError(f"unsafe archive member path: {member_name!r}")
    return destination


def _extract_zip_safely(archive: zipfile.ZipFile, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for member in archive.infolist():
        destination = _safe_member_destination(out_dir, member.filename)
        if member.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as src, destination.open("wb") as dst:
            shutil.copyfileobj(src, dst)


def _extract_tar_safely(archive: tarfile.TarFile, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for member in archive.getmembers():
        destination = _safe_member_destination(out_dir, member.name)
        if member.isdir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        if not member.isfile():
            # Skip symlinks/devices/etc. for portable, data-only extraction.
            continue
        extracted = archive.extractfile(member)
        if extracted is None:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        with extracted, destination.open("wb") as dst:
            shutil.copyfileobj(extracted, dst)


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract a bugreport archive into a directory named after the file")
    parser.add_argument("archive", type=Path, help="Path to bugreport archive (.zip, .tar, .tar.gz, .tgz, .tar.bz2, .tar.xz)")
    parser.add_argument("--out", "-o", type=Path, default=Path("."), help="Output root directory (default: current directory)")
    args = parser.parse_args()

    if not args.archive.is_file():
        print(f"Error: archive not found: {args.archive}", file=sys.stderr)
        return 1

    try:
        out_dir = extract_bugreport(args.archive.resolve(), args.out.resolve())
    except (OSError, ValueError, zipfile.BadZipFile, tarfile.TarError) as exc:
        print(f"Error: failed to extract archive: {exc}", file=sys.stderr)
        return 1
    print(f"Extracted to: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
