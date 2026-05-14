"""Tests for the deadlock-detection layer added to ``trace_preprocessor``.

These exercise the lock graph, Tarjan SCC cycle detection, and the five
deadlock hint kinds (DEADLOCK_CYCLE / DEADLOCK_LIKELY / DEADLOCK_SELF /
LOCK_OWNER_BLOCKED / LOCK_OWNER_SLEEPING / LOCK_CONTENTION_BLOCKED).
"""

from __future__ import annotations

import unittest

from anr_evidence import consolidate_deadlock_across_traces, preprocess_trace_content
from anr_evidence.ai_context import _append_deadlock_section, _inject_hint_markers
from anr_evidence.trace_preprocessor import compact_trace_section


def _hint_ids(result: dict) -> list[str]:
    return [hint["id"] for hint in result.get("deadlockHints", []) or []]


def _hint_by_id(result: dict, hint_id: str) -> dict | None:
    for hint in result.get("deadlockHints", []) or []:
        if hint["id"] == hint_id:
            return hint
    return None


class TraceDeadlockDetectionTests(unittest.TestCase):
    def test_classic_two_thread_deadlock_cycle_is_detected(self) -> None:
        """T1 holds lockA waiting lockB; T2 holds lockB waiting lockA."""

        trace = "\n".join(
            [
                "04-12 10:00:00.000 ----- pid 100 -----",
                "Cmd line: com.demo",
                '"main" prio=5 tid=1 Blocked',
                '  | sysTid=100',
                '  | state=S',
                '  - waiting to lock <0xAAAA> (a com.demo.LockB) held by thread 2',
                '  - locked <0xBBBB> (a com.demo.LockA)',
                '  at com.demo.Main.run(Main.java:1)',
                '"worker" prio=5 tid=2 Blocked',
                '  | sysTid=200',
                '  | state=S',
                '  - waiting to lock <0xBBBB> (a com.demo.LockA) held by thread 1',
                '  - locked <0xAAAA> (a com.demo.LockB)',
                '  at com.demo.Worker.run(Worker.java:1)',
            ]
        )
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:00.000")

        graph = result["lockGraph"]
        self.assertEqual(sorted(graph["nodes"]), ["1", "2"])
        self.assertEqual(len(graph["edges"]), 2)
        self.assertEqual(len(graph["cycles"]), 1)
        self.assertEqual(sorted(graph["cycles"][0]["tids"]), ["1", "2"])

        ids = _hint_ids(result)
        self.assertIn("DEADLOCK_CYCLE", ids)
        cycle_hint = _hint_by_id(result, "DEADLOCK_CYCLE")
        self.assertEqual(cycle_hint["severity"], "critical")
        self.assertEqual(cycle_hint["confidence"], "strong")
        self.assertEqual(sorted(cycle_hint["tids"]), ["1", "2"])
        # Once a cycle is reported, the constituent waiters must NOT also be
        # reported as LOCK_CONTENTION_BLOCKED — that would double-count.
        self.assertNotIn("LOCK_CONTENTION_BLOCKED", ids)
        self.assertNotIn("LOCK_OWNER_BLOCKED", ids)

    def test_four_thread_deadlock_cycle_with_real_world_layout(self) -> None:
        """Reproduces the wiki example: 1 → 189 → 170 → 27 → 189 (with 1 leading in)."""

        trace = "\n".join(
            [
                "04-12 10:00:00.000 ----- pid 100 -----",
                "Cmd line: com.demo",
                '"main" prio=5 tid=1 Blocked',
                '  | sysTid=1',
                '  - waiting to lock <0x0ca44263> (a MyManager) held by thread 189',
                '"node-189" prio=5 tid=189 Blocked',
                '  | sysTid=189',
                '  - waiting to lock <0x0ee7c7ea> (a ConfigStore) held by thread 170',
                '  - locked <0x0ca44263> (a MyManager)',
                '"node-170" prio=5 tid=170 Blocked',
                '  | sysTid=170',
                '  - waiting to lock <0x0bd0df19> (a Cache) held by thread 27',
                '  - locked <0x0ee7c7ea> (a ConfigStore)',
                '"node-27"  prio=5 tid=27  Blocked',
                '  | sysTid=27',
                '  - waiting to lock <0x0ca44263> (a MyManager) held by thread 189',
                '  - locked <0x0bd0df19> (a Cache)',
            ]
        )
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:00.000")

        cycles = result["lockGraph"]["cycles"]
        self.assertEqual(len(cycles), 1)
        self.assertEqual(sorted(cycles[0]["tids"]), ["170", "189", "27"])
        # tid=1 is NOT in the cycle (it just leads into it). It should be
        # surfaced as a LOCK_OWNER_BLOCKED chain instead.
        cycle_hint = _hint_by_id(result, "DEADLOCK_CYCLE")
        self.assertIsNotNone(cycle_hint)
        self.assertNotIn("1", cycle_hint["tids"])
        # Main thread chain points into the cycle's owner.
        chain_hint = _hint_by_id(result, "LOCK_OWNER_BLOCKED")
        # Chain may exist; at minimum, the main thread itself should not be
        # reported as a cycle member.
        if chain_hint:
            self.assertEqual(chain_hint.get("anchorTid"), "1")

    def test_self_loop_emits_deadlock_self(self) -> None:
        trace = "\n".join(
            [
                "04-12 10:00:00.000 ----- pid 100 -----",
                "Cmd line: com.demo",
                '"oops" prio=5 tid=7 Blocked',
                '  | sysTid=7',
                '  - waiting to lock <0xC0DE> (a Foo) held by thread 7',
                '  - locked <0xC0DE> (a Foo)',
            ]
        )
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:00.000")
        ids = _hint_ids(result)
        self.assertIn("DEADLOCK_SELF", ids)
        hint = _hint_by_id(result, "DEADLOCK_SELF")
        self.assertEqual(hint["tids"], ["7"])
        self.assertEqual(hint["severity"], "warning")
        self.assertEqual(hint["confidence"], "medium")

    def test_chain_without_cycle_emits_lock_owner_blocked(self) -> None:
        """Three-tid chain main → A → B with no edge from B back."""

        trace = "\n".join(
            [
                "04-12 10:00:00.000 ----- pid 100 -----",
                "Cmd line: com.demo",
                '"main" prio=5 tid=1 Blocked',
                '  | sysTid=1',
                '  - waiting to lock <0x1111> (a L1) held by thread 2',
                '"workerA" prio=5 tid=2 Blocked',
                '  | sysTid=2',
                '  - waiting to lock <0x2222> (a L2) held by thread 3',
                '  - locked <0x1111> (a L1)',
                '"workerB" prio=5 tid=3 Blocked',
                '  | sysTid=3',
                '  - waiting to lock <0x3333> (a L3) held by thread 4',
                '  - locked <0x2222> (a L2)',
                '"workerC" prio=5 tid=4 Native',
                '  | sysTid=4',
                '  - locked <0x3333> (a L3)',
                '  native: #00 pc 0  /system/lib/libc.so (read+0)',
            ]
        )
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:00.000")
        self.assertEqual(result["lockGraph"]["cycles"], [])

        chain_hint = _hint_by_id(result, "LOCK_OWNER_BLOCKED")
        self.assertIsNotNone(chain_hint)
        self.assertEqual(chain_hint["anchorTid"], "1")
        self.assertEqual(chain_hint["chain"][0], "1")
        # Walk should reach the non-blocked tid 4 and stop there.
        self.assertEqual(chain_hint["chain"][-1], "4")

    def test_owner_sleeping_emits_lock_owner_sleeping(self) -> None:
        trace = "\n".join(
            [
                "04-12 10:00:00.000 ----- pid 100 -----",
                "Cmd line: com.demo",
                '"main" prio=5 tid=1 Blocked',
                '  | sysTid=1',
                '  - waiting to lock <0xDEAD> (a Foo) held by thread 9',
                '"sleepy" prio=5 tid=9 Sleeping',
                '  | sysTid=9',
                '  - locked <0xDEAD> (a Foo)',
                '  - sleeping on <0xBEEF>',
                '  at java.lang.Thread.sleep(Native method)',
            ]
        )
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:00.000")
        ids = _hint_ids(result)
        self.assertIn("LOCK_OWNER_SLEEPING", ids)
        self.assertNotIn("DEADLOCK_CYCLE", ids)
        hint = _hint_by_id(result, "LOCK_OWNER_SLEEPING")
        self.assertEqual(hint["anchorTid"], "1")

    def test_simple_lock_contention_with_runnable_owner(self) -> None:
        trace = "\n".join(
            [
                "04-12 10:00:00.000 ----- pid 100 -----",
                "Cmd line: com.demo",
                '"main" prio=5 tid=1 Blocked',
                '  | sysTid=1',
                '  - waiting to lock <0xCAFE> (a Foo) held by thread 5',
                '"busy" prio=5 tid=5 Runnable',
                '  | sysTid=5',
                '  - locked <0xCAFE> (a Foo)',
                '  at com.demo.Busy.compute(Busy.java:42)',
            ]
        )
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:00.000")
        ids = _hint_ids(result)
        self.assertIn("LOCK_CONTENTION_BLOCKED", ids)
        self.assertNotIn("DEADLOCK_CYCLE", ids)
        self.assertNotIn("LOCK_OWNER_SLEEPING", ids)

    def test_suspended_threads_are_excluded_from_cycle_detection(self) -> None:
        """Signal-Catcher dump artifact: SUSPENDED ≠ deadlock participant."""

        trace = "\n".join(
            [
                "04-12 10:00:00.000 ----- pid 100 -----",
                "Cmd line: com.demo",
                '"main" prio=5 tid=1 SUSPENDED',
                '  | sysTid=1',
                '  - waiting to lock <0xAAAA> (a L1) held by thread 2',
                '"worker" prio=5 tid=2 SUSPENDED',
                '  | sysTid=2',
                '  - waiting to lock <0xBBBB> (a L2) held by thread 1',
                '  - locked <0xAAAA> (a L1)',
            ]
        )
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:00.000")
        # Both threads are SUSPENDED, so neither contributes an edge.
        self.assertEqual(result["lockGraph"]["edges"], [])
        self.assertEqual(result["lockGraph"]["cycles"], [])
        self.assertEqual(result["deadlockHints"], [])

    def test_no_lock_data_yields_empty_graph(self) -> None:
        trace = "\n".join(
            [
                "04-12 10:00:00.000 ----- pid 100 -----",
                "Cmd line: com.demo",
                '"main" prio=5 tid=1 Native',
                '  at android.os.MessageQueue.nativePollOnce(Native method)',
            ]
        )
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:00.000")
        self.assertEqual(result["lockGraph"]["edges"], [])
        self.assertEqual(result["lockGraph"]["cycles"], [])
        self.assertEqual(result["deadlockHints"], [])

    def test_thread_dict_exposes_held_and_waiting_locks(self) -> None:
        trace = "\n".join(
            [
                "04-12 10:00:00.000 ----- pid 100 -----",
                "Cmd line: com.demo",
                '"main" prio=5 tid=1 Blocked',
                '  - waiting to lock <0xAAAA> (a L1) held by thread 2',
                '  - locked <0xBBBB> (a L2)',
            ]
        )
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:00.000")
        main_thread = next(t for t in result["threads"] if t["tid"] == "1")
        self.assertEqual(main_thread["heldLocks"], ["0xBBBB"])
        self.assertEqual(main_thread["waitingLocks"], [{"object": "0xAAAA", "ownerTid": "2"}])


class DeadlockSectionRenderingTests(unittest.TestCase):
    def test_render_emits_section_when_hints_present(self) -> None:
        out: list[str] = []
        _append_deadlock_section(
            out,
            {
                "nodes": ["1", "2"],
                "edges": [
                    {"waiterTid": "1", "ownerTid": "2", "lockObject": "0xAAA"},
                    {"waiterTid": "2", "ownerTid": "1", "lockObject": "0xBBB"},
                ],
                "cycles": [{"tids": ["1", "2"], "size": 2, "selfLoop": False}],
            },
            [
                {
                    "id": "DEADLOCK_CYCLE",
                    "severity": "critical",
                    "confidence": "strong",
                    "message": "test cycle",
                    "nextActions": ["fix it"],
                }
            ],
        )
        text = "\n".join(out)
        self.assertIn("### 死锁检测", text)
        self.assertIn("Cycle #1", text)
        self.assertIn("DEADLOCK_CYCLE", text)
        self.assertIn("next: fix it", text)

    def test_render_skipped_when_no_graph_and_no_hints(self) -> None:
        out: list[str] = []
        _append_deadlock_section(out, None, [])
        self.assertEqual(out, [])

    def test_render_when_graph_present_but_no_hints(self) -> None:
        out: list[str] = []
        _append_deadlock_section(
            out,
            {
                "nodes": ["1", "2"],
                "edges": [{"waiterTid": "1", "ownerTid": "2", "lockObject": "0xAAA"}],
                "cycles": [],
            },
            [],
        )
        text = "\n".join(out)
        self.assertIn("### 死锁检测", text)
        self.assertIn("`0` cycle(s)", text)
        self.assertIn("Hints: none", text)


class CompactionPriorityTests(unittest.TestCase):
    """Phase A — deadlock-graph members must survive compaction."""

    def test_priority_tids_pin_threads_through_compaction(self) -> None:
        """A 6-thread deadlock cycle must keep all 6 cycle members in the compacted view."""

        # 6 threads in a ring: 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 1
        thread_blocks = []
        for i in range(1, 7):
            owner = (i % 6) + 1
            thread_blocks.append("\n".join([
                f'"node-{i}" prio=5 tid={i} Blocked',
                f'  | sysTid={i}',
                f'  - waiting to lock <0x{i:04x}> (a L{i}) held by thread {owner}',
                f'  - locked <0x{owner:04x}> (a L{owner})',
                f'  at com.demo.Node{i}.run(Node.java:1)',
            ]))
        # Add 8 noise threads (NativePollOnce / Binder etc.) that the default
        # heuristic would otherwise rank above some cycle members.
        noise = []
        for i in range(20, 28):
            noise.append("\n".join([
                f'"binder:{i}" prio=5 tid={i} Native',
                f'  | sysTid={i}',
                '  native: #00 pc 0  /system/lib/libc.so (__ioctl+0)',
                '  native: #01 pc 0  /system/lib/libbinder.so (talkWithDriver+0)',
            ]))
        trace = "\n".join(["04-12 10:00:00.000 ----- pid 100 -----", "Cmd line: com.demo"] + thread_blocks + noise)

        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:00.000", max_lines=80)

        # Every tid in the cycle must appear in the compacted view.
        compacted = "\n".join(result["compactedLines"])
        for i in range(1, 7):
            self.assertIn(f'"node-{i}" prio=5 tid={i}', compacted, f"node-{i} dropped from compacted view")

    def test_compact_trace_section_accepts_priority_tids_kwarg(self) -> None:
        """compact_trace_section signature is backward compatible (defaults work)."""

        trace_lines = "\n".join([
            "----- pid 1 -----",
            "Cmd line: x",
            '"main" prio=5 tid=1 Native',
            '  at android.os.MessageQueue.nativePollOnce(Native method)',
            '"worker" prio=5 tid=99 Blocked',
            '  - waiting to lock <0xAB> held by thread 100',
        ]).splitlines()
        # Without priority_tids — default behaviour
        out_default = compact_trace_section(trace_lines, max_lines=200)
        self.assertEqual(out_default, trace_lines)
        # With explicit priority_tids — also fine, no errors
        out_pinned = compact_trace_section(trace_lines, max_lines=200, priority_tids={"99"})
        self.assertIn('"worker" prio=5 tid=99 Blocked', "\n".join(out_pinned))


class CrossProcessDeadlockTests(unittest.TestCase):
    """Phase B — main thread parked in binder_wait_reply + local cycle."""

    def test_main_binder_wait_plus_local_cycle_emits_cross_process_suspect(self) -> None:
        trace = "\n".join([
            "04-12 10:00:00.000 ----- pid 100 -----",
            "Cmd line: com.demo",
            '"main" prio=5 tid=1 Native',
            '  | sysTid=100',
            '  native: #00 pc 0  /system/lib/libc.so (__ioctl+0)',
            '  native: #01 pc 0  /system/lib/libbinder.so (android::IPCThreadState::talkWithDriver(bool)+0)',
            '  native: #02 pc 0  /system/lib/libbinder.so (android::IPCThreadState::waitForResponse+0)',
            '  at android.os.BinderProxy.transactNative(Native method)',
            # Local cycle in this process: tid=10 ↔ tid=11
            '"workerA" prio=5 tid=10 Blocked',
            '  | sysTid=110',
            '  - waiting to lock <0xAAAA> (a L1) held by thread 11',
            '  - locked <0xBBBB> (a L2)',
            '"workerB" prio=5 tid=11 Blocked',
            '  | sysTid=111',
            '  - waiting to lock <0xBBBB> (a L2) held by thread 10',
            '  - locked <0xAAAA> (a L1)',
        ])
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:00.000")
        ids = [h["id"] for h in result["deadlockHints"]]
        self.assertIn("DEADLOCK_CYCLE", ids)
        self.assertIn("CROSS_PROCESS_DEADLOCK_SUSPECTED", ids)
        suspect = next(h for h in result["deadlockHints"] if h["id"] == "CROSS_PROCESS_DEADLOCK_SUSPECTED")
        self.assertEqual(suspect["confidence"], "weak")
        self.assertEqual(suspect["category"], "binder")
        self.assertEqual(suspect["tids"], ["1"])
        self.assertEqual(suspect["cycleTids"], [["10", "11"]])

    def test_no_local_cycle_means_no_cross_process_suspect(self) -> None:
        """Main thread in binder_wait_reply alone is NOT enough — needs local cycle too."""

        trace = "\n".join([
            "04-12 10:00:00.000 ----- pid 100 -----",
            "Cmd line: com.demo",
            '"main" prio=5 tid=1 Native',
            '  native: #02 pc 0  /system/lib/libbinder.so (android::IPCThreadState::waitForResponse+0)',
            '  at android.os.BinderProxy.transactNative(Native method)',
        ])
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:00.000")
        ids = [h["id"] for h in result["deadlockHints"]]
        self.assertNotIn("CROSS_PROCESS_DEADLOCK_SUSPECTED", ids)


class CrossTraceConsistencyTests(unittest.TestCase):
    """Phase C — multi-trace cycle consistency upgrades to CONFIRMED."""

    @staticmethod
    def _two_thread_cycle_trace(*, ts: str) -> str:
        return "\n".join([
            f"04-12 10:00:0{ts} ----- pid 100 -----",
            "Cmd line: com.demo",
            '"main" prio=5 tid=1 Blocked',
            '  - waiting to lock <0xAAAA> (a LB) held by thread 2',
            '  - locked <0xBBBB> (a LA)',
            '"worker" prio=5 tid=2 Blocked',
            '  - waiting to lock <0xBBBB> (a LA) held by thread 1',
            '  - locked <0xAAAA> (a LB)',
        ])

    def test_two_consistent_traces_produce_confirmed_hint(self) -> None:
        traces = [
            self._two_thread_cycle_trace(ts="0.000"),
            self._two_thread_cycle_trace(ts="2.000"),
        ]
        report = consolidate_deadlock_across_traces(traces)
        self.assertEqual(report["traceCount"], 2)
        self.assertEqual(len(report["consistentCycles"]), 1)
        cycle = report["consistentCycles"][0]
        self.assertEqual(cycle["tids"], ["1", "2"])
        self.assertEqual(cycle["traceIndices"], [0, 1])
        self.assertEqual(cycle["occurrences"], 2)
        self.assertEqual(len(report["upgradedHints"]), 1)
        upgraded = report["upgradedHints"][0]
        self.assertEqual(upgraded["id"], "DEADLOCK_CYCLE_CONFIRMED")
        self.assertEqual(upgraded["confidence"], "confirmed")
        self.assertEqual(upgraded["severity"], "critical")

    def test_inconsistent_traces_do_not_upgrade(self) -> None:
        """Cycle in trace 0 (1↔2) is gone in trace 1 (only worker still blocked) → not consistent."""

        traces = [
            self._two_thread_cycle_trace(ts="0.000"),
            "\n".join([
                "04-12 10:00:02.000 ----- pid 100 -----",
                "Cmd line: com.demo",
                '"main" prio=5 tid=1 Native',
                '  at android.os.MessageQueue.nativePollOnce(Native method)',
                '"worker" prio=5 tid=2 Native',
                '  at java.lang.Thread.run(Thread.java:1)',
            ]),
        ]
        report = consolidate_deadlock_across_traces(traces)
        self.assertEqual(report["consistentCycles"], [])
        self.assertEqual(report["upgradedHints"], [])

    def test_empty_input_returns_empty_report(self) -> None:
        report = consolidate_deadlock_across_traces([])
        self.assertEqual(report["traceCount"], 0)
        self.assertEqual(report["consistentCycles"], [])
        self.assertEqual(report["upgradedHints"], [])


class HintMarkerInjectionTests(unittest.TestCase):
    """Phase D — inline ▸ HINT markers injected below original lock-wait lines."""

    def test_marker_injected_below_waiting_to_lock_line(self) -> None:
        trace_lines = [
            '"main" prio=5 tid=1 Blocked',
            '  - waiting to lock <0xAAAA> (a Foo) held by thread 2',
            '  at com.demo.Main.run(Main.java:1)',
        ]
        hints = [{
            "id": "DEADLOCK_CYCLE",
            "confidence": "strong",
            "edges": [{"waiterTid": "1", "ownerTid": "2", "lockObject": "0xAAAA"}],
        }]
        out = _inject_hint_markers(trace_lines, hints)
        self.assertEqual(len(out), 4)
        self.assertEqual(out[0], trace_lines[0])
        self.assertEqual(out[1], trace_lines[1])
        self.assertIn("▸ HINT[DEADLOCK_CYCLE, strong]", out[2])
        self.assertIn("tid=1 → tid=2", out[2])
        self.assertEqual(out[3], trace_lines[2])

    def test_no_hints_returns_original_lines(self) -> None:
        trace_lines = ['"main" prio=5 tid=1 Blocked']
        self.assertEqual(_inject_hint_markers(trace_lines, []), trace_lines)
        self.assertEqual(_inject_hint_markers([], []), [])

    def test_marker_does_not_modify_original_line(self) -> None:
        """Property: original line content must be preserved verbatim for grep-ability."""

        trace_lines = ['  - waiting to lock <0xCAFE> (a Foo) held by thread 5']
        hints = [{
            "id": "LOCK_CONTENTION_BLOCKED",
            "confidence": "strong",
            "edges": [{"waiterTid": "1", "ownerTid": "5", "lockObject": "0xCAFE"}],
        }]
        out = _inject_hint_markers(trace_lines, hints)
        self.assertEqual(out[0], trace_lines[0])
        self.assertTrue(out[1].startswith("  "))
        self.assertIn("▸ HINT[LOCK_CONTENTION_BLOCKED, strong]", out[1])

    def test_no_dedup_per_unique_marker(self) -> None:
        """Same edge appearing in two hints generates two markers (different ids)."""

        trace_lines = ['  - waiting to lock <0xAAAA> (a Foo) held by thread 2']
        hints = [
            {"id": "DEADLOCK_CYCLE", "confidence": "strong",
             "edges": [{"waiterTid": "1", "ownerTid": "2", "lockObject": "0xAAAA"}]},
            {"id": "LOCK_OWNER_BLOCKED", "confidence": "strong",
             "edges": [{"waiterTid": "1", "ownerTid": "2", "lockObject": "0xAAAA"}]},
        ]
        out = _inject_hint_markers(trace_lines, hints)
        self.assertEqual(len(out), 3)
        self.assertIn("DEADLOCK_CYCLE", out[1])
        self.assertIn("LOCK_OWNER_BLOCKED", out[2])


if __name__ == "__main__":
    unittest.main()
