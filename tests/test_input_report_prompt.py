from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from anr_evidence.ai_agent import (
    AgentConfig,
    LlmClient,
    MANAGER_SYSTEM_PROMPT,
    ProviderConfig,
    ProviderKind,
    _build_manager_prompt,
    run_ai_agent_analysis,
)
from anr_evidence.ai_context import (
    AiContextOptions,
    _analysis_slot_statuses_from_text,
    _get_analysis_slot_template,
    _merge_analyses,
    _read_existing_analyses_from_text,
    build_ai_context,
    build_ai_context_artifacts,
)
from anr_evidence.anr_strategy import ANR_TYPE_STRATEGIES


ROOT = Path(__file__).resolve().parent.parent
HEADINGS = ("## 超时输入事件", "## ANR 发生模型", "## 模拟操作步骤")
ARRAYS = ("inputEventAssessments", "anrModel", "simulationSteps")
SLOTS = ("trace", "eventlog", "logcat-anrmanager", "final-anr")
ANR_TYPES = tuple(ANR_TYPE_STRATEGIES)
AGENT_GUIDES = ("AGENTS.md", "CLAUDE.md", "CODEBUDDY.md", "GEMINI.md", "HERMES.md")


def _json_blocks(text: str) -> list[dict]:
    return [json.loads(block) for block in re.findall(r"```json\n(.*?)\n```", text, re.DOTALL)]


def _package(anr_type: str) -> dict:
    package = json.loads((ROOT / "tests/fixtures/idt_01.json").read_text(encoding="utf-8"))
    package["provided_type"] = anr_type
    if anr_type != "input_dispatching_timeout":
        reason = {
            "no_focus_window": "No focused window",
            "broadcast_timeout": "Broadcast of Intent",
            "service_timeout": "Timeout executing service",
            "content_provider_timeout": "Content provider not responding",
            "job_scheduler_timeout": "JobService onStartJob timed out",
            "system_watchdog_swt": "Watchdog blocked in handler system_server",
            "unknown": "ANR trigger not recorded",
        }[anr_type]
        package["sources"]["event_log"]["content"] = f"04-12 11:00:03.000 am_anr ANR in com.demo: {reason}"
        package["sources"]["logcat"]["content"] = f"04-12 11:00:03.050 E ActivityManager {reason}"
        package["sources"]["trace"]["content"] = "04-12 11:00:03.100 main tid=1 Native"
    return package


class InputReportPromptTests(unittest.TestCase):
    def assert_candidate_schema(self, payload: dict) -> None:
        for name in ARRAYS:
            self.assertIsInstance(payload[name], list)
            self.assertTrue(payload[name], name)
            for candidate in payload[name]:
                self.assertTrue(candidate["summary"])
                self.assertTrue("supportingEventIds" in candidate or "evidenceRefs" in candidate)
                self.assertIsInstance(candidate["uncertainties"], list)
                self.assertEqual(candidate["certainty"], "inferred")
                self.assertEqual(candidate["validation"], "unverified")
                self.assertIs(candidate["executed"], False)
                self.assertIs(candidate["requiresHumanConfirmation"], True)

    def assert_input_contract(self, prompt: str) -> None:
        for heading in HEADINGS:
            self.assertIn(heading, prompt)
        for field in ("action", "keyCode", "native seq", "eventId", "device", "source", "display", "window", "channel"):
            self.assertIn(field, prompt)
        for phrase in (
            "reason 描述事件", "真正最早到期条目", "等待投递事件", "不是 ACK",
            "observed", "inferred", "unknown", "计时", "阻塞", "通知", "恢复",
            "固定 5 秒", "快照当作持续阻塞", "前置条件", "编号动作", "预期观察",
            "采集核验", "缺失参数", "候选未执行", "不保证复现", "坐标", "业务入口",
            "故障注入", "不自动运行设备命令", "回归重跑", "不适用", "不新增第五个槽位",
            "引用存在不代表事实或因果成立",
        ):
            self.assertIn(phrase, prompt)

    def assert_all_type_contract(self, prompt: str) -> None:
        for phrase in (
            "所有 ANR 类型", "候选内容或具体缺口及补证计划", "不能因非 input 写不适用",
            "inputEventAssessments=[]", "incidentScenario", "anr-incident-scenario-v1",
            "missingParameters", "evidenceGaps", "unknown 列出分类/触发点缺口和补证计划，不猜成 input",
            "顶层 certainty=inferred、validation=unverified、executed=false",
            "模板节点除精确 anchor ANR 上报以外均为 unknown", "candidateObservationIds",
            "仅保留在 JSON", "不升级 observed", "jobFinished 不等于 onStartJob/onStopJob 按时返回",
            "setNotification 是另一分支", "referenceValidation 只有引用解析结果",
        ):
            self.assertIn(phrase, prompt)
        for obsolete in (
            "三个候选数组均为空数组", "非 input 必须改为空数组",
            "非 input 保留三节并逐节写", "恢复。非 input 写不适用",
            "不保证复现。非 input 写不适用",
        ):
            self.assertNotIn(obsolete, prompt)

    def test_final_template_has_three_sections_before_json(self) -> None:
        template = _get_analysis_slot_template("final-anr")
        positions = [template.index(heading) for heading in HEADINGS]
        self.assertEqual(positions, sorted(positions))
        self.assertLess(positions[-1], template.index("## 结构化 JSON 尾部"))
        self.assert_input_contract(template)
        self.assert_all_type_contract(template)
        self.assert_candidate_schema(_json_blocks(template)[0])

    def test_source_templates_do_not_gain_input_sections(self) -> None:
        for slot in SLOTS[:-1]:
            template = _get_analysis_slot_template(slot)
            for heading in HEADINGS:
                self.assertNotIn(heading, template)

    def test_public_prompt_carries_contract_for_input_and_non_input(self) -> None:
        for anr_type in ANR_TYPES:
            with self.subTest(anr_type=anr_type):
                result = build_ai_context(_package(anr_type), AiContextOptions(anr_type=anr_type))
                self.assert_input_contract(result.ai_prompt_markdown)
                self.assert_all_type_contract(result.ai_prompt_markdown)
                self.assert_candidate_schema(_json_blocks(result.ai_prompt_markdown)[0])
                self.assertEqual(result.strategy["anrType"], anr_type)
                for focus in ANR_TYPE_STRATEGIES[anr_type].analysis_focus:
                    self.assertIn(focus, result.ai_prompt_markdown)

    def test_artifacts_keep_four_pending_markers_for_each_branch(self) -> None:
        for anr_type in ANR_TYPES:
            with self.subTest(anr_type=anr_type), tempfile.TemporaryDirectory() as directory:
                index = build_ai_context_artifacts(_package(anr_type), AiContextOptions(out_dir=directory))
                analysis = Path(index["groups"][0]["artifactPaths"]["analysis"]).read_text(encoding="utf-8")
                self.assertEqual(re.findall(r"<!-- AI_ANALYSIS_SLOT:([^ ]+) -->", analysis), list(SLOTS))
                self.assertEqual(_analysis_slot_statuses_from_text(analysis), dict.fromkeys(SLOTS, "pending"))
                for heading in HEADINGS:
                    self.assertEqual(analysis.count(heading), 1)
                self.assert_all_type_contract(analysis)
                self.assert_candidate_schema(_json_blocks(analysis)[0])
                self.assertIn("#### AI Analysis — 最终 ANR 综合分析", analysis)

    def test_new_headings_survive_analysis_preservation(self) -> None:
        marker = "<!-- AI_ANALYSIS_SLOT:final-anr -->"
        old_body = "\n".join(["## 综合分析结论", "候选结论。", *[f"{heading}\n证据不足。" for heading in HEADINGS]])
        old = f"#### AI Analysis — 最终 ANR 综合分析\n{marker}\n{old_body}\n"
        pending = f"#### AI Analysis — 最终 ANR 综合分析\n{marker}\n{_get_analysis_slot_template('final-anr')}\n"
        preserved = _read_existing_analyses_from_text(old)
        merged = _merge_analyses(pending, preserved)
        self.assertEqual(_read_existing_analyses_from_text(merged)["final-anr"], old_body)
        self.assertEqual(merged.count(marker), 1)

    def test_legacy_pending_final_template_is_not_preserved_as_an_analysis(self) -> None:
        current = _get_analysis_slot_template("final-anr")
        legacy = current[:current.index("## 超时输入事件")] + current[current.index("## 证据质量"):]
        payload = _json_blocks(legacy)[0]
        for name in ARRAYS:
            payload.pop(name)
        legacy = re.sub(r"```json\n.*?\n```", lambda _: f"```json\n{json.dumps(payload)}\n```", legacy, flags=re.DOTALL)
        text = f"#### AI Analysis — 最终 ANR 综合分析\n<!-- AI_ANALYSIS_SLOT:final-anr -->\n{legacy}\n"
        self.assertNotIn("final-anr", _read_existing_analyses_from_text(text))
        self.assertEqual(_analysis_slot_statuses_from_text(text)["final-anr"], "pending")

    def test_manager_system_prompt_uses_the_same_candidate_contract(self) -> None:
        self.assert_input_contract(MANAGER_SYSTEM_PROMPT)
        self.assert_all_type_contract(MANAGER_SYSTEM_PROMPT)
        self.assert_candidate_schema(_json_blocks(MANAGER_SYSTEM_PROMPT)[0])
        self.assertIn("只输出 JSON", MANAGER_SYSTEM_PROMPT)
        self.assertNotIn("除非证据极其充分", MANAGER_SYSTEM_PROMPT)

    def test_manager_includes_available_input_assessment_without_mutating_it(self) -> None:
        derived = {"branch": "no_focus_window", "evidenceRefs": ["exact observed input line"]}
        groups = [{"id": "anr-one", "reconstruction": {"inputAnr": derived}, "completeness": {"complete": False}}]
        before = deepcopy(groups)
        prompt = _build_manager_prompt({}, groups, None, ANR_TYPE_STRATEGIES["no_focus_window"])
        self.assertIn('"inputAnr"', prompt)
        self.assertIn("exact observed input line", prompt)
        self.assertIn("anr-one", prompt)
        self.assertIn("不升级为事实", prompt)
        self.assertIn("只输出 JSON", prompt)
        self.assertEqual(groups, before)

    def test_manager_without_derived_assessment_does_not_invent_input_data(self) -> None:
        prompt = _build_manager_prompt({}, [{"id": "anr-one"}], None, ANR_TYPE_STRATEGIES["broadcast_timeout"])
        self.assertNotIn('"inputAnr"', prompt)
        self.assertNotIn('"incidentScenario"', prompt)
        self.assertIn("非 input", prompt)
        self.assertIn("空数组", prompt)

    def test_manager_carries_current_strategy_for_every_type(self) -> None:
        for anr_type, strategy in ANR_TYPE_STRATEGIES.items():
            with self.subTest(anr_type=anr_type):
                prompt = _build_manager_prompt({}, [{"id": "anr-one"}], None, strategy)
                self.assert_all_type_contract(prompt)
                self.assertIn(f"`{anr_type}`", prompt)
                for focus in strategy.analysis_focus:
                    self.assertIn(focus, prompt)

    def test_manager_preserves_incident_scenario_and_input_summary_in_both_locations(self) -> None:
        for location in ("group", "reconstruction"):
            for anr_type in ANR_TYPES:
                with self.subTest(location=location, anr_type=anr_type):
                    scenario = {
                        "schemaVersion": "anr-incident-scenario-v1",
                        "anrModel": [{"summary": f"{anr_type} model gap"}],
                        "simulationSteps": [{"summary": f"{anr_type} collection plan"}],
                        "missingParameters": ["trigger identity"],
                        "evidenceGaps": ["effective timeout unknown"],
                    }
                    derived = {"incidentScenario": scenario}
                    if anr_type in {"input_dispatching_timeout", "no_focus_window"}:
                        derived["inputAnr"] = {"branch": anr_type, "reportedEvents": []}
                    group = {"id": "anr-one", **(derived if location == "group" else {"reconstruction": derived})}
                    before = deepcopy(group)
                    prompt = _build_manager_prompt({}, [group], None, ANR_TYPE_STRATEGIES[anr_type])
                    self.assertIn(json.dumps([{"groupId": "anr-one", "incidentScenario": scenario}], ensure_ascii=False, indent=2), prompt)
                    self.assertEqual('"inputAnr"' in prompt, "inputAnr" in derived)
                    self.assertIn("不是新增观测", prompt)
                    self.assertEqual(group, before)

    def test_manager_summary_keeps_group_identity_and_prefers_group_scenario(self) -> None:
        groups = [
            {"id": "anr-first", "incidentScenario": {"marker": "current-scenario"}, "reconstruction": {"incidentScenario": {"marker": "stale-scenario"}}},
            {"id": "anr-second", "reconstruction": {"incidentScenario": {"marker": "second-scenario"}}},
        ]
        prompt = _build_manager_prompt({}, groups, None, ANR_TYPE_STRATEGIES["unknown"])
        self.assertIn("current-scenario", prompt)
        self.assertIn("second-scenario", prompt)
        self.assertNotIn("stale-scenario", prompt)
        self.assertIn('"groupId": "anr-first"', prompt)
        self.assertIn('"groupId": "anr-second"', prompt)

    def test_example_model_nodes_do_not_promote_keyword_candidates_to_observed(self) -> None:
        for prompt in (_get_analysis_slot_template("final-anr"), MANAGER_SYSTEM_PROMPT):
            with self.subTest(prompt=prompt[:40]):
                payload = _json_blocks(prompt)[0]
                for model in payload["anrModel"]:
                    self.assertEqual(model["certainty"], "inferred")
                    self.assertEqual(model["validation"], "unverified")
                    self.assertIs(model["executed"], False)
                    for phase in model["phases"]:
                        self.assertEqual(phase["certainty"], "unknown")
                        self.assertEqual(phase["candidateObservationIds"], [])

    def test_mocked_manager_round_trip_exposes_new_candidate_arrays(self) -> None:
        payload = {name: [] for name in ARRAYS}
        payload.update({"candidateChains": [], "globalLimitations": ["No device execution"], "reProbeRequest": None})
        provider = ProviderConfig(kind=ProviderKind.ANTHROPIC, model="test", api_key="test-key")
        with patch.object(LlmClient, "complete_with_tokens", side_effect=[("{}", 1)] * 3 + [(json.dumps(payload), 1)]) as complete:
            result = run_ai_agent_analysis(_package("no_focus_window"), provider_config=provider, agent_config=AgentConfig(provider=provider, max_iterations=1))
        for name in ARRAYS:
            self.assertEqual(result.integrated_report[name], payload[name])
        self.assertEqual(json.loads(result.final_turn.response), payload)
        self.assertIs(result.integrated_report["finalJudgment"], False)
        self.assertIs(result.integrated_report["notRootCauseYet"], True)
        self.assertIs(result.integrated_report["requiresHumanConfirmation"], True)
        self.assertEqual(result.delivery_readiness["status"], "blocked")
        self.assertEqual(complete.call_count, 4)
        self.assert_input_contract(complete.call_args.args[0][0].content)

    def test_prompts_do_not_claim_manager_runtime_enforces_the_contract(self) -> None:
        self.assertIn("raw integrated_report", MANAGER_SYSTEM_PROMPT)
        self.assertIn("不保证", MANAGER_SYSTEM_PROMPT)
        self.assertIn("运行时", MANAGER_SYSTEM_PROMPT)

    def test_guide_and_specialized_skills_document_the_contract(self) -> None:
        for path in ("docs/anr-ai-analysis-guide.md", "skills/input-dispatch-timeout-anr-analysis/SKILL.md", "skills/no-focused-window-anr-analysis/SKILL.md", "README.md", "README-zh.md"):
            with self.subTest(path=path):
                text = (ROOT / path).read_text(encoding="utf-8")
                for heading in HEADINGS:
                    self.assertIn(heading, text)
                for name in ARRAYS:
                    self.assertIn(name, text)
                self.assertIn("executed=false", text)
                self.assertIn("unverified", text)
                for field in ("inputAnr", "reportedEvents", "nativeSeq", "reportedSeq", "seqDomain", "triggeringEvent", "missingParameters", "aiClaims", "referenceValidation", "anr-reconstruction-v1", "--report", "--deliver"):
                    self.assertIn(field, text)
                for field in ("incidentScenario", "anr-incident-scenario-v1", "所有 ANR 类型"):
                    self.assertIn(field, text)
                for phrase in ("candidateObservationIds", "精确 anchor ANR 上报", "仅保留在 JSON", "jobFinished", "onStartJob/onStopJob 按时返回", "setNotification", "另一分支", "certainty=inferred", "validation=unverified", "executed=false"):
                    self.assertIn(phrase, text)
                self.assertNotIn("三个对应数组均为", text)
                self.assertNotIn("对应三个数组为空", text)
                self.assertNotIn("非 input 三节均写", text)

    def test_agent_delivery_guides_apply_models_to_all_types(self) -> None:
        for path in AGENT_GUIDES:
            with self.subTest(path=path):
                text = (ROOT / path).read_text(encoding="utf-8")
                delivery = text.split("### 第 3 步：输出分析报告", 1)[1].split('<a id="input-dispatch-timeout-anr-specialization">', 1)[0]
                for heading in HEADINGS:
                    self.assertIn(heading, delivery)
                for phrase in ("所有 ANR 类型", "incidentScenario", "anr-incident-scenario-v1", "inputEventAssessments=[]", "具体缺口", "unknown", "候选未执行", "replay", "精确 anchor ANR 上报", "candidateObservationIds", "仅保留在 JSON", "jobFinished 不等于 onStartJob/onStopJob 按时返回", "setNotification 是另一分支", "certainty=inferred", "validation=unverified", "executed=false"):
                    self.assertIn(phrase, delivery)
                self.assertNotIn("对于 input 类型（包括无焦点窗口），最终综合槽位和报告还必须包含", delivery)


if __name__ == "__main__":
    unittest.main()
