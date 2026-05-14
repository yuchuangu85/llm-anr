"""Tests for anr_evidence.ai_agent.

Uses mock HTTP responses to avoid real API calls.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from anr_evidence.ai_agent import (
    AgentConfig,
    AgentMessage,
    AgentTurn,
    LlmClient,
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
from anr_evidence.ai_context import AiContextOptions


class LlmClientTests(unittest.TestCase):
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

        def mock_urlopen(req):
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
