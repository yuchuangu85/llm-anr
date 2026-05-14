#!/usr/bin/env python3
"""Standalone ANR trace preprocessor entrypoint."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from anr_evidence import (
    ArchiveLoadError,
    load_package_from_archive,
    load_package_from_directory,
    load_package_from_fixture,
    preprocess_trace_content,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Preprocess trace.txt content into deterministic structured metadata.")
    parser.add_argument("input", help="Trace text file, fixture/raw package JSON, phase package JSON, raw directory, or archive")
    parser.add_argument("-o", "--output", help="Write output JSON to a file instead of stdout")
    args = parser.parse_args()

    result = preprocess_input(Path(args.input))
    rendered = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


def preprocess_input(input_path: Path) -> dict[str, Any]:
    if input_path.is_dir():
        payload = load_package_from_directory(input_path)
        return _preprocess_payload(payload, input_path, origin="raw-directory")
    if _is_archive_path(input_path):
        try:
            payload = load_package_from_archive(input_path)
        except ArchiveLoadError as exc:
            raise SystemExit(str(exc)) from exc
        return _preprocess_payload(payload, input_path, origin="raw-archive")
    if input_path.suffix.lower() == ".json":
        payload = load_package_from_fixture(input_path)
        return _preprocess_payload(payload, input_path, origin="json-payload")
    content = input_path.read_text(encoding="utf-8", errors="replace")
    return _build_output(
        input_path,
        source_origin="trace-text",
        source_path=str(input_path),
        content=content,
        anchor_timestamp=None,
    )


def _preprocess_payload(payload: dict[str, Any], input_path: Path, *, origin: str) -> dict[str, Any]:
    metadata = payload.get("metadata", {}) if isinstance(payload, dict) else {}
    phase = metadata.get("phase")
    if phase == "phase1-evidence-extraction-mvp":
        trace_evidence = next((item for item in payload.get("evidence", []) if item.get("sourceKind") == "trace"), None)
        if not trace_evidence:
            raise SystemExit("No trace evidence found in Phase 1 package.")
        anchor_timestamp = None
        anchor_used = trace_evidence.get("provenance", {}).get("anchorUsed")
        if isinstance(anchor_used, dict):
            anchor_timestamp = anchor_used.get("timestamp")
        return _build_output(
            input_path,
            source_origin="phase1-evidence",
            source_path=trace_evidence.get("provenance", {}).get("sourcePath", "trace"),
            content=trace_evidence.get("content", ""),
            anchor_timestamp=anchor_timestamp,
        )
    if phase == "phase2-evidence-normalization":
        trace_record = next((item for item in payload.get("normalizedRecords", []) if item.get("sourceKind") == "trace"), None)
        if not trace_record:
            raise SystemExit("No trace record found in Phase 2 package.")
        anchor_ref = trace_record.get("anchorRef") or {}
        return _build_output(
            input_path,
            source_origin="phase2-normalized",
            source_path=trace_record.get("sourcePath", "trace"),
            content=trace_record.get("rawSnippet", ""),
            anchor_timestamp=anchor_ref.get("timestamp"),
        )

    raw_trace = payload.get("sources", {}).get("trace") if isinstance(payload, dict) else None
    if not raw_trace:
        raise SystemExit(f"No trace source found in payload loaded from `{input_path}`.")
    return _build_output(
        input_path,
        source_origin=origin,
        source_path=raw_trace.get("path", "trace"),
        content=raw_trace.get("content", ""),
        anchor_timestamp=None,
    )


def _build_output(
    input_path: Path,
    *,
    source_origin: str,
    source_path: str,
    content: str,
    anchor_timestamp: str | None,
) -> dict[str, Any]:
    preprocessed = preprocess_trace_content(content, anchor_timestamp=anchor_timestamp)
    return {
        "metadata": {
            "phase": "trace-preprocess-v1",
            "inputPath": str(input_path),
            "sourceOrigin": source_origin,
            "traceSourcePath": source_path,
            "anchorTimestamp": anchor_timestamp,
        },
        "trace": preprocessed,
    }


def _is_archive_path(path: Path) -> bool:
    suffixes = [suffix.lower() for suffix in path.suffixes]
    if not suffixes:
        return False
    if suffixes[-1] == ".zip":
        return True
    return any(suffix in {".tar", ".gz", ".tgz", ".bz2", ".xz"} for suffix in suffixes)


if __name__ == "__main__":
    raise SystemExit(main())
