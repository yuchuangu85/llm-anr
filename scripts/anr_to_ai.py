#!/usr/bin/env python3
"""Process an ANR bugreport and produce AI-ready context for LLM analysis.

Single entrypoint for Claude Code / Codex CLI agents.  Takes any bugreport
format (directory, ZIP, TAR, fixture JSON) and writes ``anr_ai_context/`` next
to the input by default, containing ``index.json`` and per-ANR
``anr_analysis.md`` files (AI
instructions + filtered evidence + inline analysis slots).

Usage::

    python3 scripts/anr_to_ai.py tests/fixtures/nfw_01.json
    python3 scripts/anr_to_ai.py bugreport.zip --package com.android.launcher
    python3 scripts/anr_to_ai.py /path/to/bugreport_dir --anr-type input_dispatching_timeout

After running, the agent should read
``<input-dir>/anr_ai_context/<anr-id>/anr_analysis.md`` (or the explicit
``--out-dir``) and analyze the evidence to produce a root-cause report.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from anr_evidence import (
    AiContextOptions,
    build_ai_context,
    build_ai_context_artifacts,
    load_package_from_path,
)
from anr_evidence.extractor import ArchiveLoadError


def _default_out_dir(input_path: Path) -> Path:
    """Place generated context beside the input logs by default."""

    return (input_path if input_path.is_dir() else input_path.parent) / "anr_ai_context"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Process an ANR bugreport into AI-ready context (anr_analysis.md per group + index.json).",
    )
    parser.add_argument(
        "input",
        help=(
            "Bugreport path.  Accepted forms: "
            "a directory (may contain bugreport.zip — auto-extracted), "
            "an archive (.zip/.tar/.tar.gz/.tgz/.tar.bz2/.tar.xz), "
            "or a fixture JSON."
        ),
    )
    parser.add_argument("--package", dest="package_name", help="Target package name filter")
    parser.add_argument("--anr-type", help="Force ANR type (no_focus_window | input_dispatching_timeout)")
    parser.add_argument("--event-before", type=int, help="EventLog seconds before ANR anchor")
    parser.add_argument("--logcat-before", type=int, help="Logcat seconds before ANR anchor")
    parser.add_argument("--logcat-after", type=int, help="Logcat seconds after ANR anchor")
    parser.add_argument("--meminfo-before", type=int, default=5, help="Meminfo snapshot window seconds before ANR anchor (default: 5)")
    parser.add_argument("--meminfo-after", type=int, default=5, help="Meminfo snapshot window seconds after ANR anchor (default: 5)")
    parser.add_argument(
        "--out-dir",
        help="Output directory (default: anr_ai_context under the input log directory)",
    )
    parser.add_argument("--print-summary", action="store_true", help="Print summary JSON to stdout after building")
    args = parser.parse_args()

    input_path = Path(args.input)

    # Load the package — auto-detects nested archives
    try:
        package = load_package_from_path(input_path, package_name=args.package_name)
    except ArchiveLoadError as exc:
        print(f"Error loading input: {exc}", file=sys.stderr)
        return 1

    if not package.get("sources"):
        print("Error: no recognizable log sources found in input.", file=sys.stderr)
        return 1

    # Keep generated context next to the input logs by default.
    # An explicit --out-dir always takes precedence.
    out_dir = Path(args.out_dir) if args.out_dir else _default_out_dir(input_path)

    # Build AI context
    options = AiContextOptions(
        out_dir=out_dir,
        event_before_seconds=args.event_before,
        logcat_before_seconds=args.logcat_before,
        logcat_after_seconds=args.logcat_after,
        meminfo_before_seconds=args.meminfo_before,
        meminfo_after_seconds=args.meminfo_after,
        package_name=args.package_name,
        anr_type=args.anr_type,
    )

    summary = build_ai_context_artifacts(package, options)

    print(f"[✓] {summary['groupCount']} ANR analysis file(s) written under {out_dir}/")
    print(f"    Analysis guide — read before analyzing: {REPO_ROOT / 'docs/anr-ai-analysis-guide.md'}")
    print("    index.json — context directory index")
    for group in summary.get("groups", []):
        analysis_path = group.get("artifactPaths", {}).get("analysis")
        print(f"    {group.get('id')}/anr_analysis.md — AI instructions + evidence + analysis slots: {analysis_path}")
    print()
    pending_groups = [
        (
            group.get("id"),
            [
                slot_id
                for slot_id, status in group.get("analysisSlots", {}).items()
                if status != "filled"
            ],
        )
        for group in summary.get("groups", [])
    ]
    pending_groups = [(group_id, slots) for group_id, slots in pending_groups if slots]
    if pending_groups:
        print("Status: AI conclusions are not generated by this extractor; analysis slots are pending.")
        for group_id, slots in pending_groups:
            print(f"    {group_id}: pending slots = {', '.join(slots)}")
        print(f"Next: fill each {out_dir}/<anr-id>/anr_analysis.md in order: Trace → EventLog → Logcat/AnrManager → Final ANR.")
        print("      Re-running this command preserves filled slots and updates only the extracted evidence.")
    else:
        print("Status: all AI analysis slots are already filled and were preserved during regeneration.")
        print(f"Next: review each {out_dir}/<anr-id>/anr_analysis.md final ANR section and deliver the report.")

    if args.print_summary:
        import json
        print()
        print(json.dumps(summary, indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
