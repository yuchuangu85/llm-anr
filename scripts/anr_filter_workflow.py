#!/usr/bin/env python3
"""Standalone source filtering workflow entrypoint."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from anr_evidence import ArchiveLoadError, FilterWorkflowOptions, load_package_from_path, run_filter_workflow
from anr_evidence.extractor import collect_anchor_candidates, resolve_anchor


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the trace -> EventLog -> logcat ANR filtering workflow.")
    parser.add_argument("input", help="Package JSON, archive, or directory")
    parser.add_argument("-o", "--output", help="Write output JSON to a file instead of stdout")
    parser.add_argument("--package", dest="package_name", help="Optional package name filter")
    parser.add_argument("--process", dest="high_processes", action="append", default=[], help="High-load process name for meminfo follow-up; repeatable")
    parser.add_argument("--pid", dest="high_pids", action="append", type=int, default=[], help="High-load process pid for meminfo follow-up; repeatable")
    args = parser.parse_args()

    try:
        package = load_package_from_path(Path(args.input), package_name=args.package_name)
    except ArchiveLoadError as exc:
        raise SystemExit(str(exc)) from exc
    anchors = resolve_anchor(collect_anchor_candidates(package))
    result = run_filter_workflow(
        package,
        anchors,
        FilterWorkflowOptions(
            package_name=args.package_name,
            high_load_processes=tuple(args.high_processes),
            high_load_pids=tuple(args.high_pids),
        ),
    )
    rendered_payload: dict[str, Any] = {
        "anchors": anchors,
        "evidence": result.evidence,
        "warnings": result.warnings,
        "sources": {
            source_kind: {
                "warnings": source_result.warnings,
                "lineCount": len(source_result.lines),
                "metadata": source_result.metadata,
            }
            for source_kind, source_result in result.source_results.items()
        },
    }
    rendered = json.dumps(rendered_payload, indent=2, ensure_ascii=False, default=str)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
