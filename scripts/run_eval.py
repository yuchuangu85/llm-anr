#!/usr/bin/env python3
"""Run the ground-truth eval corpus and print a summary table.

Usage:
    python3 scripts/run_eval.py [path_to_eval_dir]

Default eval directory: tests/fixtures/eval

Exits non-zero if pass rate is below 1.0 (any case failed).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

# Make `anr_evidence` importable when run from a checkout.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anr_evidence import run_eval_directory  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "eval_dir",
        nargs="?",
        default=str(Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "eval"),
        help="Directory containing eval fixtures (*.json)",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of a table")
    args = parser.parse_args()

    aggregate = run_eval_directory(args.eval_dir)

    if args.json:
        print(json.dumps(aggregate.to_dict(), indent=2, ensure_ascii=False))
        return 0 if aggregate.pass_rate == 1.0 else 1

    print(f"Eval corpus: {args.eval_dir}")
    print(f"Cases: {aggregate.total}  passed={aggregate.passed}  failed={aggregate.failed}  pass_rate={aggregate.pass_rate}")
    print()
    print("Per-case results:")
    for case in aggregate.cases:
        status = "PASS" if case.passed else "FAIL"
        print(f"  [{status}] {case.case_id}: {case.description}")
        if not case.passed:
            for note in case.notes:
                print(f"         - {note}")
            print(f"         detected: {case.detected_hint_ids}")
    print()
    print("Per-hint metrics (precision = 1 - forbidden_triggers; recall = required_hits / required_cases):")
    print(f"  {'hint_id':<35} {'tp':>3} {'fp':>3} {'fn':>3} {'P':>6} {'R':>6} {'F1':>6}")
    for hid, stats in aggregate.per_hint_id.items():
        print(
            f"  {hid:<35} {stats['tp']:>3} {stats['fp']:>3} {stats['fn']:>3} "
            f"{stats['precision']:>6} {stats['recall']:>6} {stats['f1']:>6}"
        )
    return 0 if aggregate.pass_rate == 1.0 else 1


if __name__ == "__main__":
    sys.exit(main())
