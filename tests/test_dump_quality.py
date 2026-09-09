from __future__ import annotations

import base64
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from anr_evidence.cross_source_fusion import FusionScope
from anr_evidence.dump_quality import audit_dump_quality, bind_dump_quality_references
from anr_evidence.evidence_slice import attach_store_references, build_evidence_slices
from anr_evidence.evidence_store import EvidenceStore
from anr_evidence.evidence_store_export import export_evidence_store


ANCHOR = "04-12 10:00:05.000 1234 1234 I am_anr  : [0,1234,com.demo,1,Input dispatching timed out]"
HEADER = "----- pid 1234 at 2026-04-12 10:00:05.100000000+0000 -----"
PROCESS = "Cmd line: com.demo"


def scope(**changes: object) -> FusionScope:
    value: dict[str, object] = {
        "group_id": "g",
        "target_pid": 1234,
        "target_process": "com.demo",
        "trace_pid": 1234,
        "trace_process": "com.demo",
        "anchor_timestamp": datetime(2026, 4, 12, 10, 0, 5),
        "trace_timestamp": datetime(2026, 4, 12, 10, 0, 5, 100_000),
        "before_seconds": 15,
        "after_seconds": 15,
    }
    value.update(changes)
    return FusionScope(**value)  # type: ignore[arg-type]


def captured_source(path: str, content: str) -> dict[str, object]:
    raw = content.encode("utf-8")
    return {
        "path": path,
        "content": content,
        "originalArtifacts": [{
            "path": path,
            "originalBase64": base64.b64encode(raw).decode("ascii"),
            "origin": {"type": "file", "path": f"/synthetic/{path}"},
        }],
    }


class DumpQualityAuditTests(unittest.TestCase):
    def test_matched_identity_reports_shared_association_but_not_lifecycle_claims(self) -> None:
        quality = audit_dump_quality(scope(), trace_tid=1)

        self.assertEqual(quality["schemaVersion"], "dq1-v1")
        self.assertEqual(quality["identityStatus"], "matched")
        self.assertEqual(quality["timeStatus"], "within_window")
        self.assertEqual(quality["associationStatus"], "associated")
        self.assertEqual(quality["dump"]["tid"], "1")
        self.assertEqual(quality["freshnessStatus"], "unknown")
        self.assertEqual(quality["stalenessStatus"], "unknown")
        self.assertEqual(quality["processLifetimeStatus"], "unknown")
        self.assertEqual(quality["lifecycleStatus"], "unknown")
        self.assertFalse(quality["eligibleForHighCausalConfidence"])

    def test_wrong_identity_missing_time_invalid_window_and_late_sampling_are_fail_closed(self) -> None:
        cases = (
            (scope(trace_pid=9999), "wrong_pid", "within_window", "rejected", "wrong_trace_identity"),
            (scope(trace_process="other.proc"), "wrong_process", "within_window", "rejected", "wrong_trace_identity"),
            (scope(trace_timestamp=None), "matched", "unknown", "unknown", "unknown_trace_timestamp"),
            (scope(trace_timestamp=datetime(2026, 4, 12, 10, 1, 5)), "matched", "outside_window", "rejected", "dump_outside_window"),
            (scope(before_seconds=float("nan")), "matched", "unknown", "unknown", "invalid_window"),
        )
        for input_scope, identity, time, association, reason in cases:
            with self.subTest(reason=reason):
                quality = audit_dump_quality(input_scope)
                self.assertEqual(quality["identityStatus"], identity)
                self.assertEqual(quality["timeStatus"], time)
                self.assertEqual(quality["scopeRejectionReason"], reason)
                self.assertEqual(quality["associationStatus"], association)
                if reason == "invalid_window":
                    self.assertIsNone(quality["associationWindow"]["beforeSeconds"])
                self.assertEqual(quality["stalenessStatus"], "unknown")
                self.assertFalse(quality["eligibleForHighCausalConfidence"])

    def test_same_tid_and_pid_reuse_at_nonoverlapping_time_is_not_stale_or_associated(self) -> None:
        reused = scope(trace_timestamp=datetime(2026, 4, 12, 10, 5, 5), trace_pid=1234)
        quality = audit_dump_quality(reused, trace_tid=1)

        self.assertEqual(quality["dump"]["tid"], "1")
        self.assertEqual(quality["identityStatus"], "matched")
        self.assertEqual(quality["timeStatus"], "outside_window")
        self.assertEqual(quality["associationStatus"], "rejected")
        self.assertEqual(quality["stalenessStatus"], "unknown")
        self.assertIn("dump_outside_window", quality["gaps"])


class DumpQualityProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        event_raw = (ANCHOR + "\n").encode("utf-8")
        trace_raw = (HEADER + "\n" + PROCESS + "\n").encode("utf-8")
        self.package = {
            "package_id": "dq1-synthetic",
            "sources": {
                "event_log": captured_source("events.txt", event_raw.decode("utf-8")),
                "trace": captured_source("trace.txt", trace_raw.decode("utf-8")),
            },
        }
        self.groups = [{
            "id": "g",
            "eventLog": {"lines": [ANCHOR]},
            "trace": {"lines": [HEADER, PROCESS]},
        }]
        self.exported = export_evidence_store(self.package, self.groups, Path(self.tmp.name))
        self.store = EvidenceStore(self.exported["path"])
        self.addCleanup(self.store.close)
        self.slices = attach_store_references(
            build_evidence_slices(self.groups), self.store, self.exported["groups"],
        )

    def test_committed_field_references_promote_provenance_only(self) -> None:
        quality = bind_dump_quality_references(
            audit_dump_quality(scope()), self.slices, self.store, anchor_line=ANCHOR,
        )

        self.assertEqual(quality["referenceStatus"], "verified")
        self.assertEqual(quality["provenanceStatus"], "verified")
        self.assertEqual(quality["associationStatus"], "associated")
        self.assertEqual(set(quality["fieldReferences"]), {
            "anchor", "dumpPid", "dumpTimestamp", "dumpProcess",
        })
        self.assertFalse(quality["eligibleForHighCausalConfidence"])
        self.assertEqual(quality["freshnessStatus"], "unknown")

    def test_wrong_group_reference_is_invalid_not_reused(self) -> None:
        anchor = next(item for item in self.slices if item.source == "event_log")
        self.store.add_group("foreign")
        self.store.attach_artifact("foreign", anchor.evidence_ref["artifactId"])
        foreign_span = self.store.add_span(
            "foreign", anchor.evidence_ref["artifactId"],
            anchor.evidence_ref["startByte"], anchor.evidence_ref["endByte"],
        )
        foreign_ref = {**anchor.evidence_ref, "spanId": foreign_span, "groupId": "foreign"}
        wrong_group_anchor = replace(anchor, evidence_ref=foreign_ref)
        quality = bind_dump_quality_references(
            audit_dump_quality(scope()), [wrong_group_anchor], self.store, anchor_line=ANCHOR,
        )

        self.assertEqual(quality["referenceStatus"], "invalid")
        self.assertEqual(quality["provenanceStatus"], "invalid")
        self.assertIn("wrong_group_reference", quality["gaps"])
        self.assertIn("dump_reference_integrity_failed", quality["gaps"])
        self.assertFalse(quality["eligibleForHighCausalConfidence"])

    def test_non_header_pid_mention_does_not_bind_dump_identity(self) -> None:
        non_header = "thread mentions pid 1234 but is not an ART dump header"
        package = {
            "package_id": "dq1-non-header",
            "sources": {
                "event_log": captured_source("events.txt", ANCHOR),
                "trace": captured_source("trace.txt", non_header),
            },
        }
        groups = [{
            "id": "g",
            "eventLog": {"lines": [ANCHOR]},
            "trace": {"lines": [non_header]},
        }]
        with tempfile.TemporaryDirectory() as tmpdir:
            exported = export_evidence_store(package, groups, Path(tmpdir))
            with EvidenceStore(exported["path"]) as store:
                slices = attach_store_references(
                    build_evidence_slices(groups), store, exported["groups"],
                )
                quality = bind_dump_quality_references(
                    audit_dump_quality(scope()), slices, store, anchor_line=ANCHOR,
                )

        self.assertEqual(quality["referenceStatus"], "partial")
        self.assertEqual(quality["provenanceStatus"], "partial")
        self.assertNotIn("dumpPid", quality["fieldReferences"])
        self.assertFalse(quality["eligibleForHighCausalConfidence"])


if __name__ == "__main__":
    unittest.main()
