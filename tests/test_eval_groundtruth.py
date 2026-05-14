"""Ground-truth eval regression tests.

Runs every fixture under ``tests/fixtures/eval/*.json`` through the full
``build_ai_context`` + AnrManager parser pipeline, then asserts:

  1. Per-case: the configured ``requiredHintIds`` were detected, none of
     the ``forbiddenHintIds`` fired, and the ``primaryRootCauseHintId``
     (if any) was detected.
  2. Aggregate: pass rate must remain at 100% — any drop indicates a
     regression in the hint emitters and must be fixed before merge.
  3. Per-hint precision must remain at 1.0 (no false positives in the
     curated corpus).

This is the regression baseline for any future hint expansion.
"""

from __future__ import annotations

from pathlib import Path
import unittest

from anr_evidence import run_eval_directory

EVAL_DIR = Path(__file__).parent / "fixtures" / "eval"


class GroundTruthEvalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.aggregate = run_eval_directory(EVAL_DIR)

    def test_corpus_is_non_empty(self) -> None:
        self.assertGreaterEqual(self.aggregate.total, 13, "eval corpus should grow over time")

    def test_all_cases_pass(self) -> None:
        failed_cases = [c for c in self.aggregate.cases if not c.passed]
        if failed_cases:
            details = "\n".join(
                f"  - {c.case_id}: {c.notes} (detected={c.detected_hint_ids})"
                for c in failed_cases
            )
            self.fail(
                f"{len(failed_cases)}/{self.aggregate.total} eval cases failed:\n{details}"
            )

    def test_no_false_positives_per_hint(self) -> None:
        offenders = [
            (hid, stats) for hid, stats in self.aggregate.per_hint_id.items()
            if stats["fp"] > 0
        ]
        if offenders:
            details = "\n".join(f"  - {hid}: {stats}" for hid, stats in offenders)
            self.fail(
                f"{len(offenders)} hint id(s) produced false positives in eval corpus:\n{details}"
            )

    def test_aggregate_pass_rate_is_100(self) -> None:
        self.assertEqual(
            self.aggregate.pass_rate,
            1.0,
            f"aggregate pass rate should be 1.0, got {self.aggregate.pass_rate}",
        )

    def test_fusion_promotes_at_least_one_case_to_critical(self) -> None:
        """Eval corpus must include at least one case that exercises fusion."""

        from anr_evidence import build_ai_context
        from anr_evidence.ai_context import AiContextOptions
        import json
        from pathlib import Path

        promoted_any = False
        for case_path in Path(EVAL_DIR).glob("*.json"):
            data = json.loads(case_path.read_text(encoding="utf-8"))
            opts = AiContextOptions(package_name=data.get("expected", {}).get("packageNameFilter"))
            res = build_ai_context(data["package"], opts)
            for group in res.groups:
                for hint in group.get("trace", {}).get("traceHints", []) or []:
                    if hint.get("confidencePromotedFrom") and hint.get("confidence") == "critical":
                        promoted_any = True
                        break
        self.assertTrue(
            promoted_any,
            "no case in the eval corpus actually exercises cross-source fusion to critical",
        )

    def test_each_hint_id_has_at_least_one_case(self) -> None:
        """Every hint id we ship must be exercised by at least one fixture."""

        from anr_evidence.trace_preprocessor import (
            _emit_deadlock_hints,  # noqa: F401  (sanity import)
            _emit_native_poll_hints,  # noqa: F401
        )
        from anr_evidence.anrmanager_parser import _derive_hints  # noqa: F401

        documented_required_ids = {
            "DEADLOCK_CYCLE",
            "DEADLOCK_SELF",
            "LOCK_OWNER_SLEEPING",
            "LOCK_OWNER_BLOCKED",
            "NATIVE_POLL_IDLE_LIKELY",
            "NATIVE_POLL_BUT_BUSY",
            "SYSTEM_CPU_SATURATED",
            "SYSTEM_IO_PRESSURE",
            "ANR_REASON_CLASSIFIED",
            # Phase 4 main-thread pattern hints
            "MAIN_BINDER_WAIT_REPLY",
            "MAIN_SP_APPLY_WAIT",
            "MAIN_DB_BLOCKED",
            "MAIN_RENDER_WAIT_FENCE",
        }
        exercised = set()
        for case in self.aggregate.cases:
            exercised.update(case.expected_required)
        missing = documented_required_ids - exercised
        self.assertFalse(missing, f"hint ids not exercised by any fixture: {sorted(missing)}")


if __name__ == "__main__":
    unittest.main()
