#!/usr/bin/env python3
"""Reproducible Input/WMS/SF digest overhead benchmark.

The default comparison generates a roughly 13 MiB, six-ANR in-memory package.
Each measurement runs in a fresh child process so maximum RSS is comparable.
The baseline child disables only the new digest integration hook; extraction,
filtering, artifact rendering, and all baseline outputs remain unchanged.
No generated fixture or artifact is committed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import tempfile
import time
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_SIZE_MIB = 13.0
DEFAULT_GROUPS = 6
DEFAULT_REPEATS = 5
WALL_REGRESSION_LIMIT_PCT = 15.0
RSS_REGRESSION_LIMIT_PCT = 10.0


def _timestamp(minute: int, second: int, millis: int = 0) -> str:
    return f"04-12 10:{minute:02d}:{second:02d}.{millis:03d}"


def _build_package(size_mib: float, group_count: int) -> dict[str, Any]:
    if size_mib <= 0:
        raise ValueError("size_mib must be positive")
    if group_count < 1 or group_count > 50:
        raise ValueError("group_count must be between 1 and 50")

    anchors = [
        f"{_timestamp(index, 5)} am_anr ANR in com.demo: Input dispatching timed out group={index}"
        for index in range(group_count)
    ]
    signal_lines: list[str] = []
    trace_lines: list[str] = []
    for index in range(group_count):
        signal_lines.extend([
            f"{_timestamp(index, 4, 900)} E InputDispatcher: Input dispatching timed out for com.demo group={index}",
            f"{_timestamp(index, 4, 950)} W WindowManager: mCurrentFocus=null for com.demo group={index}",
            (
                f"{_timestamp(index, 5, 50)} E SurfaceFlinger: fence blocked; "
                "waiting because the touched window has not finished processing "
                f"for com.demo group={index}"
            ),
        ])
        trace_lines.extend([
            f"{_timestamp(index, 5, 100)} ----- pid 100 -----",
            "Cmd line: com.demo",
            '"main" prio=5 tid=1 Native',
            "  native: #00 pc 0 /system/lib/libc.so (__epoll_pwait+8)",
        ])

    target_bytes = max(1, int(size_mib * 1024 * 1024))
    noise_template = (
        "04-12 09:30:00.000  1000  1000 I NoiseTag: deterministic non-matching payload "
        "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    )
    base = "\n".join(signal_lines)
    noise_count = max(0, (target_bytes - len(base.encode("utf-8"))) // (len(noise_template) + 1))
    logcat = "\n".join([base, *([noise_template] * noise_count)])
    while len(logcat.encode("utf-8")) < target_bytes:
        logcat += "\n" + noise_template

    return {
        "package_id": "DIGEST-BENCHMARK",
        "sources": {
            "event_log": {"path": "events.log", "content": "\n".join(anchors)},
            "trace": {"path": "traces.txt", "content": "\n".join(trace_lines)},
            "logcat": {"path": "logcat.txt", "content": logcat},
        },
    }


def _max_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes; Linux and most BSD CI environments report KiB.
    return int(value if sys.platform == "darwin" else value * 1024)


def _run_child(mode: str, size_mib: float, group_count: int) -> dict[str, Any]:
    import anr_evidence.ai_context as ai_context

    if mode == "baseline":
        ai_context._build_input_window_digest_for_group = lambda *_args, **_kwargs: None

    package = _build_package(size_mib, group_count)
    source_bytes = len(package["sources"]["logcat"]["content"].encode("utf-8"))
    with tempfile.TemporaryDirectory(prefix=f"anr-digest-{mode}-") as tmpdir:
        started = time.perf_counter()
        index = ai_context.build_ai_context_artifacts(
            package,
            ai_context.AiContextOptions(
                out_dir=tmpdir,
                package_name="com.demo",
                anr_type="input_dispatching_timeout",
            ),
        )
        wall_seconds = time.perf_counter() - started
        group_dirs = [Path(tmpdir, group["id"]) for group in index["groups"]]
        artifacts_ok = (
            Path(tmpdir, "index.json").is_file()
            and len(group_dirs) == group_count
            and all((directory / "anr_analysis.md").is_file() for directory in group_dirs)
            and all((directory / "logcat.txt").is_file() for directory in group_dirs)
        )
        digest_event_count = sum(
            event.get("step") == "input_window_digest_built"
            for event in index["events"]
        )

    return {
        "mode": mode,
        "sourceBytes": source_bytes,
        "groupCount": index["groupCount"],
        "artifactChecksPassed": artifacts_ok,
        "digestEventCount": digest_event_count,
        "wallSeconds": wall_seconds,
        "maxRssBytes": _max_rss_bytes(),
    }


def _child_command(mode: str, args: argparse.Namespace) -> list[str]:
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "--child-mode",
        mode,
        "--size-mib",
        str(args.size_mib),
        "--groups",
        str(args.groups),
    ]


def _measure(mode: str, args: argparse.Namespace) -> dict[str, Any]:
    completed = subprocess.run(
        _child_command(mode, args),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _regression_pct(current: float, baseline: float) -> float:
    if baseline <= 0:
        return 0.0
    return (current - baseline) / baseline * 100.0


def _compare(args: argparse.Namespace) -> dict[str, Any]:
    for _ in range(args.warmups):
        _measure("baseline", args)
        _measure("digest", args)

    samples = {"baseline": [], "digest": []}
    for _ in range(args.repeats):
        for mode in ("baseline", "digest"):
            samples[mode].append(_measure(mode, args))

    baseline_wall = statistics.median(sample["wallSeconds"] for sample in samples["baseline"])
    digest_wall = statistics.median(sample["wallSeconds"] for sample in samples["digest"])
    baseline_rss = statistics.median(sample["maxRssBytes"] for sample in samples["baseline"])
    digest_rss = statistics.median(sample["maxRssBytes"] for sample in samples["digest"])
    wall_regression = _regression_pct(digest_wall, baseline_wall)
    rss_regression = _regression_pct(digest_rss, baseline_rss)
    artifacts_ok = all(
        sample["artifactChecksPassed"]
        for mode_samples in samples.values()
        for sample in mode_samples
    )
    digest_events_ok = all(
        sample["digestEventCount"] == args.groups
        for sample in samples["digest"]
    )
    baseline_events_ok = all(
        sample["digestEventCount"] == 0
        for sample in samples["baseline"]
    )

    return {
        "configuration": {
            "sizeMiB": args.size_mib,
            "groupCount": args.groups,
            "warmups": args.warmups,
            "repeats": args.repeats,
            "wallRegressionLimitPct": WALL_REGRESSION_LIMIT_PCT,
            "rssRegressionLimitPct": RSS_REGRESSION_LIMIT_PCT,
        },
        "baseline": {
            "medianWallSeconds": baseline_wall,
            "medianMaxRssBytes": baseline_rss,
            "samples": samples["baseline"],
        },
        "digest": {
            "medianWallSeconds": digest_wall,
            "medianMaxRssBytes": digest_rss,
            "samples": samples["digest"],
        },
        "comparison": {
            "wallRegressionPct": wall_regression,
            "rssRegressionPct": rss_regression,
            "artifactsPassed": artifacts_ok,
            "eventChecksPassed": digest_events_ok and baseline_events_ok,
            "wallPassed": wall_regression <= WALL_REGRESSION_LIMIT_PCT,
            "rssPassed": rss_regression <= RSS_REGRESSION_LIMIT_PCT,
            "passed": (
                artifacts_ok
                and digest_events_ok
                and baseline_events_ok
                and wall_regression <= WALL_REGRESSION_LIMIT_PCT
                and rss_regression <= RSS_REGRESSION_LIMIT_PCT
            ),
        },
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size-mib", type=float, default=DEFAULT_SIZE_MIB)
    parser.add_argument("--groups", type=int, default=DEFAULT_GROUPS)
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--child-mode", choices=("baseline", "digest"))
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.repeats < 1:
        raise ValueError("repeats must be at least 1")
    if args.warmups < 0:
        raise ValueError("warmups must be non-negative")
    result = (
        _run_child(args.child_mode, args.size_mib, args.groups)
        if args.child_mode
        else _compare(args)
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.child_mode:
        return 0
    return 0 if result["comparison"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
