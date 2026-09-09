#!/usr/bin/env python3
"""Real-disk C2 benchmark: stream source bytes into SQLite, then export store-backed context."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from anr_evidence.ai_context import AiContextOptions, build_ai_context_artifacts_from_store
from anr_evidence.evidence_store import EvidenceStore

MIB = 1024 * 1024
GIB = 1024 * MIB
RSS_LIMIT_1_GIB = 512 * MIB


def _max_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == "darwin" else value * 1024


def _tree_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _git_revision() -> str:
    completed = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True)
    return completed.stdout.strip()


def _write_input(root: Path, size_bytes: int, group_count: int) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    event_path = root / "events.txt"
    anchors = [f"06-09 13:{12 + index:02d}:45.000 am_anr ANR in com.benchmark\n" for index in range(group_count)]
    event_path.write_text("".join(anchors), encoding="utf-8")
    logcat_path = root / "logcat.txt"
    prefix = b""
    for index in range(group_count):
        minute = 12 + index
        prefix += (
            f"06-09 13:{minute:02d}:44.900 I InputDispatcher: pre-anchor {index}\n"
            f"06-09 13:{minute:02d}:45.000 E InputDispatcher: Application does not have a focused window\n"
        ).encode()
    noise_prefix = b"06-09 01:00:00.000 I benchmark: retained only in store; non-window payload "
    # Keep a 1 GiB fixture below the production 1,000,000 line-index limit
    # while still forcing all source bytes through the SQLite chunk/line path.
    noise = noise_prefix + (b"x" * (4095 - len(noise_prefix))) + b"\n"
    with logcat_path.open("wb") as handle:
        handle.write(prefix)
        remaining = max(0, size_bytes - len(prefix) - event_path.stat().st_size)
        while remaining:
            chunk = noise[:min(len(noise), remaining)]
            handle.write(chunk)
            remaining -= len(chunk)
    digest = hashlib.sha256()
    for path in (event_path, logcat_path):
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    return {"eventPath": event_path, "logcatPath": logcat_path, "inputSha256": digest.hexdigest(), "inputBytes": _tree_bytes(root)}


def _child(size_mib: int, group_count: int, run_kind: str) -> dict[str, Any]:
    root = Path(tempfile.mkdtemp(prefix="llm-anr-c2-benchmark-"))
    try:
        input_info = _write_input(root / "input", size_mib * MIB, group_count)
        store_path = root / "evidence.sqlite3"
        start = time.monotonic()
        with EvidenceStore(store_path) as store:
            with input_info["eventPath"].open("rb") as events, input_info["logcatPath"].open("rb") as logcat:
                store.add_streamed_group("capture", [
                    {"reader": events, "source_kind": "event_log", "logical_path": "events.txt", "origin": {"type": "file", "path": str(input_info["eventPath"])}},
                    {"reader": logcat, "source_kind": "logcat", "logical_path": "logcat.txt", "origin": {"type": "file", "path": str(input_info["logcatPath"])}},
                ], shared_byte_budget=input_info["inputBytes"], chunk_size=64 * 1024)
        index = build_ai_context_artifacts_from_store(
            store_path,
            source_group_id="capture",
            package_id="com.benchmark",
            options=AiContextOptions(
                out_dir=root / "anr_ai_context", package_name="com.benchmark",
                anr_type="input_dispatching_timeout",
            ),
            max_stream_source_bytes=size_mib * MIB,
            max_stream_source_lines=1_000_000,
        )
        wall = time.monotonic() - start
        output_dir = root / "anr_ai_context"
        return {
            "runKind": run_kind,
            "sizeMiB": size_mib,
            "groupCountRequested": group_count,
            "groupCountExported": index["groupCount"],
            "inputBytes": input_info["inputBytes"],
            "inputSha256": input_info["inputSha256"],
            "storeBytes": sum(path.stat().st_size for path in root.glob("evidence.sqlite3*") if path.is_file()),
            "outputBytes": _tree_bytes(output_dir),
            "workingTreeBytes": _tree_bytes(root),
            "wallSeconds": wall,
            "maxRssBytes": _max_rss_bytes(),
            "indexExists": (output_dir / "index.json").is_file(),
            "allGroupArtifactsExist": all((output_dir / group["id"] / "anr_analysis.md").is_file() and (output_dir / group["id"] / "logcat.txt").is_file() for group in index["groups"]),
        }
    finally:
        shutil.rmtree(root)


def _run_child(size_mib: int, group_count: int, run_kind: str) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--child", "--size-mib", str(size_mib), "--groups", str(group_count), "--run-kind", run_kind],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr or completed.stdout)
    return json.loads(completed.stdout)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size-mib", type=int, required=True)
    parser.add_argument("--groups", type=int, default=2)
    parser.add_argument("--cold-runs", type=int, default=1)
    parser.add_argument("--warm-runs", type=int, default=1)
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--run-kind", default="cold")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.size_mib <= 0 or args.groups <= 0 or args.cold_runs < 0 or args.warm_runs < 0:
        raise SystemExit("size/groups must be positive and run counts non-negative")
    if args.child:
        print(json.dumps(_child(args.size_mib, args.groups, args.run_kind), sort_keys=True))
        return 0
    runs = ([_run_child(args.size_mib, args.groups, "cold") for _ in range(args.cold_runs)] +
            [_run_child(args.size_mib, args.groups, "warm") for _ in range(args.warm_runs)])
    passed = all(run["indexExists"] and run["allGroupArtifactsExist"] and run["groupCountExported"] == args.groups for run in runs)
    if args.size_mib >= 1024:
        passed = passed and all(run["maxRssBytes"] <= RSS_LIMIT_1_GIB for run in runs)
    report = {
        "benchmark": "c2-store-backed-context",
        "revision": _git_revision(),
        "python": sys.version,
        "machine": {"platform": platform.platform(), "machine": platform.machine()},
        "configuration": {"sizeMiB": args.size_mib, "groupCount": args.groups, "coldRuns": args.cold_runs, "warmRuns": args.warm_runs},
        "limits": {"oneGiBMaxRssBytes": RSS_LIMIT_1_GIB},
        "runs": runs,
        "passed": passed,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
