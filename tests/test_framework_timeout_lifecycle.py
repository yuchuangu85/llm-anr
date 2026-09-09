from __future__ import annotations

from base64 import b64encode
from copy import deepcopy
import hashlib
import json
import unittest

from anr_evidence.framework_timeout_lifecycle import (
    FrameworkTimeoutPackError,
    build_framework_timeout_lifecycle,
    framework_timeout_pack_recipe,
    retrieve_framework_timeout_sources,
    verify_framework_timeout_pack,
)


ANCHOR = {"packageName": "com.demo"}


def source_blobs(release: int) -> dict[str, dict[str, object]]:
    recipe = framework_timeout_pack_recipe(release)
    required_by_source: dict[str, set[str]] = {source_id: set(source["symbols"]) for source_id, source in recipe["sources"].items()}
    for definition in recipe["kinds"].values():
        for source_id, symbols in definition["requiredSymbols"].items():
            required_by_source[source_id].update(symbols)
    return {
        source_id: {
            **source,
            "content": "\n".join(sorted(required_by_source[source_id])),
            "sha256": hashlib.sha256("\n".join(sorted(required_by_source[source_id])).encode()).hexdigest(),
        }
        for source_id, source in recipe["sources"].items()
    }


def verified_pack(release: int = 17) -> dict[str, object]:
    return verify_framework_timeout_pack(framework_timeout_pack_recipe(release), source_blobs(release))


class FrameworkTimeoutRecipeTests(unittest.TestCase):
    def test_recipes_pin_distinct_commits_without_borrowing_android_17_hash(self) -> None:
        recipes = [framework_timeout_pack_recipe(release) for release in (14, 15, 16, 17)]
        self.assertEqual([recipe["androidRelease"] for recipe in recipes], [14, 15, 16, 17])
        self.assertEqual(len({recipe["frameworksBaseCommit"] for recipe in recipes}), 4)
        self.assertTrue(all(recipe["provenanceStatus"] == "pending_source_acquisition" for recipe in recipes))
        self.assertTrue(all(rule["status"] == "unverified" for recipe in recipes for name, rule in recipe["kinds"].items() if name != "CONTENT_PROVIDER_PUBLISH_TIMEOUT"))

    def test_retrieval_rechecks_tag_and_hashes_exact_gitiles_bytes(self) -> None:
        recipe = framework_timeout_pack_recipe(16)
        blobs = source_blobs(16)
        content_by_url = {
            source["url"] + "?format=TEXT": str(blobs[source_id]["content"])
            for source_id, source in recipe["sources"].items()
        }
        tag_url = "https://android.googlesource.com/platform/frameworks/base/+/refs/tags/android-16.0.0_r1?format=JSON"

        def fetch(url: str) -> bytes:
            if url == tag_url:
                return b")]}'\n" + json.dumps({"commit": recipe["frameworksBaseCommit"]}).encode()
            return b64encode(content_by_url[url].encode())

        retrieved_recipe, sources = retrieve_framework_timeout_sources(16, fetch=fetch)
        self.assertEqual(retrieved_recipe, recipe)
        profile = verify_framework_timeout_pack(retrieved_recipe, sources)
        self.assertEqual(profile["provenanceStatus"], "verified")
        self.assertEqual(profile["kinds"]["BROADCAST_RECEIVER"]["status"], "verified")
        self.assertEqual(len(profile["sources"]["activeServices"]["sha256"]), 64)

    def test_bare_or_metadata_free_content_cannot_verify_provenance(self) -> None:
        recipe = framework_timeout_pack_recipe(17)
        blobs = source_blobs(17)
        blobs["timeoutRecord"] = {"content": str(blobs["timeoutRecord"]["content"])}
        profile = verify_framework_timeout_pack(recipe, blobs)
        self.assertEqual(profile["sources"]["timeoutRecord"]["status"], "invalid")
        self.assertEqual(profile["kinds"]["BROADCAST_RECEIVER"]["status"], "invalid")

    def test_broadcast_pack_accepts_one_verified_modern_or_legacy_route(self) -> None:
        recipe = framework_timeout_pack_recipe(17)
        blobs = source_blobs(17)
        blobs["broadcastLegacy"]["content"] = "BroadcastQueueImpl without timeout route"
        profile = verify_framework_timeout_pack(recipe, blobs)
        self.assertEqual(profile["kinds"]["BROADCAST_RECEIVER"]["status"], "verified")
        self.assertEqual(profile["kinds"]["BROADCAST_RECEIVER"]["verifiedRoutes"], [["broadcastModern"]])

    def test_tag_commit_mismatch_fails_closed(self) -> None:
        recipe = framework_timeout_pack_recipe(14)

        def fetch(_: str) -> bytes:
            return b")]}'\n" + json.dumps({"commit": "0" * 40}).encode()

        with self.assertRaisesRegex(FrameworkTimeoutPackError, "does not resolve"):
            retrieve_framework_timeout_sources(14, fetch=fetch)


class FrameworkTimeoutLifecycleTests(unittest.TestCase):
    def build(self, lines: list[str], *, release: int = 17, pack: dict[str, object] | None = None, kind: str | None = "BROADCAST_RECEIVER") -> dict[str, object]:
        return build_framework_timeout_lifecycle(
            group_id="g", android_release=release, verified_pack=verified_pack(release) if pack is None else pack,
            anchor=ANCHOR, source_lines=lines, timeout_kind=kind,
        )

    def test_trigger_cancelled_lifecycle_is_component_scoped_and_never_root_cause(self) -> None:
        result = self.build([
            "04-12 10:00:01.000 I AMS: timeoutKind=BROADCAST_RECEIVER component=com.demo/.Receiver recordId=br-1 triggered",
            "04-12 10:00:02.000 I AMS: timeoutKind=BROADCAST_RECEIVER component=com.demo/.Receiver recordId=br-1 cancelled",
        ])
        self.assertEqual(result["trigger"]["status"], "observed")
        self.assertEqual(result["lifecycle"]["status"], "cancelled")
        self.assertEqual(result["edges"][0]["causalStatus"], "trigger_only")
        self.assertFalse(result["triggerIsRootCause"])
        self.assertFalse(result["finalJudgment"])
        self.assertTrue(result["notRootCauseYet"])

    def test_each_anr_kind_requires_its_own_verified_route(self) -> None:
        pack = verified_pack(17)
        for kind, definition in pack["kinds"].items():
            if definition["status"] != "verified":
                continue
            with self.subTest(timeout_kind=kind):
                result = self.build([
                    f"timeoutKind={kind} component=com.demo/.Receiver recordId=record-1 triggered",
                ], pack=pack, kind=kind)
                self.assertEqual(result["trigger"]["status"], "observed")
                self.assertEqual(result["edges"][0]["causalStatus"], "trigger_only")

    def test_same_record_recovery_is_not_treated_as_cause(self) -> None:
        result = self.build([
            "04-12 10:00:01.000 I AMS: timeoutKind=BROADCAST_RECEIVER component=com.demo/.Receiver recordId=br-1 triggered",
            "04-12 10:00:02.000 I AMS: timeoutKind=BROADCAST_RECEIVER component=com.demo/.Receiver recordId=br-1 recovered",
        ])
        self.assertEqual(result["lifecycle"]["status"], "recovered")
        self.assertFalse(result["triggerIsRootCause"])

    def test_recovery_needs_same_record_key(self) -> None:
        result = self.build([
            "04-12 10:00:01.000 I AMS: timeoutKind=BROADCAST_RECEIVER component=com.demo/.Receiver recordId=br-1 triggered",
            "04-12 10:00:02.000 I AMS: timeoutKind=BROADCAST_RECEIVER component=com.demo/.Receiver recordId=br-2 recovered",
        ])
        self.assertIn("multiple_framework_timeout_records_unresolved", result["gaps"])
        self.assertEqual(result["edges"], [])

    def test_wrong_component_is_not_reused(self) -> None:
        result = self.build([
            "04-12 10:00:01.000 I AMS: timeoutKind=BROADCAST_RECEIVER component=com.other/.Receiver recordId=br-1 triggered",
        ])
        self.assertEqual(result["edges"], [])
        self.assertIn("timeout_component_not_selected_target", result["gaps"])

    def test_unknown_release_and_unverified_source_remain_unsupported(self) -> None:
        unknown = self.build([
            "timeoutKind=BROADCAST_RECEIVER component=com.demo/.Receiver recordId=br-1 triggered",
        ], release=18, pack={})
        self.assertIn("unsupported_android_release", unknown["gaps"])

        recipe = framework_timeout_pack_recipe(17)
        bad_sources = source_blobs(17)
        bad_sources["timeoutRecord"]["content"] = "not the required source symbol"
        unverified = self.build([
            "timeoutKind=BROADCAST_RECEIVER component=com.demo/.Receiver recordId=br-1 triggered",
        ], pack=verify_framework_timeout_pack(recipe, bad_sources))
        self.assertIn("framework_timeout_pack_provenance_invalid", unverified["gaps"])
        self.assertEqual(unverified["edges"], [])

    def test_mutated_verified_pack_is_not_accepted_by_lifecycle_builder(self) -> None:
        pack = verified_pack(17)
        pack["sources"]["timeoutRecord"]["revision"] = "0" * 40
        result = self.build([
            "timeoutKind=BROADCAST_RECEIVER component=com.demo/.Receiver recordId=br-1 triggered",
        ], pack=pack)
        self.assertEqual(result["edges"], [])
        self.assertIn("framework_timeout_pack_provenance_invalid", result["gaps"])

    def test_non_anr_provider_publish_timeout_never_promotes(self) -> None:
        result = self.build([
            "timeoutKind=CONTENT_PROVIDER_PUBLISH_TIMEOUT component=com.demo/.Provider recordId=provider-1 triggered",
        ], kind="CONTENT_PROVIDER_PUBLISH_TIMEOUT")
        self.assertIn("non_anr_timeout_must_not_promote", result["gaps"])
        self.assertEqual(result["edges"], [])


if __name__ == "__main__":
    unittest.main()


class FrameworkTimeoutAiContextIntegrationTests(unittest.TestCase):
    def test_context_accepts_only_caller_supplied_verified_pack_and_renders_f1(self) -> None:
        from anr_evidence import AiContextOptions, build_ai_context

        package = {
            "package_id": "f1-context",
            "android_release": 17,
            "framework_timeout_pack": verified_pack(17),
            "sources": {
                "event_log": {
                    "path": "events.txt",
                    "content": (
                        "04-12 10:00:05.000 1000 1000 I am_anr  : "
                        "[0,1234,com.demo,1,timeoutKind=BROADCAST_RECEIVER "
                        "component=com.demo/.Receiver recordId=br-1 triggered]"
                    ),
                },
                "trace": {"path": "trace.txt", "content": "04-12 10:00:05.100 ----- pid 1234 -----\nCmd line: com.demo\n"},
                "logcat": {"path": "logcat.txt", "content": ""},
            },
        }
        result = build_ai_context(package, AiContextOptions(package_name="com.demo"))
        lifecycle = result.groups[0]["frameworkTimeoutLifecycle"]
        self.assertEqual(lifecycle["trigger"]["status"], "observed")
        self.assertEqual(lifecycle["edges"][0]["causalStatus"], "trigger_only")
        self.assertEqual(result.summary()["groups"][0]["frameworkTimeoutLifecycle"], lifecycle)
        self.assertIn("framework_timeout_lifecycle_built", [event["step"] for event in result.events])
        self.assertIn("Framework timeout pack observation (F1)", result.cache_markdown)
        self.assertIn("never a root-cause conclusion", result.cache_markdown)
