#!/usr/bin/env python3
"""Standalone System_log/meminfo.txt filter entrypoint."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from anr_evidence import MeminfoFilterOptions, SourceFilterContext, filter_meminfo_source

_TEXT_FILE_SUFFIXES = {".txt", ".log", ".out", ""}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Filter System_log/meminfo.txt for target package memory and high-memory/high-load process evidence. "
            "Pass --process/--pid for high CPU/load offenders found in AnrManager Top CPU processes."
        )
    )
    parser.add_argument("input", help="meminfo.txt file or bugreport directory containing System_log/meminfo.txt")
    parser.add_argument("-o", "--output", help="Write output JSON to a file instead of stdout")
    parser.add_argument("--package", dest="package_name", help="Target/current package name, e.g. com.tcl.android.launcher")
    parser.add_argument("--process", dest="high_processes", action="append", default=[], help="High-load process name to inspect in meminfo; repeatable")
    parser.add_argument("--pid", dest="high_pids", action="append", type=int, default=[], help="High-load process pid to inspect in meminfo; repeatable")
    parser.add_argument("--top", dest="top_n", type=int, default=8, help="Number of latest top PSS/RSS processes to retain (default: 8)")
    parser.add_argument("--all-snapshots", action="store_true", help="Search requested high-load processes in all snapshots, not only latest")
    args = parser.parse_args()

    result = filter_input(
        Path(args.input),
        package_name=args.package_name,
        high_processes=tuple(args.high_processes),
        high_pids=tuple(args.high_pids),
        top_n=args.top_n,
        include_all_snapshots=args.all_snapshots,
    )
    rendered = json.dumps(result, indent=2, ensure_ascii=False, default=str)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


def filter_input(
    input_path: Path,
    *,
    package_name: str | None = None,
    high_processes: tuple[str, ...] = (),
    high_pids: tuple[int, ...] = (),
    top_n: int = 8,
    include_all_snapshots: bool = False,
) -> dict[str, Any]:
    source = _load_meminfo_source(input_path)
    result = filter_meminfo_source(
        source,
        SourceFilterContext(package_name=package_name),
        MeminfoFilterOptions(
            package_name=package_name,
            high_processes=high_processes,
            high_pids=high_pids,
            top_n=top_n,
            include_all_snapshots=include_all_snapshots,
        ),
    )
    return {
        "sourceKind": result.source_kind,
        "evidence": result.evidence,
        "warnings": result.warnings,
        "lines": result.lines,
        "metadata": result.metadata,
    }


def _load_meminfo_source(input_path: Path) -> dict[str, Any]:
    if not input_path.exists():
        raise SystemExit(f"Path does not exist: `{input_path}`")
    if input_path.is_file():
        if input_path.suffix.lower() not in _TEXT_FILE_SUFFIXES:
            raise SystemExit(f"Expected a text meminfo file, got `{input_path.name}`.")
        return {"path": str(input_path), "content": input_path.read_text(encoding="utf-8", errors="replace"), "readable": True}
    if input_path.is_dir():
        candidates = [
            input_path / "System_log" / "meminfo.txt",
            input_path / "meminfo.txt",
        ]
        candidates.extend(sorted(input_path.rglob("meminfo.txt")))
        seen: set[Path] = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            if candidate.is_file():
                return {
                    "path": str(candidate.relative_to(input_path)),
                    "content": candidate.read_text(encoding="utf-8", errors="replace"),
                    "readable": True,
                }
        raise SystemExit(f"No meminfo.txt found under `{input_path}` (expected System_log/meminfo.txt).")
    raise SystemExit(f"Unsupported input type: `{input_path}`")


if __name__ == "__main__":
    raise SystemExit(main())
