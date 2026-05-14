"""Tests for the data-driven pattern catalog and evaluator."""

from __future__ import annotations

import unittest

from anr_evidence import (
    MAIN_THREAD_PATTERN_CATALOG,
    evaluate_main_thread_patterns,
    preprocess_trace_content,
)


class CatalogSchemaTests(unittest.TestCase):
    REQUIRED_FIELDS = ("id", "category", "severity", "confidence", "message")
    VALID_SEVERITY = {"info", "warning", "critical"}
    VALID_CONFIDENCE = {"weak", "strong", "critical"}
    VALID_CATEGORY = {"binder", "sp", "io", "gc", "render", "main_block", "system"}

    def test_every_entry_has_required_fields_and_valid_enums(self) -> None:
        seen_ids: set[str] = set()
        for entry in MAIN_THREAD_PATTERN_CATALOG:
            for field in self.REQUIRED_FIELDS:
                self.assertIn(field, entry, f"missing field={field} in {entry.get('id')}")
            self.assertIn(entry["severity"], self.VALID_SEVERITY)
            self.assertIn(entry["confidence"], self.VALID_CONFIDENCE)
            self.assertIn(entry["category"], self.VALID_CATEGORY)
            self.assertNotIn(entry["id"], seen_ids, f"duplicate id={entry['id']}")
            seen_ids.add(entry["id"])
            # At least one match predicate must exist
            self.assertTrue(
                entry.get("anyMatch") or entry.get("allMatch"),
                f"{entry['id']} has no anyMatch/allMatch predicate"
            )

    def test_every_match_string_is_lowercase(self) -> None:
        """Engine lowercases the haystack so needles must be lowercase too."""

        for entry in MAIN_THREAD_PATTERN_CATALOG:
            for field in ("anyMatch", "allMatch", "notMatch"):
                for needle in entry.get(field, ()) or ():
                    self.assertEqual(needle, needle.lower(), f"non-lowercase needle in {entry['id']}: {needle!r}")


class EvaluatorSemanticsTests(unittest.TestCase):
    """Verify allMatch / notMatch composition; uses synthetic catalog records."""

    def _main_thread(self, raw_block: str) -> dict:
        return {"isMainThread": True, "tid": "1", "rawBlock": raw_block, "name": "main"}

    def test_any_match_or_semantics(self) -> None:
        catalog = (
            {"id": "X", "category": "io", "severity": "info", "confidence": "weak",
             "message": "x", "anyMatch": ("aaa", "bbb")},
        )
        thread = self._main_thread("zzz aaa zzz")
        hits = evaluate_main_thread_patterns(thread, "[main]", catalog=catalog)
        self.assertEqual(len(hits), 1)
        thread2 = self._main_thread("zzz nothing zzz")
        self.assertEqual(evaluate_main_thread_patterns(thread2, "[main]", catalog=catalog), [])

    def test_all_match_and_semantics(self) -> None:
        catalog = (
            {"id": "Y", "category": "io", "severity": "info", "confidence": "weak",
             "message": "y", "anyMatch": ("foo",), "allMatch": ("bar", "baz")},
        )
        # foo + bar but no baz -> allMatch fails
        self.assertEqual(
            evaluate_main_thread_patterns(self._main_thread("foo bar"), "[main]", catalog=catalog),
            [],
        )
        # foo + bar + baz -> hit
        hits = evaluate_main_thread_patterns(self._main_thread("foo bar baz"), "[main]", catalog=catalog)
        self.assertEqual(len(hits), 1)

    def test_not_match_negation(self) -> None:
        catalog = (
            {"id": "Z", "category": "io", "severity": "info", "confidence": "weak",
             "message": "z", "anyMatch": ("payload",), "notMatch": ("bad",)},
        )
        # has notMatch needle -> suppressed
        self.assertEqual(
            evaluate_main_thread_patterns(self._main_thread("payload but bad"), "[main]", catalog=catalog),
            [],
        )
        hits = evaluate_main_thread_patterns(self._main_thread("payload only"), "[main]", catalog=catalog)
        self.assertEqual(len(hits), 1)

    def test_no_main_thread_returns_empty(self) -> None:
        self.assertEqual(evaluate_main_thread_patterns(None, "[?]"), [])
        self.assertEqual(evaluate_main_thread_patterns({"isMainThread": True, "rawBlock": ""}, "[?]"), [])

    def test_unknown_fields_in_record_are_ignored(self) -> None:
        catalog = (
            {"id": "W", "category": "io", "severity": "info", "confidence": "weak",
             "message": "w", "anyMatch": ("hit",),
             "schedstatRule": {"runNs": ">=200_000_000"},  # not yet implemented
             "threadStateAny": ["Blocked"],                # not yet implemented
             "weirdFutureField": True},
        )
        hits = evaluate_main_thread_patterns(self._main_thread("hit"), "[main]", catalog=catalog)
        self.assertEqual(len(hits), 1)


class CatalogIntegrationTests(unittest.TestCase):
    def test_catalog_picks_up_provider_pattern_added_in_phase5(self) -> None:
        trace = "\n".join([
            "04-12 10:00:05.100 ----- pid 100 -----",
            "Cmd line: com.demo",
            '"main" prio=5 tid=1 Native',
            '  | sysTid=100',
            '  | state=S schedstat=( 100000000 200000000 50 ) utm=10 stm=10 core=0 HZ=100',
            "  at android.content.ContentResolver.query(ContentResolver.java:550)",
            "  at com.demo.Foo.read(Foo.java:30)",
        ])
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:05.100")
        ids = [h["id"] for h in result["traceHints"]]
        self.assertIn("MAIN_PROVIDER_QUERY", ids)

    def test_catalog_picks_up_webview_pattern(self) -> None:
        trace = "\n".join([
            "04-12 10:00:05.100 ----- pid 100 -----",
            "Cmd line: com.demo",
            '"main" prio=5 tid=1 Native',
            '  | sysTid=100',
            '  | state=S schedstat=( 100000000 200000000 50 ) utm=10 stm=10 core=0 HZ=100',
            "  at android.webkit.WebView.loadUrl(WebView.java:550)",
            "  at com.demo.Foo.open(Foo.java:30)",
        ])
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:05.100")
        ids = [h["id"] for h in result["traceHints"]]
        self.assertIn("MAIN_WEBVIEW_LOAD", ids)


if __name__ == "__main__":
    unittest.main()
