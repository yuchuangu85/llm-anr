"""Tests for anr_evidence.ai_agent.

Uses mock HTTP responses to avoid real API calls.
"""

from __future__ import annotations

import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from anr_evidence.ai_agent import (
    AgentConfig,
    AgentMessage,
    AgentTurn,
    LlmClient,
    LlmRequestError,
    ProviderConfig,
    ProviderKind,
    ReProbeRequest,
    _build_sub_agent_prompt,
    _focus_slices_for_sub_agent,
    _parse_manager_json,
    _parse_re_probe_request,
    SUB_AGENT_SPECS,
    run_ai_agent_analysis,
)
from anr_evidence.evidence_slice import EvidenceSlice, annotate_slices_with_tags, build_evidence_slices
from anr_evidence.ai_context import AiContextOptions, build_ai_context


class _MockHTTPError(urllib.error.HTTPError):
    def __init__(self, code: int):
        Exception.__init__(self, f"HTTP Error {code}")
        self.code = code
        self.url = "https://example.test"
        self.filename = self.url
        self.hdrs = None
        self.headers = None
        self.fp = None


class LlmClientTests(unittest.TestCase):
    def _mock_http_response(self, payload: dict) -> MagicMock:
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read = MagicMock(return_value=json.dumps(payload).encode("utf-8"))
        return mock_resp

    def _http_error(self, code: int) -> urllib.error.HTTPError:
        return _MockHTTPError(code)

    def test_parse_manager_json_with_fences(self) -> None:
        text = '```json\n{"candidateChains": [], "globalLimitations": ["test"]}\n```'
        result = _parse_manager_json(text)
        self.assertIsNotNone(result)
        self.assertEqual(result["globalLimitations"], ["test"])

    def test_parse_manager_json_raw(self) -> None:
        text = '{"candidateChains": [], "candidateConclusions": [{"rank": 1}]}'
        result = _parse_manager_json(text)
        self.assertIsNotNone(result)
        self.assertEqual(len(result["candidateConclusions"]), 1)

    def test_parse_manager_json_empty(self) -> None:
        self.assertIsNone(_parse_manager_json(""))
        self.assertIsNone(_parse_manager_json("no json here"))

    def test_parse_re_probe_request(self) -> None:
        data = {
            "reProbeRequest": {
                "beforeSeconds": 3,
                "afterSeconds": 2,
                "sourceKinds": ["logcat"],
                "additionalTags": ["binder"],
                "reason": "need more detail",
            }
        }
        req = _parse_re_probe_request(data)
        self.assertIsNotNone(req)
        if req:
            self.assertEqual(req.before_seconds, 3)
            self.assertEqual(req.after_seconds, 2)
            self.assertEqual(req.source_kinds, ["logcat"])
            self.assertEqual(req.reason, "need more detail")

    def test_parse_re_probe_request_none(self) -> None:
        self.assertIsNone(_parse_re_probe_request({"reProbeRequest": None}))
        self.assertIsNone(_parse_re_probe_request({}))

    def test_post_json_retries_retryable_http_error_and_passes_timeout(self) -> None:
        config = ProviderConfig(outbound_policy="synthetic_only", kind=ProviderKind.OPENAI, model="test", api_key="key", timeout_seconds=3.5, max_retries=1)
        client = LlmClient(config)
        success = self._mock_http_response({"ok": True})

        with patch("time.sleep") as sleep_mock, patch("urllib.request.urlopen") as urlopen_mock:
            urlopen_mock.side_effect = [self._http_error(500), success]
            result = client._post_json("https://example.test", {"hello": "world"}, {"content-type": "application/json"})

        self.assertEqual(result, {"ok": True})
        self.assertEqual(urlopen_mock.call_count, 2)
        self.assertEqual(urlopen_mock.call_args_list[0].kwargs["timeout"], 3.5)
        sleep_mock.assert_called_once_with(1)

    def test_post_json_does_not_retry_non_retryable_http_error(self) -> None:
        config = ProviderConfig(outbound_policy="synthetic_only", kind=ProviderKind.OPENAI, model="test", api_key="key", max_retries=3)
        client = LlmClient(config)

        with patch("time.sleep") as sleep_mock, patch("urllib.request.urlopen") as urlopen_mock:
            urlopen_mock.side_effect = self._http_error(400)
            with self.assertRaises(LlmRequestError):
                client._post_json("https://example.test", {}, {})

        self.assertEqual(urlopen_mock.call_count, 1)
        sleep_mock.assert_not_called()

    def test_post_json_retries_url_error_until_exhausted(self) -> None:
        config = ProviderConfig(outbound_policy="synthetic_only", kind=ProviderKind.OPENAI, model="test", api_key="key", max_retries=2)
        client = LlmClient(config)

        with patch("time.sleep") as sleep_mock, patch("urllib.request.urlopen") as urlopen_mock:
            urlopen_mock.side_effect = urllib.error.URLError("network down")
            with self.assertRaises(LlmRequestError):
                client._post_json("https://example.test", {}, {})

        self.assertEqual(urlopen_mock.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep_mock.call_args_list], [1, 2])


class SubAgentDispatchTests(unittest.TestCase):
    def setUp(self):
        self.slices = [
            EvidenceSlice(
                source="trace", timestamp_iso="2026-04-12T10:00:04.000",
                delta_t_seconds=-1.0, tag="binder", content="binder tid=3",
                importance="critical", group_id="g1", line_index=0,
            ),
            EvidenceSlice(
                source="event_log", timestamp_iso="2026-04-12T10:00:03.000",
                delta_t_seconds=-2.0, tag="am_anr", content="I am_anr: trigger",
                importance="critical", group_id="g1", line_index=0,
            ),
            EvidenceSlice(
                source="logcat", timestamp_iso="2026-04-12T10:00:06.000",
                delta_t_seconds=1.0, tag="timeout", content="W timeout",
                importance="warning", group_id="g1", line_index=0,
            ),
        ]

    def test_focus_slices_for_cpu_memory(self) -> None:
        spec = SUB_AGENT_SPECS["cpu_memory"]
        focused = _focus_slices_for_sub_agent(self.slices, spec)
        sources = {s.source for s in focused}
        self.assertIn("trace", sources)
        self.assertIn("event_log", sources)

    def test_focus_slices_for_stack_lock(self) -> None:
        spec = SUB_AGENT_SPECS["stack_lock"]
        focused = _focus_slices_for_sub_agent(self.slices, spec)
        # stack_lock only cares about trace
        self.assertTrue(all(s.source == "trace" for s in focused))

    def test_focus_slices_for_io_binder(self) -> None:
        spec = SUB_AGENT_SPECS["io_binder"]
        focused = _focus_slices_for_sub_agent(self.slices, spec)
        self.assertGreater(len(focused), 0)

    def test_build_sub_agent_prompt(self) -> None:
        spec = SUB_AGENT_SPECS["cpu_memory"]
        prompt = _build_sub_agent_prompt(spec, self.slices, None)
        self.assertIn("CPU/Memory", prompt)
        self.assertIn("binder tid=3", prompt)
        self.assertIn("am_anr: trigger", prompt)

    def test_build_sub_agent_prompt_with_entity_map(self) -> None:
        from anr_evidence.entity_linker import EntityMap

        em = EntityMap(
            package_id="test", process_name="com.foo",
            pids=frozenset(["1234"]), tids=frozenset(["1"]), uids=frozenset(),
        )
        spec = SUB_AGENT_SPECS["io_binder"]
        prompt = _build_sub_agent_prompt(spec, self.slices, em)
        self.assertIn("Entity Map", prompt)
        self.assertIn("com.foo", prompt)


class AgentIntegrationTests(unittest.TestCase):
    def test_ambiguous_group_ids_fail_before_sending_any_prompt(self) -> None:
        package = {"provided_type": "input_dispatching_timeout", "sources": {
            "event_log": {"content": "\n".join([
                "2026-04-12 10:00:05.000 I am_anr: [0,456,com.first,1,Input dispatching timed out]",
                "2026-04-12 10:00:05.000 I am_anr: [0,789,com.second,1,Input dispatching timed out]",
            ])},
        }}
        response = json.dumps({"content": [{"type": "text", "text": self.MOCK_MANAGER_RESPONSE}]}).encode()
        config = ProviderConfig(outbound_policy="synthetic_only", kind=ProviderKind.ANTHROPIC, model="test", api_key="test-key")
        duplicate_groups = [{"id": "duplicate"}, {"id": "duplicate"}]
        with patch("anr_evidence.ai_agent.build_ai_context", return_value=MagicMock(groups=duplicate_groups)), \
                patch("anr_evidence.ai_agent.build_evidence_slices", return_value=[]), \
                patch("urllib.request.urlopen", side_effect=self._make_mock_urlopen([response])) as http:
            with self.assertRaisesRegex(ValueError, "duplicate ANR group"):
                run_ai_agent_analysis(package, provider_config=config,
                                      agent_config=AgentConfig(provider=config, max_iterations=1))
        http.assert_not_called()

    def test_same_time_groups_reach_http_with_unique_ids(self) -> None:
        package = {"sources": {"event_log": {"content": "\n".join([
            "2026-04-12 10:00:05.000 I am_anr: [0,456,com.first,1,Input dispatching timed out]",
            "2026-04-12 10:00:05.000 I am_anr: [0,789,com.second,1,Input dispatching timed out]",
        ])}}}
        ids = [g["id"] for g in build_ai_context(package, AiContextOptions()).groups]
        self.assertEqual(len(set(ids)), 2)
        response = json.dumps({"content": [{"type": "text", "text": self.MOCK_MANAGER_RESPONSE}]}).encode()
        cfg = ProviderConfig(kind=ProviderKind.ANTHROPIC, model="test", api_key="test-key",
                             outbound_policy="synthetic_only")
        with patch("urllib.request.urlopen", side_effect=self._make_mock_urlopen([response])) as http:
            result = run_ai_agent_analysis(package, provider_config=cfg,
                                           agent_config=AgentConfig(provider=cfg, max_iterations=1))
        self.assertEqual(result.errors, [])
        self.assertEqual(http.call_count, 4)
        for call in http.call_args_list:
            prompt = json.loads(call.args[0].data)["messages"][-1]["content"]
            for group_id in ids:
                self.assertIn(f"## Entity Map — {group_id}", prompt)

    def test_actual_provider_prompts_keep_selected_entity_maps_per_anr(self) -> None:
        trace_lines = []
        for timestamp, pid, tid, process in (
            ("09:00:00", 123, 321, "com.other"),
            ("10:00:05", 456, 654, "com.first"),
            ("10:01:05", 789, 987, "com.second"),
        ):
            trace_lines.extend([
                f"----- pid {pid} at 2026-04-12 {timestamp} -----",
                f"Cmd line: {process}", '"main" prio=5 tid=1 Native',
                f" | sysTid={tid}", " at example.Work.run(Work.java:1)",
            ])
        package = {"provided_type": "input_dispatching_timeout", "sources": {
            "trace": {"content": "\n".join(trace_lines)},
            "event_log": {"content": "\n".join([
                "2026-04-12 10:00:05.000 I am_anr: [0,456,com.first,1,Input dispatching timed out] uid=101",
                "2026-04-12 10:01:05.000 I am_anr: [0,789,com.second,1,Input dispatching timed out] uid=202",
            ])},
        }}
        response = json.dumps({"content": [{"type": "text", "text": self.MOCK_MANAGER_RESPONSE}]}).encode()
        config = ProviderConfig(outbound_policy="synthetic_only", kind=ProviderKind.ANTHROPIC, model="test", api_key="test-key")
        with patch("urllib.request.urlopen", side_effect=self._make_mock_urlopen([response])) as http:
            run_ai_agent_analysis(package, provider_config=config,
                                  agent_config=AgentConfig(provider=config, max_iterations=1))
        self.assertEqual(http.call_count, 4)
        for call in http.call_args_list:
            prompt = json.loads(call.args[0].data)["messages"][-1]["content"]
            for group, process, pid, tid, uid, other_pid in (
                ("anr-20260412-100005-000", "com.first", "456", "654", "101", "789"),
                ("anr-20260412-100105-000", "com.second", "789", "987", "202", "456"),
            ):
                heading = f"## Entity Map — {group}\n"
                self.assertIn(heading, prompt)
                section = prompt.split(heading, 1)[1].split("\n## ", 1)[0]
                self.assertIn(f"- Process: `{process}`", section)
                self.assertIn(f"- PIDs: ['{pid}']", section)
                self.assertIn(f"- TIDs: ['{tid}']", section)
                self.assertIn(f"- UIDs: ['{uid}']", section)
                self.assertNotIn(other_pid, section)
                self.assertNotIn("com.other", section)

    MOCK_CPU_RESPONSE = json.dumps({
        "findings": [{"category": "memory_pressure", "confidence": "medium", "summary": "moderate memory pressure", "anr_relevance": "may amplify blocking"}],
        "overall_assessment": "moderate pressure"
    })
    MOCK_STACK_RESPONSE = json.dumps({
        "findings": [{"category": "lock_contention", "confidence": "high", "summary": "main waiting on lock held by pid 42", "anr_relevance": "direct cause"}],
        "overall_assessment": "lock contention is primary"
    })
    MOCK_IO_RESPONSE = json.dumps({
        "findings": [{"category": "binder_reply_wait", "confidence": "medium", "summary": "binder thread pool saturated", "anr_relevance": "secondary factor"}],
        "overall_assessment": "binder contributing"
    })
    MOCK_MANAGER_RESPONSE = json.dumps({
        "finalJudgment": False, "notRootCauseYet": True, "requiresHumanConfirmation": True,
        "candidateChains": [
            {"title": "锁竞争导致ANR", "rank": 1, "score": 85, "confidenceLevel": "high",
             "signalCategory": "scheduler_pressure", "rationale": "main waiting on lock", "limitations": [],
             "evidenceRefs": [], "notRootCauseYet": True}
        ],
        "candidateConclusions": [
            {"rank": 1, "confidenceLevel": "high", "statement": "锁竞争是ANR直接原因", "signalCategory": "scheduler_pressure",
             "whyNotFinal": [], "unresolvedQuestions": [], "tentative": True}
        ],
        "remediationDrafts": [
            {"rank": 1, "priority": 10, "title": "检查锁持有者", "actionDraft": "检查pid 42线程", "requiresHumanConfirmation": True}
        ],
        "globalLimitations": [],
        "reProbeRequest": None,
    })

    def _make_mock_urlopen(self, responses: list[bytes]):
        """Create a mock urlopen that returns responses in order."""
        call_count = [0]

        def mock_urlopen(req, timeout=None):
            idx = call_count[0]
            call_count[0] += 1

            mock_resp = MagicMock()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.read = MagicMock(return_value=responses[min(idx, len(responses) - 1)])
            return mock_resp

        return mock_urlopen

    def test_run_ai_agent_analysis_single_iteration(self) -> None:
        responses = [
            json.dumps({"content": [{"type": "text", "text": self.MOCK_CPU_RESPONSE}], "usage": {"input_tokens": 100, "output_tokens": 50}}).encode(),
            json.dumps({"content": [{"type": "text", "text": self.MOCK_STACK_RESPONSE}], "usage": {"input_tokens": 80, "output_tokens": 40}}).encode(),
            json.dumps({"content": [{"type": "text", "text": self.MOCK_IO_RESPONSE}], "usage": {"input_tokens": 90, "output_tokens": 45}}).encode(),
            json.dumps({"content": [{"type": "text", "text": self.MOCK_MANAGER_RESPONSE}], "usage": {"input_tokens": 200, "output_tokens": 100}}).encode(),
        ]

        package = {
            "package_id": "test-001",
            "provided_type": "no_focus_window",
            "sources": {
                "trace": {"content": "Cmd line: com.test\n----- pid 1 -----\n  main tid=1 sysTid=1"},
                "event_log": {"content": "04-12 10:00:05.000 I am_anr: [0,1,com.test,1]"},
                "logcat": {"content": "04-12 10:00:06.000 W timeout"},
            },
        }

        provider_config = ProviderConfig(
            kind=ProviderKind.ANTHROPIC,
            model="claude-sonnet-4-20250514",
            api_key="test-key",
            outbound_policy="synthetic_only",
        )
        agent_config = AgentConfig(provider=provider_config, max_iterations=1, verbose=False)

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = self._make_mock_urlopen(responses)
            result = run_ai_agent_analysis(package, provider_config=provider_config, agent_config=agent_config)

        self.assertEqual(len(result.iterations), 1)
        self.assertIsNotNone(result.final_turn)
        self.assertIn("candidateChains", result.integrated_report)
        self.assertGreater(result.total_tokens, 0)


class ReProbeTests(unittest.TestCase):
    def test_re_probe_request_fields(self) -> None:
        req = ReProbeRequest(
            before_seconds=3, after_seconds=2,
            source_kinds=["logcat"], additional_tags=["binder_reply"],
            reason="verify binder chain",
        )
        self.assertEqual(req.before_seconds, 3)
        self.assertEqual(req.reason, "verify binder chain")
