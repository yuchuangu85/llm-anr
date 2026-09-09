#!/usr/bin/env python3
"""Capture byte-level provenance from official Android Gitiles without manifests."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from dataclasses import asdict
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from anr_evidence.aosp_contract.gitiles import GitilesProvenanceError, fetch_source_record


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-tag", required=True, help="Gitiles release tag, e.g. android-15.0.0_r1")
    parser.add_argument(
        "--source",
        action="append",
        nargs=4,
        metavar=("NAME", "REPOSITORY", "PATH", "SYMBOL"),
        required=True,
        help="Repeat for each independently verified source file; NAME labels output only.",
    )
    parser.add_argument("--timeout", type=float, default=20.0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    sources: dict[str, dict[str, object]] = {}
    try:
        for name, repository, path, symbol in args.source:
            if name in sources:
                raise GitilesProvenanceError(f"duplicate source name: {name}")
            sources[name] = asdict(fetch_source_record(
                release_tag=args.release_tag,
                repository=repository,
                path=path,
                symbol=symbol,
                timeout=args.timeout,
            ))
    except GitilesProvenanceError as error:
        print(f"provenance capture failed: {error}")
        return 2
    print(json.dumps({"releaseTag": args.release_tag, "sources": sources}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
