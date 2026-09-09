from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from anr_evidence.ai_agent import (
    AgentConfig,
    EvidenceSlice,
    ProbeOutcome,
    ProviderConfig,
    ProviderKind,
    ReProbeRequest,
    _filter_re_probe_slices,
    route_sub_agents,
    run_ai_agent_analysis,
)


def provider(*, max_tokens: int = 10) -> ProviderConfig:
    return ProviderConfig(
        kind=ProviderKind.ANTHROPIC,
        model="fixture",
        api_key="test-key",
        outbound_policy="synthetic_only",
        max_tokens=max_tokens,
    )


def safe_manager_report(*, reprobe: dict[str, object] | None = None) -> str:
    return json.dumps({
        "finalJudgment": False,
        "notRootCauseYet": True,
        "requiresHumanConfirmation": True,
        "candidateChains": [],
        "candidateConclusions": [],
        "remediationDrafts": [],
        "globalLimitations": [],
        "reProbeRequest": reprobe,
    })


def anthropic_response(text: str, *, tokens: int = 1) -> bytes:
    return json.dumps({
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": tokens, "output_tokens": 0},
    }).encode("utf-8")


def package_with_all_sources() -> dict[str, object]:
    return {
        "package_id": "router-fixture",
        "provided_type": "input_dispatching_timeout",
        "sources": {
            "trace": {"content": "----- pid 10 -----\nCmd line: com.demo\nmain tid=1\nwaiting for binder reply"},
            "event_log": {"content": "2026-04-12 10:00:00.000 I am_anr: [0,10,com.demo,1,Input dispatching timed out]"},
            "logcat": {"content": "2026-04-12 10:00:01.000 W timeout binder reply"},
        },
    }


def package_with_reprobe_logcat() -> dict[str, object]:
    package = package_with_all_sources()
    package["sources"]["logcat"]["content"] = "\n".join([
        "04-12 10:00:01.000  100  200 W InputDispatcher: Input dispatching timed out; binder reply one",
        "04-12 10:00:02.000  100  200 W InputDispatcher: Input dispatching timed out; binder reply two",
    ])
    return package


class AgentRouterTests(unittest.TestCase):
    def test_router_selects_only_specialist_with_supported_available_source(self) -> None:
        slices = [
            EvidenceSlice(
                source="logcat", timestamp_iso="2026-04-12T10:00:00.000", delta_t_seconds=0,
                tag="timeout", content="Input dispatching timed out", importance="critical", group_id="g", line_index=0,
            ),
        ]
        routes = route_sub_agents(slices, max_agents=3)
        selected = [route.agent_name for route in routes if route.selected]
        skipped = {route.agent_name: route.reason for route in routes if not route.selected}
        self.assertEqual(selected, ["io_binder"])
        self.assertEqual(skipped["cpu_memory"], "no_supported_source")
        self.assertEqual(skipped["stack_lock"], "no_supported_source")
        self.assertEqual(routes[-1].capability_sources, ("logcat",))

    def test_router_records_budget_skip_reason_deterministically(self) -> None:
        slices = [
            EvidenceSlice("trace", None, None, None, "schedstat cpu", "critical", "g", 0),
            EvidenceSlice("event_log", None, None, "am_anr", "am_anr", "critical", "g", 0),
            EvidenceSlice("logcat", None, None, "timeout", "binder timeout", "warning", "g", 0),
        ]
        routes = route_sub_agents(slices, max_agents=1)
        self.assertEqual([route.agent_name for route in routes if route.selected], ["cpu_memory"])
        self.assertEqual(
            {route.agent_name: route.reason for route in routes if not route.selected},
            {"stack_lock": "routing_budget_exhausted", "io_binder": "routing_budget_exhausted"},
        )

    def test_reprobe_additional_tags_are_normalized_and_preserve_critical_anchor(self) -> None:
        slices = [
            EvidenceSlice("logcat", None, 0.0, "binder_reply_wait", "waiting for binder reply", "warning", "g", 0),
            EvidenceSlice("logcat", None, 0.0, "scheduler", "runnable pressure", "warning", "g", 1),
            EvidenceSlice("event_log", None, 0.0, "am_anr", "ANR anchor", "critical", "g", 0),
        ]
        request = ReProbeRequest(3, 3, ["logcat", "event_log"], ["BINDER_REPLY"], "check binder")
        selected = _filter_re_probe_slices(slices, request, normalized_tags=("binder reply",))
        self.assertEqual([slice.content for slice in selected], ["waiting for binder reply", "ANR anchor"])

    def test_empty_reprobe_tags_keep_source_and_window_semantics(self) -> None:
        slices = [
            EvidenceSlice("logcat", None, -1.0, "binder", "inside", "warning", "g", 0),
            EvidenceSlice("logcat", None, 5.0, "binder", "outside", "warning", "g", 1),
            EvidenceSlice("trace", None, 0.0, "binder", "wrong source", "warning", "g", 2),
        ]
        request = ReProbeRequest(2, 2, ["logcat"], [], "baseline narrowing")
        selected = _filter_re_probe_slices(slices, request, normalized_tags=())
        self.assertEqual([slice.content for slice in selected], ["inside"])

    def test_token_budget_rejects_before_http(self) -> None:
        cfg = provider(max_tokens=10)
        with patch("urllib.request.urlopen") as http:
            result = run_ai_agent_analysis(
                {"package_id": "empty", "sources": {}},
                provider_config=cfg,
                agent_config=AgentConfig(provider=cfg, max_iterations=1, max_total_tokens=9),
            )
        http.assert_not_called()
        self.assertEqual(result.integrated_report, {})
        self.assertTrue(any("token budget" in error for error in result.errors))

    def test_invalid_probe_budget_configuration_rejects_before_http(self) -> None:
        cfg = provider(max_tokens=10)
        with patch("urllib.request.urlopen") as http:
            with self.assertRaisesRegex(ValueError, "max_probe_results"):
                run_ai_agent_analysis(
                    package_with_all_sources(),
                    provider_config=cfg,
                    agent_config=AgentConfig(provider=cfg, max_probe_results=0),
                )
        http.assert_not_called()

    def test_reprobe_result_budget_stops_without_a_second_remote_iteration(self) -> None:
        cfg = provider(max_tokens=10)
        reprobe = {
            "beforeSeconds": 3,
            "afterSeconds": 3,
            "sourceKinds": ["event_log", "logcat"],
            "additionalTags": [],
            "reason": "inspect the narrow evidence set",
        }
        responses = [
            anthropic_response("{}"),
            anthropic_response("{}"),
            anthropic_response("{}"),
            anthropic_response(safe_manager_report(reprobe=reprobe)),
        ]

        def urlopen(*_args, **_kwargs):
            response = MagicMock()
            response.__enter__.return_value = response
            response.__exit__.return_value = False
            response.read.return_value = responses.pop(0)
            return response

        with patch("urllib.request.urlopen", side_effect=urlopen) as http:
            result = run_ai_agent_analysis(
                package_with_reprobe_logcat(),
                provider_config=cfg,
                agent_config=AgentConfig(
                    provider=cfg,
                    max_iterations=2,
                    max_probe_results=1,
                    max_total_tokens=80,
                ),
            )
        self.assertEqual(http.call_count, 4)
        self.assertEqual(result.integrated_report, {})
        self.assertEqual(result.probe_outcomes[0].status, "probe_result_budget_exceeded")
        self.assertGreater(result.probe_outcomes[0].result_slice_count, 1)

    def test_reprobe_wall_time_budget_stops_without_a_second_remote_iteration(self) -> None:
        cfg = provider(max_tokens=10)
        reprobe = {
            "beforeSeconds": 3,
            "afterSeconds": 3,
            "sourceKinds": ["logcat"],
            "additionalTags": [],
            "reason": "measure the bounded local filter",
        }
        responses = [
            anthropic_response("{}"),
            anthropic_response("{}"),
            anthropic_response("{}"),
            anthropic_response(safe_manager_report(reprobe=reprobe)),
        ]

        def urlopen(*_args, **_kwargs):
            response = MagicMock()
            response.__enter__.return_value = response
            response.__exit__.return_value = False
            response.read.return_value = responses.pop(0)
            return response

        with (
            patch("urllib.request.urlopen", side_effect=urlopen) as http,
            patch("anr_evidence.ai_agent.time.monotonic", side_effect=(100.0, 100.02)),
        ):
            result = run_ai_agent_analysis(
                package_with_all_sources(),
                provider_config=cfg,
                agent_config=AgentConfig(
                    provider=cfg,
                    max_iterations=2,
                    max_probe_wall_seconds=0.01,
                    max_total_tokens=80,
                ),
            )
        self.assertEqual(http.call_count, 4)
        self.assertEqual(result.integrated_report, {})
        self.assertEqual(result.probe_outcomes[0].status, "probe_wall_time_exceeded")
        self.assertGreater(result.probe_outcomes[0].elapsed_ms, 10)

    def test_reprobe_count_budget_rejects_second_request(self) -> None:
        cfg = provider(max_tokens=10)
        reprobe = {
            "beforeSeconds": 3,
            "afterSeconds": 3,
            "sourceKinds": ["logcat"],
            "additionalTags": [],
            "reason": "request one more narrow view",
        }
        responses = [
            anthropic_response("{}"),
            anthropic_response("{}"),
            anthropic_response("{}"),
            anthropic_response(safe_manager_report(reprobe=reprobe)),
            anthropic_response("{}"),
            anthropic_response(safe_manager_report(reprobe=reprobe)),
        ]

        def urlopen(*_args, **_kwargs):
            response = MagicMock()
            response.__enter__.return_value = response
            response.__exit__.return_value = False
            response.read.return_value = responses.pop(0)
            return response

        with patch("urllib.request.urlopen", side_effect=urlopen) as http:
            result = run_ai_agent_analysis(
                package_with_reprobe_logcat(),
                provider_config=cfg,
                agent_config=AgentConfig(
                    provider=cfg,
                    max_iterations=3,
                    max_re_probe_requests=1,
                    max_total_tokens=80,
                ),
            )
        self.assertEqual(http.call_count, 6)
        self.assertEqual(result.integrated_report, {})
        self.assertEqual(
            [outcome.status for outcome in result.probe_outcomes],
            ["executed", "probe_count_budget_exceeded"],
        )

    def test_no_matching_reprobe_stops_without_a_second_remote_iteration(self) -> None:
        cfg = provider(max_tokens=10)
        reprobe = {
            "beforeSeconds": 3,
            "afterSeconds": 3,
            "sourceKinds": ["logcat"],
            "additionalTags": ["nonexistent_probe_tag"],
            "reason": "look for counterevidence",
        }
        responses = [
            anthropic_response("{}"),
            anthropic_response("{}"),
            anthropic_response("{}"),
            anthropic_response(safe_manager_report(reprobe=reprobe)),
        ]

        def urlopen(*_args, **_kwargs):
            response = MagicMock()
            response.__enter__.return_value = response
            response.__exit__.return_value = False
            response.read.return_value = responses.pop(0)
            return response

        with patch("urllib.request.urlopen", side_effect=urlopen) as http:
            result = run_ai_agent_analysis(
                package_with_all_sources(),
                provider_config=cfg,
                agent_config=AgentConfig(provider=cfg, max_iterations=2, max_total_tokens=80),
            )
        self.assertEqual(http.call_count, 4)
        self.assertEqual(result.integrated_report, {})
        self.assertIsNone(result.final_turn)
        self.assertTrue(result.probe_outcomes)
        self.assertIsInstance(result.probe_outcomes[0], ProbeOutcome)
        self.assertEqual(result.probe_outcomes[0].status, "no_results")
        self.assertTrue(any("no qualifying evidence" in error for error in result.errors))


if __name__ == "__main__":
    unittest.main()

class AgentRouterIntegrationTests(unittest.TestCase):
    def test_matching_reprobe_changes_second_round_specialist_prompt(self) -> None:
        cfg = provider(max_tokens=10)
        package = package_with_all_sources()
        package["sources"]["logcat"]["content"] = "\n".join([
            "04-12 10:00:01.000  100  200 W InputDispatcher: Input dispatching timed out; binder reply stalled",
            "04-12 10:00:02.000  100  200 W InputDispatcher: Input dispatching timed out; unrelated scheduler sample",
        ])
        reprobe = {
            "beforeSeconds": 3,
            "afterSeconds": 3,
            "sourceKinds": ["logcat"],
            "additionalTags": ["binder_reply"],
            "reason": "inspect the binder reply",
        }
        responses = [
            anthropic_response("{}"),
            anthropic_response("{}"),
            anthropic_response("{}"),
            anthropic_response(safe_manager_report(reprobe=reprobe)),
            anthropic_response("{}"),
            anthropic_response(safe_manager_report()),
        ]
        requests = []

        def urlopen(request, **_kwargs):
            requests.append(request)
            response = MagicMock()
            response.__enter__.return_value = response
            response.__exit__.return_value = False
            response.read.return_value = responses.pop(0)
            return response

        with patch("urllib.request.urlopen", side_effect=urlopen) as http:
            result = run_ai_agent_analysis(
                package,
                provider_config=cfg,
                agent_config=AgentConfig(provider=cfg, max_iterations=2, max_total_tokens=80),
            )
        self.assertEqual(http.call_count, 6)
        self.assertEqual(result.probe_outcomes[0].status, "executed")
        self.assertEqual(result.probe_outcomes[0].additional_tags, ("binder reply",))
        second_round_specialist_prompt = json.loads(requests[4].data)["messages"][-1]["content"]
        self.assertIn("binder reply stalled", second_round_specialist_prompt)
        self.assertNotIn("unrelated scheduler sample", second_round_specialist_prompt)
        self.assertEqual(result.validation_status, "structure_only")
