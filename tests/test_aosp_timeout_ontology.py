from __future__ import annotations

from copy import deepcopy
import unittest

from anr_evidence import extract_evidence_package
from anr_evidence.aosp_contract import (
    ANDROID_17_TIMEOUT_KINDS,
    ContractValidationError,
    load_manifest,
    resolve_timeout,
    validate_manifest_data,
)


NO_FOCUS_REASON = "Input dispatching timed out (Application does not have a focused window)."
UNRESPONSIVE_REASON = (
    "Input dispatching timed out (37e4ea9 Taskbar is not responding. "
    "Waited 8000ms for MotionEvent)."
)


def package_with_reason(reason: str, *, source_kind: str = "logcat", **extra: object) -> dict:
    package = {
        "package_id": "aosp-contract",
        "android_release": 17,
        "sources": {
            source_kind: {
                "path": f"{source_kind}.txt",
                "content": f"06-09 13:12:54.321 I AnrManager: Reason: {reason}",
            }
        },
    }
    package.update(extra)
    return package


class AospTimeoutManifestTests(unittest.TestCase):
    def test_manifest_has_complete_auditable_schema_and_b1_kind_coverage(self) -> None:
        manifest = load_manifest()

        self.assertIs(validate_manifest_data(manifest), manifest)
        self.assertEqual(manifest["androidRelease"], 17)
        self.assertEqual(manifest["releaseTag"], "android-17.0.0_r1")
        self.assertEqual(set(manifest["sources"]), {"base", "native"})
        for source in manifest["sources"].values():
            self.assertEqual(
                set(source),
                {"repository", "revision", "path", "sha256", "symbol", "url"},
            )
            self.assertEqual(len(source["revision"]), 40)
            self.assertEqual(len(source["sha256"]), 64)
            self.assertEqual(
                source["url"],
                (
                    f"https://android.googlesource.com/{source['repository']}/+/"
                    f"{source['revision']}/{source['path']}"
                ),
            )

        self.assertEqual(tuple(manifest["kinds"]), ANDROID_17_TIMEOUT_KINDS)
        self.assertEqual(len(manifest["kinds"]), 10)
        self.assertNotIn("FGS_TIMEOUT", manifest["kinds"])
        statuses = [rule["status"] for rule in manifest["kinds"].values()]
        self.assertEqual(statuses.count("mapped"), 2)
        self.assertEqual(statuses.count("unsupported"), 8)
        for rule in manifest["kinds"].values():
            if rule["status"] == "mapped":
                self.assertEqual(rule["sourceRefs"], ["base", "native"])

    def test_manifest_validation_rejects_incomplete_or_conflicting_schema(self) -> None:
        cases = []

        missing_source_field = deepcopy(load_manifest())
        del missing_source_field["sources"]["base"]["sha256"]
        cases.append((missing_source_field, "missing required fields"))

        mutable_revision = deepcopy(load_manifest())
        mutable_revision["sources"]["base"]["revision"] = "android-17.0.0_r1"
        cases.append((mutable_revision, "immutable 40-character"))

        conflicting_pinned_revision = deepcopy(load_manifest())
        conflicting_pinned_revision["sources"]["base"]["revision"] = "0" * 40
        conflicting_pinned_revision["sources"]["base"]["url"] = (
            conflicting_pinned_revision["sources"]["base"]["url"].replace(
                load_manifest()["sources"]["base"]["revision"], "0" * 40
            )
        )
        cases.append((conflicting_pinned_revision, "conflicts with the pinned Android 17 source"))

        missing_kind = deepcopy(load_manifest())
        del missing_kind["kinds"]["APP_START"]
        cases.append((missing_kind, "ten Android 17"))

        extra_fgs = deepcopy(load_manifest())
        extra_fgs["kinds"]["FGS_TIMEOUT"] = {
            "value": 11,
            "status": "unsupported",
            "reason": "not a real Android 17 kind",
        }
        cases.append((extra_fgs, "ten Android 17"))

        unsupported_promoted = deepcopy(load_manifest())
        unsupported_promoted["kinds"]["BROADCAST_RECEIVER"].update(
            status="mapped", trigger="broadcast.receiver", ruleId="invented"
        )
        cases.append((unsupported_promoted, "must remain unsupported"))

        for manifest, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ContractValidationError, message):
                    validate_manifest_data(manifest)

    def test_manifest_validation_rejects_forged_source_url_host(self) -> None:
        manifest = deepcopy(load_manifest())
        manifest["sources"]["base"]["url"] = manifest["sources"]["base"]["url"].replace(
            "android.googlesource.com", "evil.example"
        )

        with self.assertRaisesRegex(ContractValidationError, "canonical Android source URL"):
            validate_manifest_data(manifest)

    def test_manifest_validation_rejects_missing_mapped_source_ref(self) -> None:
        manifest = deepcopy(load_manifest())
        manifest["kinds"]["INPUT_DISPATCH_NO_FOCUSED_WINDOW"]["sourceRefs"] = ["base"]

        with self.assertRaisesRegex(
            ContractValidationError, r"must equal \['base', 'native'\]"
        ):
            validate_manifest_data(manifest)


class AospTimeoutResolutionTests(unittest.TestCase):
    def test_resolves_no_focused_window_reason_from_explicit_anchor(self) -> None:
        result = resolve_timeout(package_with_reason(NO_FOCUS_REASON, source_kind="event_log"))

        self.assertEqual(result["canonicalTrigger"], "input.no_focused_window")
        self.assertEqual(result["timeoutKind"], "INPUT_DISPATCH_NO_FOCUSED_WINDOW")
        self.assertEqual(result["matchedRuleIds"], ["aosp17.input.no_focused_window"])
        self.assertEqual(result["evidenceStatus"], "observed")
        self.assertIsNone(result["directBlocker"])
        self.assertEqual(result["observationEvidenceRefs"][0]["sourceKind"], "event_log")

    def test_resolves_window_unresponsive_reason_from_explicit_anchor(self) -> None:
        result = resolve_timeout(package_with_reason(UNRESPONSIVE_REASON))

        self.assertEqual(result["canonicalTrigger"], "input.window_unresponsive")
        self.assertEqual(result["timeoutKind"], "INPUT_DISPATCH_WINDOW_UNRESPONSIVE")
        self.assertEqual(result["matchedRuleIds"], ["aosp17.input.window_unresponsive"])
        self.assertEqual(result["evidenceStatus"], "observed")
        self.assertIsNone(result["directBlocker"])

    def test_unknown_and_unsupported_releases_fail_closed(self) -> None:
        unknown = package_with_reason(NO_FOCUS_REASON)
        unknown.pop("android_release")
        unsupported = package_with_reason(NO_FOCUS_REASON)
        unsupported["android_release"] = 16

        self.assertEqual(resolve_timeout(unknown)["gaps"], ["android_release_unknown"])
        self.assertEqual(resolve_timeout(unsupported)["gaps"], ["unsupported_android_release"])
        self.assertIsNone(resolve_timeout(unsupported)["profileId"])

    def test_source_revision_conflict_fails_closed(self) -> None:
        revisions = {
            name: source["revision"] for name, source in load_manifest()["sources"].items()
        }
        revisions["native"] = "0" * 40

        result = resolve_timeout(
            package_with_reason(NO_FOCUS_REASON, aosp_source_revisions=revisions)
        )

        self.assertEqual(result["gaps"], ["source_revision_conflict"])
        self.assertIsNone(result["canonicalTrigger"])
        self.assertEqual(result["matchedRuleIds"], [])

    def test_declared_kind_conflict_and_unsupported_kind_fail_closed(self) -> None:
        conflict = resolve_timeout(
            package_with_reason(
                NO_FOCUS_REASON,
                timeout_kind="INPUT_DISPATCH_WINDOW_UNRESPONSIVE",
            )
        )
        unsupported = resolve_timeout(
            package_with_reason(NO_FOCUS_REASON, timeout_kind="BROADCAST_RECEIVER")
        )

        self.assertEqual(conflict["gaps"], ["declared_kind_conflict"])
        self.assertEqual(unsupported["gaps"], ["timeout_kind_unsupported"])
        self.assertIsNone(conflict["timeoutKind"])
        self.assertIsNone(unsupported["timeoutKind"])

    def test_ambiguous_reasons_fail_closed(self) -> None:
        package = package_with_reason(NO_FOCUS_REASON)
        package["sources"]["logcat"]["content"] += (
            f"\n06-09 13:12:55.321 I AnrManager: Reason: {UNRESPONSIVE_REASON}"
        )

        result = resolve_timeout(package)

        self.assertEqual(result["gaps"], ["ambiguous_or_multiple_anr_reasons"])
        self.assertIsNone(result["canonicalTrigger"])
        self.assertEqual(result["observationEvidenceRefs"], [])

    def test_matching_text_without_anr_reason_anchor_is_not_observed(self) -> None:
        package = package_with_reason(NO_FOCUS_REASON)
        package["sources"]["logcat"]["content"] = f"E InputDispatcher: {NO_FOCUS_REASON}"

        result = resolve_timeout(package)

        self.assertEqual(result["gaps"], ["supported_reason_not_observed"])
        self.assertIsNone(result["timeoutKind"])

    def test_vendor_overlay_is_explicitly_unsupported_in_b1(self) -> None:
        result = resolve_timeout(
            package_with_reason(NO_FOCUS_REASON, vendor_overlays={"example": {}})
        )

        self.assertEqual(result["gaps"], ["vendor_overlay_unsupported"])
        self.assertIsNone(result["profileId"])
        self.assertIsNone(result["canonicalTrigger"])

    def test_provided_type_does_not_infer_release_or_change_ontology(self) -> None:
        package = package_with_reason(NO_FOCUS_REASON, provided_type="input_dispatching_timeout")
        package.pop("android_release")

        result = resolve_timeout(package)

        self.assertEqual(result["gaps"], ["android_release_unknown"])
        self.assertIsNone(result["canonicalTrigger"])

    def test_extractor_adds_ontology_without_replacing_legacy_classification(self) -> None:
        package = package_with_reason(
            NO_FOCUS_REASON,
            provided_type="input_dispatching_timeout",
        )
        package["sources"].update(
            trace={"path": "trace.txt", "content": "main tid=1 Blocked"},
            kernel_log={"path": "kernel.txt", "content": "sched normal"},
        )

        classification = extract_evidence_package(package)["classification"]

        self.assertEqual(classification["detectedType"], "input_dispatching_timeout")
        self.assertEqual(classification["triggerType"], "input_dispatching_timeout")
        self.assertTrue(classification["supported"])
        self.assertEqual(classification["confidence"], 1.0)
        ontology = classification["timeoutOntology"]
        self.assertEqual(ontology["canonicalTrigger"], "input.no_focused_window")
        self.assertFalse(ontology["finalJudgment"])
        self.assertTrue(ontology["notRootCauseYet"])
        self.assertTrue(ontology["requiresHumanConfirmation"])


if __name__ == "__main__":
    unittest.main()
