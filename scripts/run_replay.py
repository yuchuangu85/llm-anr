#!/usr/bin/env python3
"""Run a replay manifest and archive the results under a timestamped session directory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from anr_evidence.replay import archive_replay_session


def main() -> int:
    parser = argparse.ArgumentParser(description="Run ANR replay manifest and archive outputs")
    parser.add_argument("manifest", help="Path to replay manifest JSON")
    parser.add_argument("--out-root", default="samples/replay/runs", help="Root directory for archived replay sessions")
    parser.add_argument("--label", help="Optional session label suffix")
    args = parser.parse_args()

    result = archive_replay_session(args.manifest, args.out_root, label=args.label)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
