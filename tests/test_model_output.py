import copy
import math
import unittest

from anr_evidence.model_output import validate_manager_report


def valid_report():
    return {
        "candidateChains": [
            {
                "title": "Binder wait",
                "rank": 1,
                "score": 72.5,
                "confidenceLevel": "medium",
                "signalCategory": "binder_or_ipc_pressure",
                "rationale": "Correlated signals",
                "limitations": ["No transaction trace"],
                "evidenceRefs": ["trace:12"],
                "notRootCauseYet": True,
            }
        ],
        "candidateConclusions": [
            {
                "rank": 1,
                "confidenceLevel": "low",
                "statement": "Binder pressure may contribute",
                "signalCategory": "binder_or_ipc_pressure",
                "whyNotFinal": ["Peer is unknown"],
                "unresolvedQuestions": ["Which transaction blocked?"],
                "tentative": True,
            }
        ],
        "remediationDrafts": [
            {
                "rank": 1,
                "priority": 80,
                "title": "Capture binder evidence",
                "actionDraft": "Collect a bounded trace",
                "requiresHumanConfirmation": True,
            }
        ],
        "globalLimitations": ["Evidence is incomplete"],
        "reProbeRequest": None,
        "finalJudgment": False,
        "notRootCauseYet": True,
        "requiresHumanConfirmation": True,
    }


class ValidateManagerReportTests(unittest.TestCase):
    def assert_invalid(self, report, text):
        errors = validate_manager_report(report)
        self.assertTrue(errors)
        self.assertTrue(any(text in error for error in errors), errors)

    def test_accepts_complete_conservative_report(self):
        self.assertEqual(validate_manager_report(valid_report()), [])

    def test_accepts_empty_candidate_lists_and_bounded_reprobe(self):
        report = valid_report()
        report["candidateChains"] = []
        report["candidateConclusions"] = []
        report["remediationDrafts"] = []
        report["reProbeRequest"] = {
            "beforeSeconds": 0,
            "afterSeconds": 300,
            "sourceKinds": ["trace", "event_log", "logcat", "kernel_log"],
            "additionalTags": ["binder_reply"],
            "reason": "Narrow the evidence window",
        }
        self.assertEqual(validate_manager_report(report), [])

    def test_requires_dict_and_all_top_level_fields(self):
        self.assert_invalid([], "$ must be an object")
        report = valid_report()
        del report["candidateChains"]
        self.assert_invalid(report, "$.candidateChains is required")

    def test_rejects_unknown_fields_at_each_schema_level(self):
        report = valid_report()
        report["command"] = "delete"
        self.assert_invalid(report, "$.command is not supported")

        report = valid_report()
        report["candidateChains"][0]["toolCall"] = {}
        self.assert_invalid(report, "$.candidateChains[0].toolCall is not supported")

        report = valid_report()
        report["reProbeRequest"] = {
            "beforeSeconds": 3,
            "afterSeconds": 3,
            "sourceKinds": ["logcat"],
            "additionalTags": [],
            "reason": "more context",
            "shell": "rm -rf /",
        }
        self.assert_invalid(report, "$.reProbeRequest.shell is not supported")

    def test_rejects_wrong_list_item_and_scalar_types(self):
        report = valid_report()
        report["candidateChains"] = {}
        self.assert_invalid(report, "$.candidateChains must be an array")

        report = valid_report()
        report["candidateConclusions"][0]["whyNotFinal"] = [1]
        self.assert_invalid(report, "$.candidateConclusions[0].whyNotFinal[0] must be a string")

        report = valid_report()
        report["candidateChains"][0]["rank"] = True
        self.assert_invalid(report, "$.candidateChains[0].rank must be a positive integer")

    def test_rejects_out_of_range_or_nonfinite_numbers(self):
        for value in (-1, 101, 10 ** 1000, math.inf, math.nan, True):
            with self.subTest(score=value):
                report = valid_report()
                report["candidateChains"][0]["score"] = value
                self.assert_invalid(report, "score must be a finite number between 0 and 100")

        for value in (0, 101, 1.5, True):
            with self.subTest(priority=value):
                report = valid_report()
                report["remediationDrafts"][0]["priority"] = value
                self.assert_invalid(report, "priority must be an integer between 1 and 100")

    def test_rejects_unknown_confidence(self):
        report = valid_report()
        for value in ("certain", [], {}, True):
            report["candidateChains"][0]["confidenceLevel"] = value
            self.assert_invalid(report, "confidenceLevel must be one of")

    def test_enforces_conservative_safety_values_recursively(self):
        for key, unsafe in (
            ("finalJudgment", True),
            ("notRootCauseYet", False),
            ("requiresHumanConfirmation", False),
            ("tentative", False),
        ):
            with self.subTest(key=key):
                report = valid_report()
                report["candidateChains"][0]["unsupported"] = {key: unsafe}
                self.assert_invalid(report, f".{key} must be")

    def test_deep_unknown_payload_is_rejected_without_recursion_failure(self):
        report = valid_report()
        current = report
        for _ in range(1200):
            current["nested"] = {}
            current = current["nested"]
        self.assert_invalid(report, "depth")

    def test_validates_reprobe_exact_shape_ranges_and_source_kinds(self):
        base = {
            "beforeSeconds": 3,
            "afterSeconds": 3,
            "sourceKinds": ["logcat"],
            "additionalTags": ["binder_reply"],
            "reason": "Validate binder evidence",
        }
        mutations = (
            ("beforeSeconds", -1, "beforeSeconds must be an integer between 0 and 300"),
            ("afterSeconds", 301, "afterSeconds must be an integer between 0 and 300"),
            ("sourceKinds", ["meminfo"], "sourceKinds[0] must be one of"),
            ("additionalTags", [False], "additionalTags[0] must be a string"),
            ("reason", 7, "reason must be a string"),
        )
        for key, value, expected in mutations:
            with self.subTest(key=key):
                report = valid_report()
                request = copy.deepcopy(base)
                request[key] = value
                report["reProbeRequest"] = request
                self.assert_invalid(report, expected)

        report = valid_report()
        request = copy.deepcopy(base)
        del request["reason"]
        report["reProbeRequest"] = request
        self.assert_invalid(report, "$.reProbeRequest.reason is required")


    def test_reprobe_rejects_empty_duplicate_or_malformed_probe_parameters(self) -> None:
        cases = (
            ("sourceKinds", [], "sourceKinds must not be empty"),
            ("sourceKinds", ["logcat", "logcat"], "sourceKinds must not contain duplicates"),
            ("additionalTags", ["binder_reply"] * 9, "additionalTags must contain at most 8 tags"),
            ("additionalTags", ["bad\nvalue"], "must be an ASCII evidence tag"),
            ("additionalTags", ["binder_reply", "binder reply"], "must not contain equivalent duplicates"),
        )
        for field, value, expected in cases:
            with self.subTest(field=field, value=value):
                report = valid_report()
                report["reProbeRequest"] = {
                    "beforeSeconds": 3,
                    "afterSeconds": 3,
                    "sourceKinds": ["logcat"],
                    "additionalTags": [],
                    "reason": "fixture",
                }
                report["reProbeRequest"][field] = value
                self.assert_invalid(report, expected)


if __name__ == "__main__":
    unittest.main()
