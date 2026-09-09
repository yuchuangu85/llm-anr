#!/usr/bin/env python3
"""Independent C2 benchmark for the *default* directory/ZIP/TAR context route.

Each measurement runs in a new interpreter and creates a real raw input.  The
child calls ``build_ai_context_artifacts_from_input_path`` -- the same single
entrypoint used by ``anr_to_ai.py`` and ``python -m anr_evidence
--build-ai-context`` for directory, ZIP, and TAR inputs.  It intentionally does
not call the explicit ``build_ai_context_artifacts_from_store`` API.
"""
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
import tarfile
import tempfile
import time
from typing import Any, Callable, Sequence
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from anr_evidence import AiContextOptions, build_ai_context_artifacts_from_input_path

MIB = 1024 * 1024
GIB = 1024 * MIB
RSS_LIMIT_1_GIB = 512 * MIB
STORE_DISK_MULTIPLIER = 1.5
WORKING_TREE_DISK_MULTIPLIER = 3.0


def _max_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes; Linux and common BSD CI report KiB.
    return int(value if sys.platform == "darwin" else value * 1024)


def _tree_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _git_revision() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        capture_output=True, check=True,
    )
    return completed.stdout.strip()


def _event_bytes(group_count: int) -> bytes:
    return b"".join(
        f"06-09 13:{12 + index:02d}:45.000 am_anr ANR in com.benchmark: Input dispatching timed out\n".encode()
        for index in range(group_count)
    )


def _write_logcat_shards(
    root: Path,
    *,
    total_logcat_bytes: int,
    group_count: int,
) -> list[Path]:
    """Write bounded logcat shards so a 1 GiB directory obeys input policy."""
    prefix = b"".join(
        (
            f"06-09 13:{12 + index:02d}:44.900 I InputDispatcher: retained pre-anchor {index}\n"
            f"06-09 13:{12 + index:02d}:45.000 E InputDispatcher: Input dispatching timed out for com.benchmark\n"
        ).encode()
        for index in range(group_count)
    )
    noise_prefix = b"06-09 01:00:00.000 I benchmark: non-window store-only payload "
    noise = noise_prefix + b"x" * (4095 - len(noise_prefix)) + b"\n"
    max_shard_bytes = 128 * MIB
    if len(prefix) > total_logcat_bytes:
        raise ValueError("Benchmark anchors exceed requested input size")

    paths: list[Path] = []
    remaining = total_logcat_bytes
    index = 0
    while remaining:
        path = root / f"logcat-{index:03d}.txt"
        shard_budget = min(max_shard_bytes, remaining)
        with path.open("wb") as handle:
            if index == 0:
                handle.write(prefix)
                shard_budget -= len(prefix)
                remaining -= len(prefix)
            while shard_budget:
                chunk = noise[:min(len(noise), shard_budget)]
                handle.write(chunk)
                shard_budget -= len(chunk)
                remaining -= len(chunk)
        paths.append(path)
        index += 1
    return paths


def _sha256_files(paths: Sequence[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(MIB), b""):
                digest.update(block)
    return digest.hexdigest()


def _write_directory(root: Path, *, size_bytes: int, group_count: int) -> tuple[Path, int, str]:
    root.mkdir(parents=True, exist_ok=True)
    events = root / "events.txt"
    event_bytes = _event_bytes(group_count)
    events.write_bytes(event_bytes)
    logcat_paths = _write_logcat_shards(
        root,
        total_logcat_bytes=max(0, size_bytes - len(event_bytes)),
        group_count=group_count,
    )
    return root, _tree_bytes(root), _sha256_files((events, *logcat_paths))


def _write_archive(
    root: Path,
    *,
    size_bytes: int,
    group_count: int,
    archive_kind: str,
) -> tuple[Path, int, str]:
    root.mkdir(parents=True, exist_ok=True)
    source = root / "source"
    input_dir, _source_bytes, digest = _write_directory(
        source, size_bytes=size_bytes, group_count=group_count,
    )
    suffix = ".zip" if archive_kind == "zip" else ".tar"
    archive_path = root / f"bugreport{suffix}"
    if archive_kind == "zip":
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.write(input_dir / "events.txt", arcname="events/events.txt")
            for logcat_path in sorted(input_dir.glob("logcat-*.txt")):
                archive.write(logcat_path, arcname=f"logs/{logcat_path.name}")
    else:
        with tarfile.open(archive_path, "w") as archive:
            archive.add(input_dir / "events.txt", arcname="events/events.txt")
            for logcat_path in sorted(input_dir.glob("logcat-*.txt")):
                archive.add(logcat_path, arcname=f"logs/{logcat_path.name}")
    shutil.rmtree(source)
    return archive_path, archive_path.stat().st_size, digest


def _write_input(root: Path, *, size_mib: int, group_count: int, input_kind: str) -> tuple[Path, int, str]:
    size_bytes = size_mib * MIB
    if input_kind == "directory":
        return _write_directory(root / "input", size_bytes=size_bytes, group_count=group_count)
    return _write_archive(root / "input", size_bytes=size_bytes, group_count=group_count, archive_kind=input_kind)


def _child(size_mib: int, group_count: int, run_kind: str, input_kind: str) -> dict[str, Any]:
    root = Path(tempfile.mkdtemp(prefix="llm-anr-c2-default-routing-"))
    try:
        input_path, input_disk_bytes, input_sha256 = _write_input(
            root, size_mib=size_mib, group_count=group_count, input_kind=input_kind,
        )
        out_dir = root / "anr_ai_context"
        started = time.monotonic()
        index = build_ai_context_artifacts_from_input_path(
            input_path,
            AiContextOptions(
                out_dir=out_dir,
                package_name="com.benchmark",
                anr_type="input_dispatching_timeout",
            ),
        )
        wall_seconds = time.monotonic() - started
        store_path = Path(index["artifactPaths"]["evidenceStore"])
        routing = index.get("inputRouting", {})
        group_dirs = [out_dir / group["id"] for group in index["groups"]]
        compact_caps_ok = all(
            len((directory / "compact_evidence.jsonl").read_bytes()) <= 512 * 1024
            for directory in group_dirs
        )
        return {
            "runKind": run_kind,
            "inputKind": input_kind,
            "sizeMiB": size_mib,
            "groupCountRequested": group_count,
            "groupCountExported": index["groupCount"],
            "inputDiskBytes": input_disk_bytes,
            "capturedInputBytes": routing.get("capturedBytes"),
            "inputSha256": input_sha256,
            "storeBytes": sum(path.stat().st_size for path in store_path.parent.glob(f"{store_path.name}*") if path.is_file()),
            "outputBytes": _tree_bytes(out_dir),
            "workingTreeBytes": _tree_bytes(root),
            "wallSeconds": wall_seconds,
            "maxRssBytes": _max_rss_bytes(),
            "routingMode": routing.get("mode"),
            "legacyPackageMaterialized": routing.get("legacyPackageMaterialized"),
            "indexExists": (out_dir / "index.json").is_file(),
            "allGroupArtifactsExist": all(
                (directory / "anr_analysis.md").is_file()
                and (directory / "logcat.txt").is_file()
                and (directory / "evidence_refs.json").is_file()
                for directory in group_dirs
            ),
            "completeFilteredBaselineExists": all(
                (directory / "logcat.txt").stat().st_size > 0 for directory in group_dirs
            ),
            "compactViewCapsPassed": compact_caps_ok,
        }
    finally:
        shutil.rmtree(root)


def _run_child(size_mib: int, group_count: int, run_kind: str, input_kind: str) -> dict[str, Any]:
    completed = subprocess.run(
        [
            sys.executable, str(Path(__file__).resolve()), "--child",
            "--size-mib", str(size_mib), "--groups", str(group_count),
            "--run-kind", run_kind, "--input-kind", input_kind,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr or completed.stdout)
    return json.loads(completed.stdout)


def _within_disk_budget(value: int, input_bytes: int, multiplier: float) -> bool:
    return value <= int(input_bytes * multiplier)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size-mib", type=int, required=True)
    parser.add_argument("--groups", type=int, default=2)
    parser.add_argument("--input-kind", choices=("directory", "zip", "tar"), default="directory")
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
        print(json.dumps(_child(args.size_mib, args.groups, args.run_kind, args.input_kind), sort_keys=True))
        return 0

    runs = (
        [_run_child(args.size_mib, args.groups, "cold", args.input_kind) for _ in range(args.cold_runs)]
        + [_run_child(args.size_mib, args.groups, "warm", args.input_kind) for _ in range(args.warm_runs)]
    )
    routing_ok = all(
        run["routingMode"] == "store_backed_default"
        and run["legacyPackageMaterialized"] is False
        for run in runs
    )
    artifact_ok = all(
        run["indexExists"]
        and run["allGroupArtifactsExist"]
        and run["completeFilteredBaselineExists"]
        and run["compactViewCapsPassed"]
        and run["groupCountExported"] == args.groups
        for run in runs
    )
    disk_ok = all(
        _within_disk_budget(run["storeBytes"], run["capturedInputBytes"], STORE_DISK_MULTIPLIER)
        and _within_disk_budget(run["workingTreeBytes"], run["capturedInputBytes"], WORKING_TREE_DISK_MULTIPLIER)
        for run in runs
    )
    rss_ok = all(run["maxRssBytes"] <= RSS_LIMIT_1_GIB for run in runs) if args.size_mib >= 1024 else True
    report = {
        "benchmark": "c2-default-routing-context",
        "revision": _git_revision(),
        "python": sys.version,
        "machine": {"platform": platform.platform(), "machine": platform.machine()},
        "configuration": {
            "sizeMiB": args.size_mib,
            "groupCount": args.groups,
            "inputKind": args.input_kind,
            "coldRuns": args.cold_runs,
            "warmRuns": args.warm_runs,
        },
        "limits": {
            "oneGiBMaxRssBytes": RSS_LIMIT_1_GIB,
            "storeMaxInputMultiplier": STORE_DISK_MULTIPLIER,
            "workingTreeMaxInputMultiplier": WORKING_TREE_DISK_MULTIPLIER,
        },
        "runs": runs,
        "checks": {
            "defaultRouting": routing_ok,
            "artifactsAndCompleteFilteredBaseline": artifact_ok,
            "disk": disk_ok,
            "rss": rss_ok,
        },
        "passed": routing_ok and artifact_ok and disk_ok and rss_ok,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
