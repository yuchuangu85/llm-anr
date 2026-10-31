#!/usr/bin/env python3
"""Refresh standalone reconstruction documents from an existing ANR workspace."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from anr_evidence.reconstruction import refresh_reconstruction


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="Context root, ANR group directory, or anr_analysis.md")
    parser.add_argument("--ai-report", type=Path, help="Optional AI JSON; candidates never become observed events")
    args = parser.parse_args()
    try:
        ai_report = json.loads(args.ai_report.read_text(encoding="utf-8")) if args.ai_report else None
        if ai_report is not None and not isinstance(ai_report, dict):
            raise ValueError("AI report must be a JSON object.")
        result = refresh_reconstruction(args.input, ai_report=ai_report)
    except (OSError, ValueError, TypeError) as exc:
        print(f"Reconstruction failed: {exc}", file=sys.stderr)
        return 1
    for group in result["groups"]:
        print(group["artifactPaths"]["reconstruction"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
