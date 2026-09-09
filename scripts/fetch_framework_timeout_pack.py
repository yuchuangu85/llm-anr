#!/usr/bin/env python3
"""Fetch and verify one Android 14--17 Framework timeout pack from Gitiles."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from anr_evidence.framework_timeout_lifecycle import (  # noqa: E402
    FrameworkTimeoutPackError,
    retrieve_framework_timeout_sources,
    verify_framework_timeout_pack,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--android-release", type=int, choices=(14, 15, 16, 17), required=True)
    parser.add_argument("--out", type=Path, help="Write the verified, content-free pack JSON atomically instead of stdout.")
    return parser.parse_args(argv)


def _write_json_atomically(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        recipe, sources = retrieve_framework_timeout_sources(args.android_release)
        pack = verify_framework_timeout_pack(recipe, sources)
    except FrameworkTimeoutPackError as error:
        print(f"framework timeout pack retrieval failed: {error}", file=sys.stderr)
        return 2

    if args.out:
        _write_json_atomically(args.out, pack)
        print(args.out)
    else:
        print(json.dumps(pack, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
