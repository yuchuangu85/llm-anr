#!/usr/bin/env python3
"""Run P1 capability inspection or one fixed local Perfetto scheduling query."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from anr_evidence.performance_adapters import (  # noqa: E402
    probe_performance_capabilities,
    run_perfetto_scheduling_query,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="Explicit performance-tool-config-v1 JSON file.")
    parser.add_argument("--trace", type=Path, help="Absolute local trace path for the sole fixed scheduling query.")
    parser.add_argument("--target-pid", type=int, help="Positive process PID required together with --trace.")
    args = parser.parse_args(argv)
    if (args.trace is None) != (args.target_pid is None):
        parser.error("--trace and --target-pid must be provided together")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        print(f"performance adapter config unreadable: {error}", file=sys.stderr)
        return 2
    report = (
        run_perfetto_scheduling_query(args.trace, config, target_pid=args.target_pid)
        if args.trace is not None
        else probe_performance_capabilities(config)
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
