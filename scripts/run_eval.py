#!/usr/bin/env python3
"""Run the hint regression eval or the fail-closed formal corpus gate.

Usage:
    python3 scripts/run_eval.py [path_to_eval_dir]
    python3 scripts/run_eval.py --formal [path_to_formal_eval_dir]

The default mode preserves the legacy hint regression suite. ``--formal``
checks mechanisms, immutable store-backed citations, pinned ontology provenance,
corpus identity, version coverage, and deterministic resource budgets. It exits
non-zero while the formal corpus is unqualified.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

# Make `anr_evidence` importable when run from a checkout.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anr_evidence import run_eval_directory, run_formal_eval_directory  # noqa: E402


_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_EVAL_DIR = _ROOT / "tests" / "fixtures" / "eval"
_DEFAULT_FORMAL_EVAL_DIR = _ROOT / "tests" / "fixtures" / "formal_eval"


def _print_hint_eval(aggregate, eval_dir: str | Path) -> None:
    print(f"Eval corpus: {eval_dir}")
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
    for hint_id, stats in aggregate.per_hint_id.items():
        print(
            f"  {hint_id:<35} {stats['tp']:>3} {stats['fp']:>3} {stats['fn']:>3} "
            f"{stats['precision']:>6} {stats['recall']:>6} {stats['f1']:>6}"
        )


def _print_formal_eval(aggregate, eval_dir: str | Path) -> None:
    print(f"Formal eval corpus: {eval_dir}")
    print(f"Corpus revision: {aggregate.corpus_revision or 'unknown'}")
    print(
        f"Cases: {aggregate.total}  passed={aggregate.passed}  failed={aggregate.failed}  "
        f"qualification={aggregate.qualification_status}"
    )
    print(f"Release coverage: {aggregate.release_coverage}")
    print("Per-release metrics:")
    for release, metrics in aggregate.per_release_metrics.items():
        print(f"  - Android {release}: {metrics}")
    print(f"Source classes: {aggregate.source_class_counts}")
    print(f"Deterministic resource totals: {aggregate.resource_totals}")
    print()
    print("Per-case results:")
    for case in aggregate.cases:
        status = "PASS" if case.passed else "FAIL"
        print(
            f"  [{status}] {case.case_id}: release={case.android_release} "
            f"mechanisms={case.detected_mechanism_ids} refs={case.verified_evidence_ref_count} "
            f"wall={case.wall_seconds:.6f}s"
        )
        for note in case.notes:
            print(f"         - {note}")
        for error in case.fixture_errors + case.budget_errors:
            if error not in case.notes:
                print(f"         - {error}")
    if aggregate.corpus_errors or aggregate.gate_failures:
        print()
        print("Formal gate blockers:")
        for error in aggregate.corpus_errors + aggregate.gate_failures:
            print(f"  - {error}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "eval_dir",
        nargs="?",
        default=None,
        help="Directory containing eval fixture JSON files",
    )
    parser.add_argument("--formal", action="store_true", help="Run formal-eval-v1 instead of legacy hint eval")
    parser.add_argument("--corpus-manifest", help="Optional formal corpus manifest path (only with --formal)")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of a table")
    args = parser.parse_args()

    if args.corpus_manifest and not args.formal:
        parser.error("--corpus-manifest requires --formal")
    if args.formal:
        eval_dir = args.eval_dir or str(_DEFAULT_FORMAL_EVAL_DIR)
        aggregate = run_formal_eval_directory(eval_dir, corpus_manifest_path=args.corpus_manifest)
        if args.json:
            print(json.dumps(aggregate.to_dict(), indent=2, ensure_ascii=False))
        else:
            _print_formal_eval(aggregate, eval_dir)
        return 0 if aggregate.qualified else 2

    eval_dir = args.eval_dir or str(_DEFAULT_EVAL_DIR)
    aggregate = run_eval_directory(eval_dir)
    if args.json:
        print(json.dumps(aggregate.to_dict(), indent=2, ensure_ascii=False))
        return 0 if aggregate.pass_rate == 1.0 else 1
    _print_hint_eval(aggregate, eval_dir)
    return 0 if aggregate.pass_rate == 1.0 else 1


if __name__ == "__main__":
    sys.exit(main())
