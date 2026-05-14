#!/usr/bin/env python3
"""Standalone logcat source filter entrypoint."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from anr_evidence import ArchiveLoadError, SourceFilterContext, SourceFilterOptions, filter_logcat_anrmanager_block, filter_logcat_source, load_package_from_path
from anr_evidence.sources.shared import parse_raw_timestamp


def main() -> int:
    parser = argparse.ArgumentParser(description="Filter one ANR logcat source.")
    parser.add_argument("input", help="Logcat text file, package JSON, archive, or directory")
    parser.add_argument("-o", "--output", help="Write output JSON to a file instead of stdout")
    parser.add_argument("--anchor", help="Optional anchor timestamp, e.g. 04-12 10:00:05.000")
    parser.add_argument("--package", dest="package_name", help="Optional package name filter")
    parser.add_argument("--anrmanager", action="store_true", help="Extract AnrManager diagnostic block instead of timestamp window")
    args = parser.parse_args()

    result = filter_input(Path(args.input), anchor=args.anchor, package_name=args.package_name, anrmanager=args.anrmanager)
    rendered = json.dumps(result, indent=2, ensure_ascii=False, default=str)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


def filter_input(input_path: Path, *, anchor: str | None = None, package_name: str | None = None, anrmanager: bool = False) -> dict[str, Any]:
    source = _load_source(input_path)
    context = SourceFilterContext(anchor_dt=parse_raw_timestamp(anchor), package_name=package_name)
    options = SourceFilterOptions(package_name=package_name)
    result = filter_logcat_anrmanager_block(source, context, options) if anrmanager else filter_logcat_source(source, context, options)
    return {
        "sourceKind": result.source_kind,
        "evidence": result.evidence,
        "warnings": result.warnings,
        "lines": result.lines,
        "metadata": result.metadata,
    }


def _load_source(input_path: Path) -> dict[str, Any]:
    if input_path.is_file() and input_path.suffix.lower() not in {".json", ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz"}:
        return {"path": str(input_path), "content": input_path.read_text(encoding="utf-8", errors="replace"), "readable": True}
    try:
        package = load_package_from_path(input_path)
    except ArchiveLoadError as exc:
        raise SystemExit(str(exc)) from exc
    source = package.get("sources", {}).get("logcat")
    if not source:
        raise SystemExit(f"No logcat source found in `{input_path}`.")
    return source


if __name__ == "__main__":
    raise SystemExit(main())
