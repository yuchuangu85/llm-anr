"""Tests for anr_evidence.entity_linker."""

from __future__ import annotations

import unittest

from anr_evidence.entity_linker import (
    build_entity_map, extract_trace_entities, entity_summary_for_ai, find_entity_cross_refs,
)
from anr_evidence.log_identity import parse_line_identity
from anr_evidence.trace_preprocessor import preprocess_trace_content


class EntityLinkerTests(unittest.TestCase):
    def _map(self, lines, *, process="com.demo", preprocessed=None):
        return build_entity_map({"package_id": "identity-regression", "sources": {
            "trace": {"content": f"----- pid 123 -----\nCmd line: {process}\n  | sysTid=123"},
            "logcat": {"content": "\n".join(lines)},
        }}, trace_preprocessed=preprocessed)

    def test_only_structured_numeric_fields_create_references(self):
        em = self._map([
            "04-12 10:00:00.123 1234 5123 W Tag: took 123 ms code=123 uid=5123",
            "04-12 10:00:01.000 123 999 W Tag: work",
            "04-12 10:00:02.000 999 123 W Tag: work",
            "04-12 10:00:03.000 999 999 W Tag: pid=123 uid=123",
        ])
        refs = [r for r in em.refs_by_entity["123"] if r.source_kind == "logcat"]
        self.assertEqual([(r.line_index, r.entity_type, r.context) for r in refs], [
            (1, "pid", "emitter"), (2, "tid", "emitter"),
            (3, "pid", "target"), (3, "uid", "explicit"),
        ])

    def test_process_names_are_exact_and_subprocesses_are_distinct(self):
        lines = ["owner=com.demo.other", "owner=com.demo:remote", "owner=com.demo", "owner=com.demo:remote2"]
        em = self._map(lines)
        self.assertEqual([r.line_index for r in em.refs_by_entity["com.demo"] if r.source_kind == "logcat"], [2])
        remote = self._map(lines, process="com.demo:remote")
        self.assertEqual([r.line_index for r in remote.refs_by_entity["com.demo:remote"] if r.source_kind == "logcat"], [1])

    def test_event_emitter_is_not_anr_target(self):
        em = build_entity_map({"sources": {
            "trace": {"content": "----- pid 123 -----\nCmd line: com.demo\n | sysTid=123"},
            "event_log": {"content": "04-12 10:00:00.000 999 999 I am_anr: [0,123,com.demo,1,reason]"},
        }})
        refs = [r for r in em.refs_by_entity["123"] if r.source_kind == "event_log"]
        self.assertEqual([(r.entity_type, r.context) for r in refs], [("pid", "target")])

    def test_selected_trace_does_not_import_other_process_ids(self):
        em = self._map(["pid=123", "pid=456", "tid=457"], preprocessed={
            "pid": 456, "processName": "com.selected", "threads": [{"sysTid": 457}],
        })
        self.assertEqual(em.pids, frozenset({"456"}))
        self.assertEqual(em.tids, frozenset({"457"}))
        self.assertEqual(em.process_name, "com.selected")
        self.assertNotIn("123", em.refs_by_entity)

    def test_selected_trace_references_exclude_same_tid_in_other_dump(self):
        trace = "\n".join([
            "----- pid 123 at 2026-04-12 10:00:00 -----",
            "Cmd line: com.other",
            '"main" prio=5 tid=1 Native',
            " | sysTid=457",
            "",
            "----- pid 456 at 2026-04-12 10:00:05 -----",
            "Cmd line: com.selected",
            '"main" prio=5 tid=1 Native',
            " | sysTid=457",
        ])
        selected = preprocess_trace_content(trace, process_name="com.selected")
        em = build_entity_map({"sources": {"trace": {"content": trace}}},
                              trace_preprocessed=selected)
        refs = [r for r in em.refs_by_entity["457"] if r.source_kind == "trace"]
        self.assertEqual([r.line_index for r in refs], [8])

    def test_selected_entity_inventory_uses_full_not_compacted_section(self):
        lines = ["----- pid 456 -----", "Cmd line: com.selected"]
        for tid in range(451, 459):
            lines.extend([f'"thread-{tid}" prio=5 tid={tid} Native',
                          f" | sysTid={tid}", " at example.Work.run(Work.java:1)"])
        trace = "\n".join(lines)
        selected = preprocess_trace_content(trace, max_lines=6)
        self.assertLess(len(selected["threads"]), 8)
        em = build_entity_map({"sources": {
            "trace": {"content": trace}, "logcat": {"content": "tid=458"},
        }}, trace_preprocessed=selected)
        self.assertEqual(em.tids, frozenset(str(tid) for tid in range(451, 459)))
        self.assertEqual(len(find_entity_cross_refs(em, "458", source_kinds=["logcat"])), 1)

    def test_unknown_selected_metadata_does_not_restore_raw_thread_inventory(self):
        em = build_entity_map({"sources": {"trace": {"content": '"main" tid=1\n | sysTid=457'}}},
                              trace_preprocessed={})
        self.assertEqual(em.tids, frozenset())
        self.assertEqual(em.refs_by_source["trace"], [])

    def test_uid_cap_and_reference_order_are_deterministic(self):
        lines = [f"uid={n}" for n in range(40, 0, -1)]
        em = self._map(lines)
        linked = sorted(int(r.entity_value) for r in em.refs_by_source["logcat"])
        self.assertEqual(linked, list(range(1, 21)))
        self.assertEqual(em, self._map(lines))

    def test_same_value_can_be_queried_by_entity_type(self):
        em = self._map(["04-12 10:00:00.000 123 123 I Tag: uid=123"])
        for kind in ("pid", "tid", "uid"):
            refs = find_entity_cross_refs(em, "123", source_kinds=["logcat"], entity_type=kind)
            self.assertEqual(len(refs), 1)
            self.assertEqual(refs[0].entity_type, kind)

    def test_unstructured_identity_is_unknown(self):
        identity = parse_line_identity("04-12 10:00:00.123 took 123ms code=123")
        self.assertEqual(identity.status, "unknown")

    def test_extract_trace_entities(self) -> None:
        trace = "\n".join([
            "Cmd line: com.foo.bar",
            "----- pid 1234 -----",
            '  "main" prio=5 tid=1 Native',
            "  | sysTid=1234 nice=0 cgrp=default",
            '  "binder" prio=5 tid=2 Native',
            "  | sysTid=15 nice=0 cgrp=default",
        ])
        process_name, pids, tids = extract_trace_entities(trace)
        self.assertEqual(process_name, "com.foo.bar")
        self.assertIn("1234", pids)
        self.assertIn("15", tids)

    def test_extract_trace_no_process(self) -> None:
        trace = '----- pid 9999 -----\n  "main" prio=5 tid=1 Native\n  | sysTid=9999'
        process_name, pids, tids = extract_trace_entities(trace)
        self.assertIsNone(process_name)
        self.assertIn("9999", pids)

    def test_build_entity_map_cross_references(self) -> None:
        package = {
            "package_id": "test-001",
            "sources": {
                "trace": {"content": "\n".join([
                    "Cmd line: com.example.app",
                    "----- pid 1234 -----",
                    '  "main" prio=5 tid=1 Native',
                    "  | sysTid=1234 nice=0",
                    '  "Binder:1_3" prio=5 tid=3 Native',
                    "  | sysTid=42 nice=0",
                ])},
                "event_log": {"content": "04-12 10:00:02 I am_anr: [0,1234,com.example.app,1]"},
                "logcat": {"content": "04-12 10:00:03 W activitymanager: pid 1234 not responding"},
                "kernel_log": {"content": "04-12 10:00:05 hung task blocked for more than 120s"},
            },
        }
        em = build_entity_map(package)
        self.assertEqual(em.process_name, "com.example.app")
        self.assertIn("1234", em.pids)
        self.assertIn("42", em.tids)
        self.assertGreater(len(em.refs_by_entity.get("1234", [])), 1)

    def test_entity_summary_for_ai(self) -> None:
        package = {
            "package_id": "test-002",
            "sources": {
                "trace": {"content": "Cmd line: demo\n----- pid 1 -----"},
            },
        }
        em = build_entity_map(package)
        summary = entity_summary_for_ai(em)
        self.assertIn("demo", summary)
        self.assertIn("Entity Map", summary)
