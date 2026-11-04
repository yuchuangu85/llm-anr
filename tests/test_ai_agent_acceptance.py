"""Bounded, fail-closed manager acceptance without network or disk writes."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from anr_evidence.ai_agent import (
    AgentConfig,
    LlmClient,
    LlmRequestError,
    ProviderConfig,
    ProviderKind,
    ReProbeRequest,
    _execute_re_probe,
    _parse_manager_json,
    _parse_re_probe_request,
    run_ai_agent_analysis,
)
from anr_evidence.anr_strategy import ANR_TYPE_STRATEGIES
from anr_evidence.ai_context import AiContextOptions
from anr_evidence.context_flooding import TruncationConfig, truncate_evidence
from anr_evidence.evidence_slice import EvidenceSlice


def _groups():
    return [{
        "id": "anr-one",
        "anchor": {"timestamp": "04-12 10:00:05.000", "line": "04-12 10:00:05.000 I am_anr: observed"},
        "completeness": {"complete": False, "missingSources": ["trace"]},
        "eventLog": {"lines": ["04-12 10:00:05.000 I am_anr: observed"]},
        "logcat": {"lines": ["04-12 10:00:04.000 I custom_probe: detail", "custom_probe: unknown clock"]},
        "reconstruction": {
            "groupId": "anr-one", "anrType": "unknown",
            "events": [{"eventId": "evt-one", "rawLine": "04-12 10:00:05.000 I am_anr: observed"}],
            "evidenceCompleteness": {"complete": False, "missingSources": ["trace"]},
        },
    }]


def _report(group_id="anr-one", reference="evt-one"):
    claim = {
        "groupId": group_id,
        "summary": "Trigger and blocking interval remain unknown; collect the corresponding lifecycle trace.",
        "supportingEventIds": [reference],
        "uncertainties": ["Trigger classification and blocking duration are not observed."],
        "certainty": "inferred", "validation": "unverified", "executed": False,
        "finalJudgment": False, "notRootCauseYet": True, "requiresHumanConfirmation": True,
    }
    return {
        "groupId": group_id,
        "candidateChains": [{
            **deepcopy(claim), "title": "Unresolved trigger", "rank": 1,
            "rationale": "Only the ANR notification is observed; inspect lifecycle evidence.",
            "confidenceLevel": "low", "evidenceRefs": [reference],
            "limitations": ["No blocking stack was collected."],
        }],
        "candidateConclusions": [{
            **deepcopy(claim), "statement": "The blocking mechanism is unknown.",
            "tentative": True, "whyNotFinal": ["Trace missing."],
        }],
        "remediationDrafts": [{
            **deepcopy(claim), "title": "Collect lifecycle evidence",
            "actionDraft": "Capture the target process stack alongside the lifecycle log.",
        }],
        "inputEventAssessments": [],
        "anrModel": [deepcopy(claim)],
        "simulationSteps": [{
            **deepcopy(claim), "preconditions": [], "actions": [],
            "expectedObservations": [], "captureChecks": ["Collect lifecycle and stack evidence."],
            "missingParameters": ["Application entry point and trigger are unknown."],
        }],
        "globalLimitations": ["Missing trace; no cause or device reproduction has been verified."],
        "finalJudgment": False, "notRootCauseYet": True, "requiresHumanConfirmation": True,
        "reProbeRequest": None,
    }


def _probe():
    return {"beforeSeconds": 3, "afterSeconds": 2, "sourceKinds": ["logcat"],
            "additionalTags": ["custom_probe"], "reason": "Inspect missing lifecycle detail."}


def _multi_groups():
    groups = _groups()
    foreign = deepcopy(groups[0])
    foreign["id"] = foreign["reconstruction"]["groupId"] = "anr-two"
    foreign["reconstruction"]["events"] = [{"eventId": "evt-two", "rawLine": "foreign observed"}]
    groups.append(foreign)
    return groups


def _multi_report():
    report = _report()
    report.pop("groupId")
    report["groups"] = [_report(), _report("anr-two", "evt-two")]
    return report


class StrictManagerParsingTests(unittest.TestCase):
    def test_raw_and_fenced_objects_are_supported(self):
        self.assertEqual(_parse_manager_json(' {"a": 1} '), {"a": 1})
        self.assertEqual(_parse_manager_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(_parse_manager_json('```\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(_parse_manager_json('```json {"a": 1} ```'), {"a": 1})

    def test_nonstandard_ambiguous_or_trailing_json_is_rejected(self):
        for text in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', '{"a":1e9999}',
                     '{} trailing', 'prefix {}', '{} {}', '[]', 'null',
                     '```json\n{}\n``` trailing', '```json\n{}\n```\n```json\n{}\n```'):
            with self.subTest(text=text):
                self.assertIsNone(_parse_manager_json(text))


class ProbeValidationTests(unittest.TestCase):
    def test_invalid_probe_fields_are_rejected(self):
        for key, value in (("beforeSeconds", True), ("beforeSeconds", -1),
                           ("afterSeconds", "2"), ("afterSeconds", 1.5),
                           ("sourceKinds", "logcat"), ("sourceKinds", []),
                           ("sourceKinds", ["not-a-source"]), ("additionalTags", [1]),
                           ("additionalTags", "binder"), ("reason", ""), ("reason", 42)):
            probe = _probe()
            probe[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                _parse_re_probe_request({"reProbeRequest": probe})
        for value in (False, [], "probe", 1, {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _parse_re_probe_request({"reProbeRequest": value})

    def test_additional_tags_select_unknown_time_evidence_without_new_dependencies(self):
        request = ReProbeRequest(3, 2, ["logcat"], ["custom_probe"], "collect detail")
        slices = _execute_re_probe(_groups(), request, ANR_TYPE_STRATEGIES["unknown"])
        self.assertEqual(len(slices), 2)
        self.assertTrue(any(item.delta_t_seconds is None for item in slices))
        self.assertTrue(all(item.importance in ("critical", "warning") for item in slices))


class ManagerAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.provider = ProviderConfig(kind=ProviderKind.OPENAI, model="base", api_key="test-key")

    def run_reports(self, reports, *, iterations=None, groups=None, **config):
        responses = []
        for report in reports:
            responses.extend([("{}", 1)] * 3)
            responses.append((report if isinstance(report, str) else json.dumps(report), 2))
        with patch("anr_evidence.ai_agent.build_ai_context", return_value=SimpleNamespace(groups=groups or _groups())), \
             patch.object(LlmClient, "complete_with_tokens", side_effect=responses) as complete:
            result = run_ai_agent_analysis(
                {"package_id": "acceptance-test", "sources": {}}, provider_config=self.provider,
                agent_config=AgentConfig(provider=self.provider, max_iterations=iterations or len(reports), **config),
            )
        return result, complete

    def assert_not_accepted(self, result):
        self.assertNotIn(result.delivery_readiness["status"], {"ready", "ready_with_limitations"})
        self.assertTrue(result.delivery_readiness["blockers"] or result.delivery_readiness["reviewItems"])

    def test_supported_synthetic_candidate_schema_can_be_accepted_with_limitations(self):
        result, complete = self.run_reports([_report()])
        self.assertEqual(result.delivery_readiness["status"], "ready_with_limitations")
        self.assertEqual(result.stop_reason, "accepted")
        self.assertIsNone(result.pending_re_probe)
        self.assertEqual(complete.call_count, 4)
        self.assertEqual(result.total_tokens, 5)

    def test_ready_status_still_does_not_confirm_a_root_cause(self):
        report = _report()
        report["globalLimitations"] = []
        for field in ("candidateChains", "candidateConclusions", "remediationDrafts", "anrModel", "simulationSteps"):
            for claim in report[field]:
                claim["uncertainties"] = []
                if "limitations" in claim:
                    claim["limitations"] = []
        result, _ = self.run_reports([report])
        self.assertEqual(result.delivery_readiness["status"], "ready")
        self.assertFalse(result.delivery_readiness["rootCauseConfirmed"])
        self.assertIs(result.integrated_report["finalJudgment"], False)
        self.assertEqual(result.stop_reason, "accepted")

    def test_runtime_does_not_write_context_or_finalized_artifacts(self):
        package = {"package_id": "memory-only", "provided_type": "unknown", "sources": {
            "event_log": {"content": "04-12 10:00:05.000 I am_anr: [0,1,com.test,1]"},
        }}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(LlmClient, "complete_with_tokens", return_value=("{}", 1)):
            result = run_ai_agent_analysis(
                package, provider_config=self.provider,
                agent_config=AgentConfig(provider=self.provider, max_iterations=1),
                ai_context_options=AiContextOptions(out_dir=directory),
            )
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assert_not_accepted(result)

    def test_empty_objects_retry_with_feedback_and_remain_unsuccessful(self):
        result, complete = self.run_reports([{}, {}])
        self.assert_not_accepted(result)
        self.assertEqual(result.stop_reason, "iteration_limit")
        self.assertEqual(complete.call_count, 8)
        feedback = complete.call_args_list[7].args[0][1].content
        self.assertIn("validation", feedback.lower())
        self.assertIn("candidateChains", feedback)

    def test_invalid_report_can_be_repaired_within_the_iteration_budget(self):
        result, complete = self.run_reports([{}, _report()])
        self.assertEqual(result.stop_reason, "accepted")
        self.assertIn(result.delivery_readiness["status"], {"ready", "ready_with_limitations"})
        self.assertEqual(len(result.iterations), 2)
        self.assertEqual(complete.call_count, 8)

    def test_structurally_invalid_reports_never_succeed(self):
        for field, value in (("candidateChains", {}), ("candidateConclusions", None),
                             ("remediationDrafts", "draft"), ("globalLimitations", 7),
                             ("inputEventAssessments", {}), ("anrModel", []),
                             ("simulationSteps", ["not a candidate"])):
            report = _report()
            report[field] = value
            with self.subTest(field=field):
                result, complete = self.run_reports([report])
                self.assert_not_accepted(result)
                self.assertEqual(result.stop_reason, "iteration_limit")
                self.assertEqual(complete.call_count, 4)

    def test_review_required_report_is_not_accepted_at_iteration_limit(self):
        report = _report()
        report["reviewRequests"] = [{"summary": "Review trigger classification against the current evidence.", "evidenceRefs": ["evt-one"]}]
        result, _ = self.run_reports([report])
        self.assert_not_accepted(result)
        self.assertTrue(result.delivery_readiness["reviewItems"])
        self.assertEqual(result.stop_reason, "iteration_limit")

    def test_deep_json_fails_closed_without_losing_the_raw_manager_turn(self):
        raw = '{"nested":' + '[' * 600 + '0' + ']' * 600 + '}'
        result, _ = self.run_reports([raw])
        self.assert_not_accepted(result)
        self.assertEqual(result.final_turn.response, raw)
        self.assertEqual(result.stop_reason, "iteration_limit")

    def test_missing_or_cross_group_evidence_cannot_pass(self):
        for reference, groups in (("evt-missing", _groups()), ("evt-two", _multi_groups())):
            with self.subTest(reference=reference):
                report = _report(reference=reference) if len(groups) == 1 else _multi_report()
                if len(groups) > 1:
                    report["groups"][0]["candidateChains"][0]["evidenceRefs"] = [reference]
                result, _ = self.run_reports([report], groups=groups)
                self.assert_not_accepted(result)
                self.assertIn("INVALID_EVIDENCE_REF", {item["code"] for item in result.delivery_readiness["blockers"]})
                self.assertEqual(result.stop_reason, "iteration_limit")

    def test_all_scoped_groups_are_normalized_and_accepted_together(self):
        report = _multi_report()
        report["groups"][1]["simulationSteps"][0].update(executed=True, validation="verified", certainty="observed")
        result, _ = self.run_reports([report], groups=_multi_groups())
        self.assertEqual(result.stop_reason, "accepted")
        self.assertIn(result.delivery_readiness["status"], {"ready", "ready_with_limitations"})
        self.assertIs(result.integrated_report["groups"][1]["simulationSteps"][0]["executed"], False)
        self.assertTrue(any("groups[1]" in warning.get("path", "") for warning in result.delivery_readiness["warnings"]))

    def test_nested_last_probe_cannot_be_silently_accepted(self):
        report = _multi_report()
        report["groups"][1]["reProbeRequest"] = _probe()
        with patch("anr_evidence.ai_agent._execute_re_probe") as execute:
            result, _ = self.run_reports([report], groups=_multi_groups())
        self.assert_not_accepted(result)
        self.assertIsNotNone(result.pending_re_probe)
        self.assertEqual(result.stop_reason, "iteration_limit")
        execute.assert_not_called()

    def test_nested_probe_executes_only_in_its_own_group(self):
        report = _multi_report()
        report["groups"][1]["reProbeRequest"] = _probe()
        with patch("anr_evidence.ai_agent._execute_re_probe", wraps=_execute_re_probe) as execute:
            result, _ = self.run_reports([report, _multi_report()], groups=_multi_groups())
        self.assertEqual(result.stop_reason, "accepted")
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(execute.call_args.args[1].group_id, "anr-two")
        self.assertEqual(result.re_probe_requests[0].group_id, "anr-two")

    def test_invalid_last_probe_remains_accessible_and_blocks_delivery(self):
        report = _report()
        report["reProbeRequest"] = {**_probe(), "beforeSeconds": -1}
        result, _ = self.run_reports([report])
        self.assert_not_accepted(result)
        self.assertEqual(result.pending_re_probe, report["reProbeRequest"])
        self.assertEqual(result.stop_reason, "iteration_limit")

    def test_malformed_manager_preserves_previous_partial_report_and_raw_turn(self):
        partial = _report(reference="missing")
        result, _ = self.run_reports([partial, "not JSON"])
        self.assert_not_accepted(result)
        self.assertEqual(result.integrated_report["candidateChains"][0]["evidenceRefs"], ["missing"])
        self.assertEqual(result.final_turn.response, "not JSON")
        self.assertEqual(result.stop_reason, "iteration_limit")

    def test_last_iteration_probe_is_recorded_and_never_executed_or_accepted(self):
        report = _report()
        report["reProbeRequest"] = _probe()
        with patch("anr_evidence.ai_agent._execute_re_probe") as execute:
            result, complete = self.run_reports([report])
        self.assert_not_accepted(result)
        self.assertEqual(result.pending_re_probe, _probe())
        self.assertEqual(len(result.re_probe_requests), 1)
        self.assertEqual(result.stop_reason, "iteration_limit")
        self.assertEqual(complete.call_count, 4)
        execute.assert_not_called()

    def test_repeated_probes_do_not_extend_the_budget(self):
        report = _report()
        report["reProbeRequest"] = _probe()
        with patch("anr_evidence.ai_agent._execute_re_probe", return_value=[]) as execute:
            result, complete = self.run_reports([report] * 3)
        self.assertEqual(result.stop_reason, "iteration_limit")
        self.assertEqual(len(result.re_probe_requests), 3)
        self.assertEqual(execute.call_count, 2)
        self.assertEqual(complete.call_count, 12)
        self.assert_not_accepted(result)

    def test_probe_preserves_baseline_and_reapplies_truncation_before_dispatch(self):
        groups = _groups()
        groups[0]["trace"] = {"lines": ["untimed baseline stack"]}
        report = _report()
        report["reProbeRequest"] = _probe()
        additions = [EvidenceSlice("logcat", None, -index, "custom_probe", f"custom_probe added-{index}", "warning", "anr-one", index + 10)
                     for index in range(1, 4)]
        config = TruncationConfig(max_total_lines=3, max_per_source={"trace": 2, "event_log": 2, "logcat": 1})
        with patch("anr_evidence.ai_agent._execute_re_probe", return_value=additions), \
             patch("anr_evidence.ai_agent.truncate_evidence", wraps=truncate_evidence) as truncate:
            result, complete = self.run_reports([report, _report()], groups=groups, truncation=config)
        self.assertEqual(result.stop_reason, "accepted")
        self.assertEqual(truncate.call_count, 2)
        io_prompt = complete.call_args_list[6].args[0][1].content
        self.assertIn("am_anr: observed", io_prompt)
        self.assertIn("untimed baseline stack", io_prompt)
        self.assertIn("custom_probe added-1", io_prompt)
        self.assertNotIn("custom_probe added-2", io_prompt)
        self.assertNotIn("custom_probe added-3", io_prompt)
        self.assertIsNone(result.pending_re_probe)

    def test_ai_observed_event_is_not_silently_demoted_to_make_report_pass(self):
        report = _report()
        report["events"] = [{"eventId": "forged-event", "rawLine": "AI fabricated observation", "certainty": "observed"}]
        result, _ = self.run_reports([report])
        self.assert_not_accepted(result)
        self.assertEqual(result.integrated_report["events"][0]["certainty"], "observed")
        self.assertIn("AI_EVENT_CLAIMED_OBSERVED", {item["code"] for item in result.delivery_readiness["blockers"]})

    def test_bad_probe_is_validation_feedback_not_an_execution_request(self):
        report = _report()
        report["reProbeRequest"] = {**_probe(), "beforeSeconds": True}
        with patch("anr_evidence.ai_agent._execute_re_probe") as execute:
            result, _ = self.run_reports([report, _report()])
        execute.assert_not_called()
        self.assertEqual(result.stop_reason, "accepted")
        self.assertTrue(any("reProbeRequest" in error for error in result.errors))

    def test_normalization_is_explicit_and_does_not_mutate_observed_evidence(self):
        groups = _groups()
        original = deepcopy(groups)
        report = _report()
        report["finalJudgment"] = True
        report["candidateChains"][0]["notRootCauseYet"] = False
        report["candidateConclusions"][0]["tentative"] = False
        report["simulationSteps"][0].update(certainty="observed", validation="verified", executed=True)
        raw = json.dumps(report)
        result, _ = self.run_reports([raw], groups=groups)
        self.assertEqual(groups, original)
        self.assertEqual(result.final_turn.response, raw)
        self.assertFalse(result.integrated_report["finalJudgment"])
        self.assertTrue(result.integrated_report["candidateChains"][0]["notRootCauseYet"])
        self.assertTrue(result.integrated_report["candidateConclusions"][0]["tentative"])
        normalized = result.integrated_report["simulationSteps"][0]
        self.assertEqual(normalized["certainty"], "inferred")
        self.assertEqual(normalized["validation"], "unverified")
        self.assertIs(normalized["executed"], False)
        self.assertTrue(result.delivery_readiness["warnings"])
        self.assertIn("normaliz", json.dumps(result.delivery_readiness["warnings"]).lower())

    def test_role_model_overrides_are_used_by_real_request_bodies(self):
        bodies = []

        def post(client, url, body, headers):
            bodies.append(body)
            response = json.dumps(_report()) if len(bodies) == 4 else "{}"
            return {"choices": [{"message": {"content": response}}]}

        with patch("anr_evidence.ai_agent.build_ai_context", return_value=SimpleNamespace(groups=_groups())), \
             patch.object(LlmClient, "_post_json", autospec=True, side_effect=post):
            result = run_ai_agent_analysis(
                {"package_id": "models", "sources": {}}, provider_config=self.provider,
                agent_config=AgentConfig(provider=self.provider, max_iterations=1, sub_agent_model="sub", manager_model="manager"),
            )
        self.assertEqual([body["model"] for body in bodies], ["sub", "sub", "sub", "manager"])
        self.assertEqual(result.stop_reason, "accepted")

    def test_failed_transport_keeps_completed_and_failed_turns(self):
        with patch("anr_evidence.ai_agent.build_ai_context", return_value=SimpleNamespace(groups=_groups())), \
             patch.object(LlmClient, "complete_with_tokens", side_effect=[("raw sub response", 7), LlmRequestError("offline")]) as complete:
            result = run_ai_agent_analysis(
                {"sources": {}}, provider_config=self.provider,
                agent_config=AgentConfig(provider=self.provider, max_iterations=3),
            )
        self.assertEqual(result.stop_reason, "transport_error")
        self.assertEqual(len(result.iterations), 1)
        self.assertEqual(result.iterations[0]["cpu_memory"].response, "raw sub response")
        self.assertIsNone(result.iterations[0]["stack_lock"].response)
        self.assertEqual(result.total_tokens, 7)
        self.assertEqual(complete.call_count, 2)
        self.assert_not_accepted(result)

    def test_provider_configuration_failure_is_an_explicit_stop(self):
        with patch("anr_evidence.ai_agent.build_ai_context", return_value=SimpleNamespace(groups=_groups())), \
             patch.object(LlmClient, "complete_with_tokens", side_effect=ValueError("missing provider key")) as complete:
            result = run_ai_agent_analysis(
                {"sources": {}}, provider_config=self.provider,
                agent_config=AgentConfig(provider=self.provider, max_iterations=3),
            )
        self.assertEqual(result.stop_reason, "provider_error")
        self.assertEqual(complete.call_count, 1)
        self.assertEqual(result.iterations[0]["cpu_memory"].error, "missing provider key")
        self.assert_not_accepted(result)

    def test_failed_manager_keeps_partial_report_and_stops_without_retrying_forever(self):
        partial = _report(reference="missing")
        responses = [("{}", 1)] * 3 + [(json.dumps(partial), 2)] + [("{}", 1)] * 3 + [LlmRequestError("offline")]
        with patch("anr_evidence.ai_agent.build_ai_context", return_value=SimpleNamespace(groups=_groups())), \
             patch.object(LlmClient, "complete_with_tokens", side_effect=responses):
            result = run_ai_agent_analysis(
                {"sources": {}}, provider_config=self.provider,
                agent_config=AgentConfig(provider=self.provider, max_iterations=3),
            )
        self.assertEqual(result.stop_reason, "transport_error")
        self.assertEqual(result.integrated_report["candidateChains"][0]["evidenceRefs"], ["missing"])
        self.assertIsNone(result.final_turn.response)
        self.assertEqual(len(result.iterations), 2)
        self.assert_not_accepted(result)

    def test_nonpositive_or_noninteger_iteration_limits_are_rejected(self):
        for limit in (0, -1, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError), \
                 patch("anr_evidence.ai_agent.build_ai_context") as build, \
                 patch.object(LlmClient, "complete_with_tokens", return_value=("{}", 0)):
                run_ai_agent_analysis(
                    {"sources": {}}, provider_config=self.provider,
                    agent_config=AgentConfig(provider=self.provider, max_iterations=limit),
                )
                build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
