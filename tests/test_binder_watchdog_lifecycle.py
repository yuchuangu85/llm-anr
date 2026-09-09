from __future__ import annotations

from datetime import datetime
import unittest

from anr_evidence.binder_watchdog_lifecycle import build_binder_watchdog_lifecycle


ANCHOR = {
    "line": "04-12 10:00:05.000 1000 1000 I am_anr  : [0,1234,com.demo,1,Input dispatching timed out]",
    "timestamp": datetime(2026, 4, 12, 10, 0, 5),
    "packageName": "com.demo",
}


def associated_dump(**extra: object) -> dict[str, object]:
    value: dict[str, object] = {"associationStatus": "associated", "identityStatus": "matched"}
    value.update(extra)
    return value


class BinderWatchdogLifecycleTests(unittest.TestCase):
    def build(self, **kwargs: object) -> dict[str, object]:
        return build_binder_watchdog_lifecycle(
            group_id="g",
            anchor=ANCHOR,
            dump_quality=associated_dump(),
            **kwargs,
        )

    def test_keyed_explicit_binder_peer_creates_observation_not_cause(self) -> None:
        result = self.build(logcat_lines=[
            "04-12 10:00:04.000 W BinderTxn: slow binder transaction; transactionId=txn-7 callerPid=1234 callerProcess=com.demo targetPid=4321 targetProcess=com.peer started",
            "04-12 10:00:04.300 I BinderTxn: transactionId=txn-7 callerPid=1234 callerProcess=com.demo targetPid=4321 targetProcess=com.peer reply",
        ])

        self.assertEqual(result["schemaVersion"], "binder-watchdog-lifecycle-v1")
        txn = result["binderTransactions"][0]
        self.assertEqual(txn["peerStatus"], "verified")
        self.assertEqual(txn["lifecycleStatus"], "reply_observed")
        edge = result["edges"][0]
        self.assertEqual(edge["kind"], "binder_caller_peer_observation")
        self.assertEqual(edge["causalStatus"], "observation_only")
        self.assertFalse(result["finalJudgment"])
        self.assertTrue(result["notRootCauseYet"])
        self.assertTrue(result["requiresHumanConfirmation"])

    def test_unknown_peer_never_creates_causal_edge(self) -> None:
        result = self.build(logcat_lines=[
            "04-12 10:00:04.000 I BinderTxn: transactionId=txn-unknown callerPid=1234 callerProcess=com.demo started",
        ])

        self.assertEqual(result["binderTransactions"][0]["peerStatus"], "unknown")
        self.assertEqual(result["edges"], [])
        self.assertIn("binder_peer_unknown", result["gaps"])

    def test_wrong_or_foreign_caller_is_not_reused_for_selected_group(self) -> None:
        result = self.build(logcat_lines=[
            "04-12 10:00:04.000 I BinderTxn: transactionId=txn-foreign callerPid=9999 callerProcess=com.other targetPid=4321 targetProcess=com.peer started",
        ])

        self.assertEqual(result["binderTransactions"], [])
        self.assertEqual(result["edges"], [])
        self.assertIn("binder_transaction_foreign_or_wrong_caller", result["gaps"])

    def test_trace_transaction_requires_dq1_associated_dump(self) -> None:
        result = build_binder_watchdog_lifecycle(
            group_id="g", anchor=ANCHOR,
            trace_lines=["BinderTxn: transactionId=txn-7 callerPid=1234 callerProcess=com.demo targetPid=4321 targetProcess=com.peer started"],
            dump_quality={"associationStatus": "rejected", "identityStatus": "wrong_pid"},
        )

        self.assertEqual(result["binderTransactions"], [])
        self.assertIn("trace_dump_not_associated", result["gaps"])

    def test_out_of_window_dump_rejects_trace_peer_even_when_transaction_fields_match(self) -> None:
        result = build_binder_watchdog_lifecycle(
            group_id="g", anchor=ANCHOR,
            trace_lines=["BinderTxn: transactionId=txn-stale callerPid=1234 callerProcess=com.demo targetPid=4321 targetProcess=com.peer started"],
            dump_quality={"associationStatus": "rejected", "timeStatus": "outside_window", "stalenessStatus": "unknown"},
        )
        self.assertEqual(result["binderTransactions"], [])
        self.assertIn("trace_dump_not_associated", result["gaps"])
        self.assertFalse(result["finalJudgment"])

    def test_watchdog_start_blocked_finish_is_ordered_and_recovered(self) -> None:
        result = self.build(logcat_lines=[
            "04-12 10:00:01.000 I Watchdog: checker=ActivityManager started",
            "04-12 10:00:03.000 I Watchdog: checker=ActivityManager blocked",
            "04-12 10:00:04.000 I Watchdog: checker=ActivityManager finished",
        ])

        check = result["watchdogChecks"][0]
        self.assertEqual(check["lifecycleStatus"], "recovered")
        edge = next(edge for edge in result["edges"] if edge["kind"] == "watchdog_checker_blocked_observation")
        self.assertEqual(edge["causalStatus"], "observation_only")

    def test_watchdog_missing_finish_stays_terminal_unknown(self) -> None:
        result = self.build(logcat_lines=[
            "04-12 10:00:01.000 I Watchdog: checker=ActivityManager started",
            "04-12 10:00:03.000 I Watchdog: checker=ActivityManager blocked",
        ])

        self.assertEqual(result["watchdogChecks"][0]["lifecycleStatus"], "terminal_unknown")
        self.assertIn("watchdog_terminal_unobserved", result["gaps"])
        self.assertFalse(result["eligibleForHighCausalConfidence"])

    def test_pressure_and_runnable_are_never_cause_candidates(self) -> None:
        result = self.build(
            trace_lines=['  | state=R schedstat=( 1 2 3 )', '"main" runnable'],
            anrmanager_summary={
                "cpuTotal": {"totalPct": 95.0, "iowaitPct": 25.0},
                "memoryPressure": {"some": {"avg10": 22.0}},
                "cpuTopProcesses": [{"pid": "1234", "processName": "com.demo", "totalPct": 99.0}],
            },
        )

        pressure = result["pressure"]
        self.assertEqual(pressure["causeCandidates"], [])
        self.assertGreaterEqual(len(pressure["amplifiers"]), 3)
        runnable = next(item for item in pressure["context"] if item["kind"] == "runnable_thread_state_only")
        self.assertTrue(runnable["notHighCpuProof"])
        self.assertIn("pressure_requires_mechanism_correlation", result["gaps"])


if __name__ == "__main__":
    unittest.main()


class BinderWatchdogAiContextIntegrationTests(unittest.TestCase):
    def test_full_context_keeps_f2_view_and_renders_conservative_boundary(self) -> None:
        from anr_evidence import AiContextOptions, build_ai_context

        package = {
            "package_id": "f2-context",
            "sources": {
                "event_log": {"path": "events.txt", "content": ANCHOR["line"]},
                "trace": {"path": "trace.txt", "content": "04-12 10:00:05.100 ----- pid 1234 -----\nCmd line: com.demo\n"},
                "logcat": {"path": "logcat.txt", "content": "\n".join([
                    "04-12 10:00:04.000 1234 1234 W BinderTxn: slow binder transaction; transactionId=txn-7 callerPid=1234 callerProcess=com.demo targetPid=4321 targetProcess=com.peer started",
                    "04-12 10:00:04.200 1000 1000 I Watchdog: checker=ActivityManager started",
                    "04-12 10:00:04.300 1000 1000 I Watchdog: checker=ActivityManager blocked",
                ])},
            },
        }
        result = build_ai_context(package, AiContextOptions(package_name="com.demo"))
        view = result.groups[0]["binderWatchdogLifecycle"]
        self.assertEqual(view["binderTransactions"][0]["peerStatus"], "verified")
        self.assertFalse(view["finalJudgment"])
        self.assertEqual(result.summary()["groups"][0]["binderWatchdogLifecycle"], view)
        self.assertIn("binder_watchdog_lifecycle_built", [event["step"] for event in result.events])
        self.assertIn("Binder / Watchdog structured observations (F2)", result.cache_markdown)
        self.assertIn("observation-only", result.cache_markdown)
