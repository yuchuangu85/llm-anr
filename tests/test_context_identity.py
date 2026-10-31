from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from anr_evidence import AiContextOptions, build_ai_context, build_ai_context_artifacts
from anr_evidence.ai_context import _identity_digest, _merge_analyses


TIMESTAMP_ID = "anr-20260412-100005-000"
OLD_AI = "Preserved human-reviewed candidate for input A, not an observed fact."


def _anchor(package_name: str = "com.demo", pid: int = 101, reason: str = "Input dispatching timed out") -> str:
    return f"04-12 10:00:05.000  1000  1001 I am_anr: [0,{pid},{package_name},0,{reason}]"


def _package(package_id: str = "A_INPUT", anchors: list[str] | None = None) -> dict:
    return {
        "package_id": package_id,
        "sources": {
            "event_log": {"path": "events.log", "content": "\n".join(anchors or [_anchor()])},
            "logcat": {
                "path": "logcat.txt",
                "content": "04-12 10:00:04.000 E InputDispatcher Input dispatching timed out for com.demo",
            },
        },
    }


def _fill_ai(group: dict) -> None:
    path = Path(group["artifactPaths"]["analysis"])
    path.write_text(_merge_analyses(path.read_text(encoding="utf-8"), {"final-anr": OLD_AI}), encoding="utf-8")


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()}


class ContextIdentityTests(unittest.TestCase):
    def test_same_millisecond_packages_pids_and_reasons_have_stable_unique_ids(self) -> None:
        anchors = [_anchor(), _anchor("com.other"), _anchor(pid=202), _anchor(reason="Broadcast timeout")]
        first = build_ai_context(_package(anchors=anchors), AiContextOptions())
        second = build_ai_context(_package(anchors=list(reversed(anchors))), AiContextOptions())
        identities = lambda result: {group["anchor"]["line"]: group["id"] for group in result.groups}

        self.assertEqual(len(first.groups), 4)
        self.assertEqual(len({group["id"] for group in first.groups}), 4)
        self.assertEqual(identities(first), identities(second))
        for group in first.groups:
            self.assertRegex(group["id"], rf"^{TIMESTAMP_ID}-[0-9a-f]+$")
            self.assertEqual(group["reconstruction"]["groupId"], group["id"])
        event_ids = {event["details"]["groupId"] for event in first.events if "groupId" in event["details"]}
        self.assertEqual(event_ids, set(identities(first).values()))

    def test_duplicate_accident_rows_still_collapse_including_interleaved_rows(self) -> None:
        for anchors, expected in (
            ([_anchor(), _anchor().replace("05.000", "05.500")], 1),
            ([_anchor(), _anchor("com.other"), _anchor()], 2),
        ):
            with self.subTest(anchors=anchors):
                result = build_ai_context(_package(anchors=anchors), AiContextOptions())
                self.assertEqual(len(result.groups), expected)
                self.assertEqual(len({group["id"] for group in result.groups}), expected)
        self.assertEqual(build_ai_context(_package(), AiContextOptions()).groups[0]["id"], TIMESTAMP_ID)

    def test_artifact_paths_and_json_are_unique_for_timestamp_collisions(self) -> None:
        anchors = [_anchor(), _anchor("com.other"), _anchor(pid=202)]
        with tempfile.TemporaryDirectory() as tmp:
            options = AiContextOptions(out_dir=tmp, build_ess=True, delta_t_normalized=True)
            index = build_ai_context_artifacts(_package(anchors=anchors), options)
            self.assertEqual(index["groupCount"], 3)
            for artifact in ("analysis", "logcat", "reconstruction", "reconstructionJson", "evidenceSlices", "timeNormalized"):
                paths = [group["artifactPaths"][artifact] for group in index["groups"]]
                self.assertEqual(len(set(paths)), 3)
                self.assertTrue(all(Path(path).is_file() for path in paths))
            for group in index["groups"]:
                report = json.loads(Path(group["artifactPaths"]["reconstructionJson"]).read_text(encoding="utf-8"))
                self.assertEqual(report["groupId"], group["id"])
                self.assertEqual(report["anchor"], group["anchor"])
            rerun = build_ai_context_artifacts(_package(anchors=list(reversed(anchors))), options)
            self.assertEqual({g["id"] for g in index["groups"]}, {g["id"] for g in rerun["groups"]})

    def test_different_input_never_reuses_ai_or_changes_original_files(self) -> None:
        for package_id, anchor in (("B_INPUT", _anchor()), ("A_INPUT", _anchor("com.other")), ("B_INPUT", _anchor("com.other"))):
            with self.subTest(package_id=package_id, anchor=anchor), tempfile.TemporaryDirectory() as tmp:
                options = AiContextOptions(out_dir=tmp)
                old = build_ai_context_artifacts(_package(), options)["groups"][0]
                _fill_ai(old)
                old_dir = Path(old["artifactPaths"]["analysis"]).parent
                before = _snapshot(old_dir)
                new_package = _package(package_id, [anchor])
                new = build_ai_context_artifacts(new_package, options)["groups"][0]
                self.assertNotEqual(new["id"], old["id"])
                self.assertEqual(_snapshot(old_dir), before)
                self.assertNotIn(OLD_AI, Path(new["artifactPaths"]["analysis"]).read_text(encoding="utf-8"))
                report = json.loads(Path(new["artifactPaths"]["reconstructionJson"]).read_text(encoding="utf-8"))
                self.assertEqual(report["aiAnalyses"], [])
                self.assertEqual(report["aiClaims"], [])
                self.assertTrue(all(status == "pending" for status in new["analysisSlots"].values()))
                self.assertEqual(build_ai_context_artifacts(new_package, options)["groups"][0]["id"], new["id"])
                self.assertEqual(_snapshot(old_dir), before)
                restored = build_ai_context_artifacts(_package(), options)["groups"][0]
                self.assertEqual(restored["id"], old["id"])
                self.assertIn(OLD_AI, Path(restored["artifactPaths"]["analysis"]).read_text(encoding="utf-8"))

    def test_same_input_preserves_ai_and_stores_identity_and_revision(self) -> None:
        package = _package()
        pure_group = build_ai_context(package, AiContextOptions()).groups[0]
        with tempfile.TemporaryDirectory() as tmp:
            options = AiContextOptions(out_dir=tmp)
            first = build_ai_context_artifacts(package, options)["groups"][0]
            _fill_ai(first)
            second = build_ai_context_artifacts(package, options)["groups"][0]
            self.assertEqual(second["id"], TIMESTAMP_ID)
            self.assertEqual(second["analysisSlots"]["final-anr"], "filled")
            self.assertIn(OLD_AI, Path(second["artifactPaths"]["analysis"]).read_text(encoding="utf-8"))
            report = json.loads(Path(second["artifactPaths"]["reconstructionJson"]).read_text(encoding="utf-8"))
            for stored in (pure_group, second, report):
                self.assertEqual(stored["inputIdentity"]["packageId"], "A_INPUT")
                self.assertEqual(stored["inputIdentity"]["packageName"], "com.demo")
                self.assertEqual(stored["inputIdentity"]["anchor"]["line"], _anchor())
                self.assertEqual(stored["evidenceRevision"], first["evidenceRevision"])
            self.assertFalse(any(w["code"] == "ai-evidence-revision-changed" for w in report["warnings"]))

    def test_retained_evidence_change_preserves_ai_but_warns_even_on_later_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            options = AiContextOptions(out_dir=tmp)
            package = _package()
            first = build_ai_context_artifacts(package, options)["groups"][0]
            _fill_ai(first)
            changed = copy.deepcopy(package)
            first_report = json.loads(Path(first["artifactPaths"]["reconstructionJson"]).read_text(encoding="utf-8"))
            changed["sources"]["logcat"]["content"] += "\n04-12 10:00:04.500 E InputDispatcher no focused window for com.demo"
            for _ in range(2):
                group = build_ai_context_artifacts(changed, options)["groups"][0]
                self.assertEqual(group["id"], first["id"])
                self.assertEqual(group["inputIdentity"], first["inputIdentity"])
                self.assertNotEqual(group["evidenceRevision"], first["evidenceRevision"])
                self.assertEqual(group["analysisRevision"]["evidenceRevision"], first["evidenceRevision"])
                self.assertEqual(group["analysisRevision"]["status"], "stale")
                report = json.loads(Path(group["artifactPaths"]["reconstructionJson"]).read_text(encoding="utf-8"))
                self.assertIn(OLD_AI, Path(group["artifactPaths"]["analysis"]).read_text(encoding="utf-8"))
                self.assertTrue(any(w["code"] == "ai-evidence-revision-changed" and w["message"] for w in report["warnings"]))
                for warning in first_report["warnings"]:
                    self.assertIn(warning, report["warnings"])
                self.assertIn("ai-evidence-revision-changed", Path(group["artifactPaths"]["reconstruction"]).read_text(encoding="utf-8"))
                self.assertEqual(report["analysisRevision"], group["analysisRevision"])

    def test_occupied_hash_directory_is_preserved_and_fallback_id_remains_stable(self) -> None:
        package = _package("B_INPUT")
        identity = build_ai_context(package, AiContextOptions()).groups[0]["inputIdentity"]
        with tempfile.TemporaryDirectory() as tmp:
            options = AiContextOptions(out_dir=tmp)
            build_ai_context_artifacts(_package(), options)
            occupied = Path(tmp) / f"{TIMESTAMP_ID}-{_identity_digest(identity)[:16]}"
            occupied.mkdir()
            (occupied / "anr_analysis.md").write_text(OLD_AI, encoding="utf-8")
            (occupied / "analysis.md").write_text("Legacy manual analysis must remain intact.", encoding="utf-8")
            before = _snapshot(occupied)
            first = build_ai_context_artifacts(package, options)["groups"][0]
            self.assertNotEqual(first["id"], occupied.name)
            _fill_ai(first)
            # Reuse must also work without the latest index as an identity source.
            (Path(tmp) / "index.json").unlink()
            second = build_ai_context_artifacts(package, options)["groups"][0]
            self.assertEqual(first["id"], second["id"])
            self.assertIn(OLD_AI, Path(second["artifactPaths"]["analysis"]).read_text(encoding="utf-8"))
            self.assertEqual(_snapshot(occupied), before)

    def test_conflicting_index_and_reconstruction_identities_do_not_authorize_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            options = AiContextOptions(out_dir=tmp)
            first = build_ai_context_artifacts(_package(), options)["groups"][0]
            _fill_ai(first)
            directory = Path(first["artifactPaths"]["analysis"]).parent
            index_path = Path(tmp) / "index.json"
            index = json.loads(index_path.read_text(encoding="utf-8"))
            index["groups"][0]["inputIdentity"]["packageId"] = "UNRELATED_INPUT"
            index_path.write_text(json.dumps(index), encoding="utf-8")
            before = _snapshot(directory)
            new = build_ai_context_artifacts(_package(), options)["groups"][0]
            self.assertNotEqual(first["id"], new["id"])
            self.assertEqual(_snapshot(directory), before)
            self.assertNotIn(OLD_AI, Path(new["artifactPaths"]["analysis"]).read_text(encoding="utf-8"))

    def test_legacy_workspace_uses_package_and_anchor_not_timestamp_alone(self) -> None:
        for variant in ("same", "different-package-id", "different-package-name", "unknown", "index-only"):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as tmp:
                options = AiContextOptions(out_dir=tmp)
                old = build_ai_context_artifacts(_package(), options)["groups"][0]
                _fill_ai(old)
                directory = Path(old["artifactPaths"]["analysis"]).parent
                Path(old["artifactPaths"]["reconstructionJson"]).unlink()
                index_path = Path(tmp) / "index.json"
                if variant == "index-only":
                    index_path.write_text(json.dumps({"packageId": "A_INPUT", "groups": [{"id": old["id"], "anchor": old["anchor"]}]}), encoding="utf-8")
                    Path(old["artifactPaths"]["analysis"]).write_text(_merge_analyses("<!-- AI_ANALYSIS_SLOT:final-anr -->\n", {"final-anr": OLD_AI}), encoding="utf-8")
                else:
                    index_path.unlink()
                if variant == "unknown":
                    path = Path(old["artifactPaths"]["analysis"])
                    path.write_text(path.read_text(encoding="utf-8").replace("- Package: `A_INPUT`", "- Package: `unknown`"), encoding="utf-8")
                before = _snapshot(directory)
                package = _package("B_INPUT" if variant == "different-package-id" else "A_INPUT", [_anchor("com.other")] if variant == "different-package-name" else None)
                new = build_ai_context_artifacts(package, options)["groups"][0]
                if variant in ("same", "index-only"):
                    self.assertEqual(new["id"], old["id"])
                    self.assertIn(OLD_AI, Path(new["artifactPaths"]["analysis"]).read_text(encoding="utf-8"))
                    report = json.loads(Path(new["artifactPaths"]["reconstructionJson"]).read_text(encoding="utf-8"))
                    self.assertTrue(any(w["code"] == "ai-evidence-revision-unknown" for w in report["warnings"]))
                else:
                    self.assertNotEqual(new["id"], old["id"])
                    self.assertNotIn(OLD_AI, Path(new["artifactPaths"]["analysis"]).read_text(encoding="utf-8"))
                    self.assertEqual(_snapshot(directory), before)


if __name__ == "__main__":
    unittest.main()
