#!/usr/bin/env python3
"""Standalone trace source filter entrypoint."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from anr_evidence import ArchiveLoadError, SourceFilterContext, filter_trace_source, load_package_from_path
from anr_evidence.sources.shared import parse_raw_timestamp


def main() -> int:
    parser = argparse.ArgumentParser(description="Filter/preprocess one ANR trace source.")
    parser.add_argument("input", help="Trace text file, package JSON, archive, or directory")
    parser.add_argument("-o", "--output", help="Write output JSON to a file instead of stdout")
    parser.add_argument("--anchor", help="Optional anchor timestamp, e.g. 04-12 10:00:05.000")
    args = parser.parse_args()

    result = filter_input(Path(args.input), anchor=args.anchor)
    rendered = json.dumps(result, indent=2, ensure_ascii=False, default=str)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


def filter_input(input_path: Path, *, anchor: str | None = None) -> dict[str, Any]:
    source = _load_source(input_path)
    result = filter_trace_source(source, SourceFilterContext(anchor_dt=parse_raw_timestamp(anchor)))
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
    source = package.get("sources", {}).get("trace")
    if not source:
        raise SystemExit(f"No trace source found in `{input_path}`.")
    return source


if __name__ == "__main__":
    raise SystemExit(main())
