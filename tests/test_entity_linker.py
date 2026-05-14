"""Tests for anr_evidence.entity_linker."""

from __future__ import annotations

import unittest

from anr_evidence.entity_linker import build_entity_map, extract_trace_entities, entity_summary_for_ai


class EntityLinkerTests(unittest.TestCase):
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
