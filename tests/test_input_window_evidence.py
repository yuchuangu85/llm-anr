"""Focused tests for the Input/WMS/SurfaceFlinger evidence digest."""

from __future__ import annotations

import json
import unittest

from anr_evidence.input_window_evidence import build_input_window_digest


ANCHOR = "04-12 10:00:10.000"


class InputWindowEvidenceTests(unittest.TestCase):
    def test_direct_target_trigger_outranks_window_state_and_generic_surface_tag(self) -> None:
        lines = [
            "04-12 10:00:09.500 111 222 W WindowManager: mCurrentFocus=null",
            "04-12 10:00:09.600 111 222 I SurfaceFlinger: transaction committed",
            (
                "04-12 10:00:09.900 321 654 E InputDispatcher: "
                "Input dispatching timed out for com.demo"
            ),
        ]

        digest = build_input_window_digest(
            lines,
            anr_type="input_dispatching_timeout",
            anchor_timestamp=ANCHOR,
            package_name="com.demo",
            target_pid=321,
        )

        self.assertEqual(digest["candidateLineCount"], 2)
        self.assertEqual(digest["items"][0]["family"], "input_trigger")
        self.assertEqual(digest["items"][0]["relevanceScore"], 85)
        self.assertEqual(
            digest["items"][0]["scoreReasons"],
            ["direct_trigger", "target_identity", "anchor_proximity_1s"],
        )

    def test_diversity_preserves_every_present_family(self) -> None:
        lines = [
            (
                f"04-12 10:00:09.{index:03d} E InputDispatcher: "
                f"Input dispatching timed out for com.demo token={index}"
            )
            for index in range(100)
        ]
        lines.extend(
            [
                "04-12 10:00:08.000 W WindowManager: mCurrentFocus=null",
                "04-12 10:00:07.000 E SurfaceFlinger: present fence timed out",
            ]
        )

        digest = build_input_window_digest(
            lines,
            anr_type="input_dispatching_timeout",
            anchor_timestamp=ANCHOR,
            package_name="com.demo",
            max_items=6,
        )

        self.assertEqual(len(digest["items"]), 6)
        self.assertEqual(set(digest["countsByFamily"]), {
            "input_trigger",
            "window_state",
            "surface_state",
        })
        self.assertEqual(digest["countsByFamily"]["input_trigger"], 4)
        self.assertEqual(digest["countsByFamily"]["window_state"], 1)
        self.assertEqual(digest["countsByFamily"]["surface_state"], 1)

    def test_identical_normalized_payload_is_collapsed_without_mutating_source(self) -> None:
        lines = [
            (
                f"04-12 10:00:{second:02d}.000 {100 + second} 222 E InputDispatcher: "
                "Input dispatching timed out for com.demo"
            )
            for second in range(20)
        ] * 10
        original = list(lines)

        digest = build_input_window_digest(
            lines,
            anr_type="input_dispatching_timeout",
            anchor_timestamp=ANCHOR,
            package_name="com.demo",
        )

        self.assertEqual(digest["candidateLineCount"], 200)
        self.assertEqual(digest["duplicateCount"], 199)
        self.assertEqual(len(digest["items"]), 1)
        self.assertEqual(digest["items"][0]["lineIndex"], 10)
        self.assertEqual(lines, original)

    def test_total_and_per_family_caps_are_enforced(self) -> None:
        lines: list[str] = []
        for index in range(20):
            lines.extend(
                [
                    (
                        f"04-12 10:00:09.{index:03d} E InputDispatcher: "
                        f"Input dispatching timed out token={index}"
                    ),
                    (
                        f"04-12 10:00:08.{index:03d} W WindowManager: "
                        f"window={index} is NOT_VISIBLE"
                    ),
                    (
                        f"04-12 10:00:07.{index:03d} E SurfaceFlinger: "
                        f"present fence {index} timed out"
                    ),
                ]
            )

        digest = build_input_window_digest(
            lines,
            anr_type="no_focus_window",
            anchor_timestamp=ANCHOR,
        )

        self.assertEqual(len(digest["items"]), 12)
        self.assertTrue(all(count <= 4 for count in digest["countsByFamily"].values()))
        self.assertEqual(digest["truncatedCount"], 48)

    def test_unparseable_direct_trigger_remains_eligible_without_proximity_bonus(self) -> None:
        digest = build_input_window_digest(
            ["E/InputDispatcher: Input dispatching timed out for com.demo"],
            anr_type="input_dispatching_timeout",
            anchor_timestamp=ANCHOR,
        )

        item = digest["items"][0]
        self.assertEqual(item["relevanceScore"], 50)
        self.assertEqual(item["scoreReasons"], ["direct_trigger"])
        self.assertIsNone(item["timestamp"])
        self.assertIsNone(item["deltaTSeconds"])

    def test_ties_use_anchor_distance_then_line_index_and_are_deterministic(self) -> None:
        lines = [
            "04-12 10:00:08.000 E InputDispatcher: Input dispatching timed out token=first",
            "04-12 10:00:12.000 E InputDispatcher: Input dispatching timed out token=second",
            "04-12 10:00:09.000 E InputDispatcher: Input dispatching timed out token=closest",
        ]

        first = build_input_window_digest(
            lines,
            anr_type="input_dispatching_timeout",
            anchor_timestamp=ANCHOR,
        )
        second = build_input_window_digest(
            lines,
            anr_type="input_dispatching_timeout",
            anchor_timestamp=ANCHOR,
        )

        self.assertEqual(
            [item["lineIndex"] for item in first["items"]],
            [2, 0, 1],
        )
        self.assertEqual(
            json.dumps(first, ensure_ascii=False, sort_keys=True),
            json.dumps(second, ensure_ascii=False, sort_keys=True),
        )

    def test_non_input_type_returns_empty_digest_without_scanning_lines(self) -> None:
        class ExplodingIterable:
            def __iter__(self):
                raise AssertionError("non-input strategy must not scan logcat")

        digest = build_input_window_digest(
            ExplodingIterable(),
            anr_type="service_timeout",
            anchor_timestamp=ANCHOR,
        )

        self.assertEqual(digest["items"], [])
        self.assertEqual(digest["candidateLineCount"], 0)


if __name__ == "__main__":
    unittest.main()
