"""Ground-truth evaluation harness for ANR analysis hints.

Each eval fixture is a JSON file under ``tests/fixtures/eval/`` that
bundles a full evidence package together with structured expectations
(``requiredHintIds`` / ``optionalHintIds`` / ``forbiddenHintIds`` /
``primaryRootCauseHintId``). ``run_eval_case`` runs the full
``build_ai_context`` pipeline + the AnrManager parser, collects every
hint that surfaced anywhere (trace, deadlock, AnrManager-derived), and
scores the case as ``passed`` / ``failed`` with a per-case breakdown.

Aggregation across all cases produces precision / recall / F1 per
hint id so we can track regressions and validate that any newly added
pattern is actually moving the metric on real-world fixtures.

This is the regression baseline for any future hint expansion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Iterable

from .ai_context import AiContextOptions, build_ai_context
from .anrmanager_parser import parse_anrmanager_block


@dataclass
class EvalCaseResult:
    case_id: str
    description: str
    passed: bool
    detected_hint_ids: list[str]
    expected_required: list[str]
    expected_forbidden: list[str]
    missing_required: list[str]
    triggered_forbidden: list[str]
    primary_hit: bool | None
    primary_expected: str | None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "caseId": self.case_id,
            "description": self.description,
            "passed": self.passed,
            "detectedHintIds": self.detected_hint_ids,
            "expectedRequired": self.expected_required,
            "expectedForbidden": self.expected_forbidden,
            "missingRequired": self.missing_required,
            "triggeredForbidden": self.triggered_forbidden,
            "primaryExpected": self.primary_expected,
            "primaryHit": self.primary_hit,
            "notes": self.notes,
        }


@dataclass
class EvalAggregate:
    total: int
    passed: int
    failed: int
    pass_rate: float
    per_hint_id: dict[str, dict[str, float]]   # id -> {tp, fp, fn, precision, recall, f1}
    cases: list[EvalCaseResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "passRate": self.pass_rate,
            "perHintId": self.per_hint_id,
            "cases": [c.to_dict() for c in self.cases],
        }


def collect_all_hint_ids(ai_context_result: Any, package: dict[str, Any]) -> list[str]:
    """Gather every hint id surfaced by the pipeline for a single package.

    Includes trace hints (deadlock + native poll + future SP/IO/Binder),
    AnrManager-derived hints, and any package-level inferred ANR type.
    """

    ids: list[str] = []
    for group in ai_context_result.groups:
        trace = group.get("trace") or {}
        for hint in trace.get("traceHints") or []:
            ids.append(hint["id"])
        for hint in trace.get("deadlockHints") or []:
            if hint["id"] not in ids:
                ids.append(hint["id"])
        anr = group.get("anrManager") or {}
        # Re-parse to catch derived hints if summary not yet rendered.
        summary = anr.get("summary") or (
            parse_anrmanager_block(anr.get("lines", [])) if anr.get("lines") else None
        )
        if summary:
            for hint in summary.get("derivedHints") or []:
                ids.append(hint["id"])
    return ids


def run_eval_case(case_path: str | Path) -> EvalCaseResult:
    """Run a single eval fixture and score it against its `expected` block."""

    case_data = json.loads(Path(case_path).read_text(encoding="utf-8"))
    case_id = case_data.get("id", str(Path(case_path).stem))
    description = case_data.get("description", "")
    package = case_data["package"]
    expected = case_data.get("expected", {})
    required = list(expected.get("requiredHintIds", []) or [])
    forbidden = list(expected.get("forbiddenHintIds", []) or [])
    primary = expected.get("primaryRootCauseHintId")

    options = AiContextOptions(
        package_name=expected.get("packageNameFilter"),
    )
    result = build_ai_context(package, options)
    detected = collect_all_hint_ids(result, package)
    detected_set = set(detected)

    missing_required = [hid for hid in required if hid not in detected_set]
    triggered_forbidden = [hid for hid in forbidden if hid in detected_set]
    primary_hit = (primary in detected_set) if primary else None

    passed = (
        not missing_required
        and not triggered_forbidden
        and (primary_hit is None or primary_hit is True)
    )

    notes: list[str] = []
    if missing_required:
        notes.append(f"missing required: {missing_required}")
    if triggered_forbidden:
        notes.append(f"forbidden triggered: {triggered_forbidden}")
    if primary_hit is False:
        notes.append(f"primary hint not detected: {primary}")

    return EvalCaseResult(
        case_id=case_id,
        description=description,
        passed=passed,
        detected_hint_ids=detected,
        expected_required=required,
        expected_forbidden=forbidden,
        missing_required=missing_required,
        triggered_forbidden=triggered_forbidden,
        primary_hit=primary_hit,
        primary_expected=primary,
        notes=notes,
    )


def run_eval_directory(case_dir: str | Path) -> EvalAggregate:
    """Run every ``*.json`` eval fixture under ``case_dir`` and aggregate."""

    case_dir = Path(case_dir)
    case_files = sorted(case_dir.glob("*.json"))
    results = [run_eval_case(p) for p in case_files]
    return aggregate_eval_results(results)


def aggregate_eval_results(results: Iterable[EvalCaseResult]) -> EvalAggregate:
    """Compute per-case pass rate + per-hint precision/recall/F1.

    Per-hint metric semantics (intentionally hint-friendly):
      tp  = cases where the hint was required and was detected
      fn  = cases where the hint was required but not detected
      fp  = cases where the hint is in the case's ``forbiddenHintIds`` and
            was detected (i.e., a real false positive). Detections that are
            *neither* required *nor* forbidden are treated as neutral —
            often a hint correctly fires across multiple unrelated cases
            (e.g., NATIVE_POLL_IDLE_LIKELY can co-fire with system pressure
            cases without being the primary cause), and we don't want that
            to be punished as a false positive.

    Precision and recall are then computed normally over (tp, fp, fn).
    """

    results = list(results)
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    pass_rate = (passed / total) if total else 0.0

    all_ids: set[str] = set()
    for r in results:
        all_ids.update(r.expected_required)
        all_ids.update(r.expected_forbidden)
        all_ids.update(r.detected_hint_ids)

    per_hint: dict[str, dict[str, float]] = {}
    for hid in sorted(all_ids):
        tp = fp = fn = 0
        for r in results:
            in_required = hid in r.expected_required
            in_forbidden = hid in r.expected_forbidden
            in_detected = hid in r.detected_hint_ids
            if in_required and in_detected:
                tp += 1
            elif in_required and not in_detected:
                fn += 1
            if in_forbidden and in_detected:
                fp += 1
        precision = tp / (tp + fp) if (tp + fp) else 1.0 if tp == 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 1.0 if tp == 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        per_hint[hid] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
        }

    return EvalAggregate(
        total=total,
        passed=passed,
        failed=total - passed,
        pass_rate=round(pass_rate, 3),
        per_hint_id=per_hint,
        cases=results,
    )


__all__ = [
    "EvalCaseResult",
    "EvalAggregate",
    "aggregate_eval_results",
    "collect_all_hint_ids",
    "run_eval_case",
    "run_eval_directory",
]
