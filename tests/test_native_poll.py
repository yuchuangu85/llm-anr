"""Tests for the NativePollOnce true/false hint emitter.

Validates the disambiguation between a genuinely-idle Looper and one that
has been processing slow messages but happened to park between two of them.
"""

from __future__ import annotations

import unittest

from anr_evidence import preprocess_trace_content


def _hint(result: dict, hint_id: str) -> dict | None:
    for hint in result.get("traceHints", []) or []:
        if hint["id"] == hint_id:
            return hint
    return None


def _trace_with_main_in_native_poll(*, run_ns: int, wait_ns: int) -> str:
    return "\n".join([
        "04-12 10:00:05.100 ----- pid 100 -----",
        "Cmd line: com.demo",
        '"main" prio=5 tid=1 Native',
        '  | sysTid=100 nice=0',
        f'  | state=S schedstat=( {run_ns} {wait_ns} 100 ) utm=0 stm=0 core=0 HZ=100',
        '  native: #00 pc 00012345  /system/lib64/libc.so (__epoll_pwait+8)',
        '  at android.os.MessageQueue.nativePollOnce(Native method)',
        '  at android.os.MessageQueue.next(MessageQueue.java:336)',
        '  at android.os.Looper.loopOnce(Looper.java:174)',
    ])


class NativePollHintTests(unittest.TestCase):
    def test_idle_likely_when_runns_and_waitns_both_tiny(self) -> None:
        trace = _trace_with_main_in_native_poll(run_ns=10_000_000, wait_ns=20_000_000)
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:05.100")
        hint = _hint(result, "NATIVE_POLL_IDLE_LIKELY")
        self.assertIsNotNone(hint, "expected NATIVE_POLL_IDLE_LIKELY but got: " + str([h["id"] for h in result["traceHints"]]))
        self.assertEqual(hint["confidence"], "weak")
        self.assertEqual(hint["severity"], "info")
        self.assertEqual(hint["schedstat"]["runNs"], 10_000_000)
        # Must NOT also emit BUSY in the same run
        self.assertIsNone(_hint(result, "NATIVE_POLL_BUT_BUSY"))

    def test_busy_when_runns_large(self) -> None:
        # 500ms of CPU time → "busy" branch
        trace = _trace_with_main_in_native_poll(run_ns=500_000_000, wait_ns=10_000_000)
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:05.100")
        hint = _hint(result, "NATIVE_POLL_BUT_BUSY")
        self.assertIsNotNone(hint)
        self.assertEqual(hint["confidence"], "strong")
        self.assertEqual(hint["severity"], "warning")
        self.assertIsNone(_hint(result, "NATIVE_POLL_IDLE_LIKELY"))

    def test_busy_when_waitns_huge_and_runns_significant(self) -> None:
        # Scheduler-starved while doing real work
        trace = _trace_with_main_in_native_poll(run_ns=100_000_000, wait_ns=3_000_000_000)
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:05.100")
        self.assertIsNotNone(_hint(result, "NATIVE_POLL_BUT_BUSY"))

    def test_ambiguous_zone_emits_weak_hint(self) -> None:
        # 100ms run / 100ms wait — neither idle (>=50ms) nor clearly busy
        trace = _trace_with_main_in_native_poll(run_ns=100_000_000, wait_ns=100_000_000)
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:05.100")
        hint = _hint(result, "NATIVE_POLL_AMBIGUOUS")
        self.assertIsNotNone(hint)
        self.assertEqual(hint["confidence"], "weak")

    def test_no_hint_when_main_not_in_native_poll(self) -> None:
        trace = "\n".join([
            "04-12 10:00:05.100 ----- pid 100 -----",
            "Cmd line: com.demo",
            '"main" prio=5 tid=1 Runnable',
            '  | sysTid=100',
            '  at com.demo.Foo.compute(Foo.java:42)',
        ])
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:05.100")
        for hid in ("NATIVE_POLL_IDLE_LIKELY", "NATIVE_POLL_BUT_BUSY", "NATIVE_POLL_AMBIGUOUS"):
            self.assertIsNone(_hint(result, hid))

    def test_busy_and_idle_are_mutually_exclusive(self) -> None:
        """Property: at most one of the three NativePoll hints fires per trace."""

        for run_ns, wait_ns in [(10_000_000, 10_000_000), (500_000_000, 10_000_000), (100_000_000, 100_000_000)]:
            trace = _trace_with_main_in_native_poll(run_ns=run_ns, wait_ns=wait_ns)
            result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:05.100")
            poll_ids = [h["id"] for h in result["traceHints"] if h["id"].startswith("NATIVE_POLL")]
            self.assertEqual(len(poll_ids), 1, f"expected exactly 1 NATIVE_POLL hint for ({run_ns},{wait_ns}), got {poll_ids}")


if __name__ == "__main__":
    unittest.main()
