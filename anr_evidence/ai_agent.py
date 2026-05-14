"""Multi-agent AI reasoning pipeline for ANR root-cause analysis.

Provides a provider-agnostic LLM client (Anthropic + OpenAI, zero external
SDKs), dispatches 3 specialized sub-agents (CPU/Memory, Stack/Lock, I/O/Binder),
runs a manager agent to synthesize their findings, and supports iterative
re-probe for hypothesis verification.

Output is compatible with the existing Phase 5/6/7 schemas so the delivery
pipeline (reporter.py, delivery.py) works unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import json
import os
import re
import urllib.request
from typing import Any

from .ai_context import AiContextOptions, AiContextResult, build_ai_context, _build_groups, _strategy_summary, _resolve_options
from .anr_strategy import AnrTypeStrategy, strategy_for_package
from .context_flooding import TruncationConfig, truncate_evidence
from .entity_linker import EntityMap, build_entity_map, entity_summary_for_ai
from .evidence_slice import (
    EvidenceSlice,
    annotate_slices_with_tags,
    build_evidence_slices,
    filter_slices_by_delta_t,
    filter_slices_by_importance,
    filter_slices_by_source,
    group_slices_by_source,
)
from .weighting import ImportanceLevel


# ── Provider Abstraction ───────────────────────────────────────────────────


class ProviderKind(Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


@dataclass(frozen=True)
class ProviderConfig:
    """Configuration for an LLM API provider."""

    kind: ProviderKind
    model: str
    api_key: str | None = None   # falls back to env var
    max_tokens: int = 4096
    temperature: float = 0.3

    def resolve_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        if self.kind == ProviderKind.ANTHROPIC:
            key = os.environ.get("ANTHROPIC_API_KEY", "")
        else:
            key = os.environ.get("OPENAI_API_KEY", "")
        if not key:
            raise ValueError(
                f"No API key for {self.kind.value}. Set {'ANTHROPIC_API_KEY' if self.kind == ProviderKind.ANTHROPIC else 'OPENAI_API_KEY'} env var."
            )
        return key


@dataclass(frozen=True)
class AgentConfig:
    """Configuration for the AI agent loop."""

    provider: ProviderConfig
    max_iterations: int = 3
    sub_agent_model: str | None = None
    manager_model: str | None = None
    verbose: bool = False

    def model_for(self, role: str) -> str:
        if role == "manager" and self.manager_model:
            return self.manager_model
        if role == "sub_agent" and self.sub_agent_model:
            return self.sub_agent_model
        return self.provider.model


# ── Messaging ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AgentMessage:
    role: str     # "system", "user", "assistant"
    content: str


@dataclass
class AgentTurn:
    agent_name: str
    iteration: int
    messages: list[AgentMessage] = field(default_factory=list)
    response: str | None = None
    tokens_used: int = 0


# ── LLM Client ─────────────────────────────────────────────────────────────


class LlmClient:
    """Provider-agnostic LLM client supporting Anthropic and OpenAI."""

    def __init__(self, config: ProviderConfig):
        self._config = config

    def complete(self, messages: list[AgentMessage]) -> str:
        response, _ = self.complete_with_tokens(messages)
        return response

    def complete_with_tokens(self, messages: list[AgentMessage]) -> tuple[str, int]:
        api_key = self._config.resolve_api_key()

        if self._config.kind == ProviderKind.ANTHROPIC:
            return self._complete_anthropic(messages, api_key)
        else:
            return self._complete_openai(messages, api_key)

    def _complete_anthropic(self, messages: list[AgentMessage], api_key: str) -> tuple[str, int]:
        system = ""
        chat_messages: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == "system":
                system = msg.content
            else:
                chat_messages.append({"role": msg.role, "content": msg.content})

        body: dict[str, Any] = {
            "model": self._config.model,
            "max_tokens": self._config.max_tokens,
            "temperature": self._config.temperature,
            "messages": chat_messages,
        }
        if system:
            body["system"] = system

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        content_blocks = data.get("content", [])
        text = "".join(block.get("text", "") for block in content_blocks if block.get("type") == "text")
        usage = data.get("usage", {})
        tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        return text, tokens

    def _complete_openai(self, messages: list[AgentMessage], api_key: str) -> tuple[str, int]:
        chat_messages: list[dict[str, str]] = []
        for msg in messages:
            chat_messages.append({"role": msg.role, "content": msg.content})

        body = {
            "model": self._config.model,
            "max_tokens": self._config.max_tokens,
            "temperature": self._config.temperature,
            "messages": chat_messages,
        }
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        choices = data.get("choices", [])
        text = choices[0].get("message", {}).get("content", "") if choices else ""
        usage = data.get("usage", {})
        tokens = usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
        return text, tokens


# ── Sub-Agent Specifications ───────────────────────────────────────────────


@dataclass(frozen=True)
class SubAgentSpec:
    name: str
    display_name: str
    source_kinds: list[str]
    focus_tags: dict[str, list[str]]
    system_prompt: str


SUB_AGENT_SPECS: dict[str, SubAgentSpec] = {
    "cpu_memory": SubAgentSpec(
        name="cpu_memory",
        display_name="CPU/Memory Sub-Agent",
        source_kinds=["trace", "event_log", "kernel_log"],
        focus_tags={
            "trace": ["schedstat", "cpu", "runnable", "nice", "cgrp", "vmwait", "suspend", "gc"],
            "event_log": [
                "am_meminfo", "am_pss", "am_kill", "am_proc_died", "am_uid_idle",
                "battery_level", "battery_status", "power_sleep_continuous",
                "power_screen_state", "power_partial_wake_state",
            ],
            "kernel_log": [
                "sched", "hung task", "lowmemorykiller", "lmkd", "oom",
                "psi", "pressure", "kworker", "irq",
            ],
        },
        system_prompt="""你是一个 Android CPU/内存压力分析专家。
请基于提供的证据片段，分析是否存在以下情况：
1. CPU 调度压力（runnable 线程过多、调度延迟、优先级反转）
2. 内存压力（lowmemorykiller、GC 暂停、VMWAIT 聚集、OOM）
3. 热/电源管理导致的限频或休眠
4. 系统层面的资源争抢

对每个发现，请：
- 引用具体的证据行（给出 content）
- 给出判断和置信度（high/medium/low）
- 说明该发现与 ANR 的可能关联

输出格式：
```json
{
  "findings": [
    {
      "category": "cpu_pressure | memory_pressure | thermal_power | resource_contention",
      "confidence": "high | medium | low",
      "summary": "...",
      "evidence_refs": ["content line 1", "content line 2"],
      "anr_relevance": "..."
    }
  ],
  "overall_assessment": "..."
}
```""",
    ),
    "stack_lock": SubAgentSpec(
        name="stack_lock",
        display_name="Stack/Lock Sub-Agent",
        source_kinds=["trace"],
        focus_tags={
            "trace": [
                "lock", "monitor", "blocked", "waiting to lock", "held mutexes",
                "waitObject", "lockOwnerTid", "futex", "threadState",
                "nativeTopFrame", "javaTopFrame", "looperFrame",
            ],
        },
        system_prompt="""你是一个 Android 线程堆栈/锁分析专家。
请基于提供的 trace 堆栈证据，分析：
1. 主线程的阻塞点（锁等待、monitor contention、futex）
2. 锁的持有者线程信息，以及持有者线程本身的状态
3. 堆栈中的关键帧（native/Java/looper）揭示的阻塞原因
4. 是否存在死锁风险

对每个发现，请：
- 引用具体的证据行（给出 content）
- 给出判断和置信度（high/medium/low）
- 说明该阻塞是否是 ANR 的直接原因

输出格式：
```json
{
  "findings": [
    {
      "category": "lock_contention | monitor_wait | futex_wait | native_block | java_block | deadlock_risk",
      "confidence": "high | medium | low",
      "summary": "...",
      "blocked_thread": "...",
      "owner_thread": "...",
      "key_frames": ["...", "..."],
      "evidence_refs": ["content line 1"],
      "anr_relevance": "..."
    }
  ],
  "overall_assessment": "..."
}
```""",
    ),
    "io_binder": SubAgentSpec(
        name="io_binder",
        display_name="I/O & Binder Sub-Agent",
        source_kinds=["trace", "event_log", "logcat", "kernel_log"],
        focus_tags={
            "trace": [
                "binder", "transaction", "reply", "talkWithDriver", "joinThreadPool",
                "transactNative", "BinderProxy", "backlog", "binderCallKind",
            ],
            "event_log": [
                "am_proc_died", "am_proc_start", "am_kill", "am_anr",
            ],
            "logcat": [
                "activitymanager", "broadcastqueue", "contentprovider",
                "system_server", "slow operation", "timeout",
                "inputdispatcher", "input dispatching timed out", "focused window",
            ],
            "kernel_log": [
                "binder", "blocked for more than", "watchdog",
            ],
        },
        system_prompt="""你是一个 Android Binder/IPC 和 I/O 阻塞分析专家。
请基于提供的证据片段，分析：
1. Binder 调用链（主线程是否在等待 binder 回复、binder 线程池是否饱和、是否存在 backlog）
2. 跨进程通信的模式（广播、ContentProvider、Service 绑定）
3. I/O 或内核层面的阻塞（blocked for more than、watchdog）
4. 使用提供的 Entity Map 跨源关联同一 PID/TID 的相关记录

对每个发现，请：
- 引用具体的证据行（给出 content）
- 给出判断和置信度（high/medium/low）
- 说明该发现与 ANR 的可能关联

输出格式：
```json
{
  "findings": [
    {
      "category": "binder_saturation | binder_reply_wait | ipc_timeout | io_block | kernel_block | cross_process_cascade",
      "confidence": "high | medium | low",
      "summary": "...",
      "cross_source_entity": "PID/TID (optional)",
      "evidence_refs": ["content line 1"],
      "anr_relevance": "..."
    }
  ],
  "overall_assessment": "..."
}
```""",
    ),
}

# ── Manager Agent ──────────────────────────────────────────────────────────

MANAGER_SYSTEM_PROMPT = """你是一个 Android ANR 根因分析的总协调专家。

你将收到 3 个子分析报告（CPU/内存、堆栈/锁、I/O/Binder），以及完整的 ANR 证据摘要。

你的任务：
1. 综合 3 个子报告，交叉验证它们的发现
2. 识别一致的证据链和矛盾的证据
3. 给出按置信度排序的候选根因链路
4. 输出与 Phase 5/6/7 兼容的结构化结论

输出必须是有效的 JSON，包含以下字段：
{
  "candidateChains": [
    {
      "title": "链路标题（中文）",
      "rank": 1,
      "score": 0-100,
      "confidenceLevel": "high | medium | low",
      "signalCategory": "focus_window_issue | input_dispatch_timeout | binder_or_ipc_pressure | scheduler_pressure",
      "rationale": "详细推理过程",
      "limitations": ["限制说明1", "限制说明2"],
      "evidenceRefs": ["证据行1", "证据行2"],
      "notRootCauseYet": true
    }
  ],
  "candidateConclusions": [
    {
      "rank": 1,
      "confidenceLevel": "high | medium | low",
      "statement": "结论陈述（中文）",
      "signalCategory": "...",
      "whyNotFinal": ["非最终原因1"],
      "unresolvedQuestions": ["未解决问题1"],
      "tentative": true
    }
  ],
  "remediationDrafts": [
    {
      "rank": 1,
      "priority": 1-100,
      "title": "修复建议标题（中文）",
      "actionDraft": "具体操作步骤",
      "requiresHumanConfirmation": true
    }
  ],
  "globalLimitations": ["全局限制说明"],
  "reProbeRequest": null
}

如果证据不足以得出任何结论，设置 candidateChains 为空数组，并在 globalLimitations 中说明。
如果某个方向需要更精确的证据（更窄的时间窗口或额外的标签过滤），在 reProbeRequest 中给出：
{
  "reProbeRequest": {
    "beforeSeconds": 3,
    "afterSeconds": 3,
    "sourceKinds": ["logcat", "kernel_log"],
    "additionalTags": ["binder_reply", "binder_transaction"],
    "reason": "需要更精确的时间窗口来验证 binder 锁等待链"
  }
}
如果不需要 re-probe，设置 reProbeRequest 为 null。"""


def _build_sub_agent_prompt(
    spec: SubAgentSpec,
    slices: list[EvidenceSlice],
    entity_map: EntityMap | None,
) -> str:
    """Build the user prompt for a sub-agent from focused evidence slices."""
    grouped = group_slices_by_source(slices)

    lines = [f"# {spec.display_name} 分析任务", ""]
    lines.append("## 证据片段")
    lines.append("")

    for source_kind in spec.source_kinds:
        source_slices = grouped.get(source_kind, [])
        if not source_slices:
            lines.append(f"### {source_kind} (无数据)")
            lines.append("")
            continue
        lines.append(f"### {source_kind} ({len(source_slices)} lines)")
        lines.append("```text")
        for s in source_slices:
            tag_info = f" [{s.tag}]" if s.tag else ""
            delta_info = f" (Δ{s.delta_t_seconds:.1f}s)" if s.delta_t_seconds is not None else ""
            lines.append(f"{s.content}{tag_info}{delta_info}")
        lines.append("```")
        lines.append("")

    if entity_map:
        lines.append(entity_summary_for_ai(entity_map))
        lines.append("")

    lines.append("## 要求")
    lines.append("请严格按照 system prompt 中的 JSON 格式输出分析结果。")
    lines.append("只输出 JSON，不要添加额外解释。")

    return "\n".join(lines)


def _focus_slices_for_sub_agent(
    slices: list[EvidenceSlice],
    spec: SubAgentSpec,
) -> list[EvidenceSlice]:
    """Filter and focus evidence slices for a specific sub-agent.

    Keeps only slices whose source_kind is in spec.source_kinds AND whose tag
    (if any) is relevant to that sub-agent's focus_tags.
    """
    # First filter by source kind
    source_filtered = filter_slices_by_source(slices, source_kinds=spec.source_kinds)

    # If no focus tags specified, return all
    if not spec.focus_tags:
        return source_filtered

    # For slices with tags, keep only those matching focus_tags for their source
    focused: list[EvidenceSlice] = []
    for s in source_filtered:
        if s.tag is None:
            focused.append(s)
            continue
        focus_for_source = spec.focus_tags.get(s.source, [])
        if not focus_for_source:
            focused.append(s)
            continue
        lowered_tag = s.tag.lower()
        if any(ft.lower() in lowered_tag or lowered_tag in ft.lower() for ft in focus_for_source):
            focused.append(s)
        else:
            # Keep all critical slices regardless
            if s.importance == "critical":
                focused.append(s)

    return focused


# ── Re-Probe ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ReProbeRequest:
    """A request to re-filter evidence with a narrower window."""

    before_seconds: int
    after_seconds: int
    source_kinds: list[str]
    additional_tags: list[str]
    reason: str


def _parse_re_probe_request(data: dict[str, Any]) -> ReProbeRequest | None:
    """Parse a reProbeRequest from the manager's JSON output."""
    req = data.get("reProbeRequest")
    if not req:
        return None
    return ReProbeRequest(
        before_seconds=req.get("beforeSeconds", 3),
        after_seconds=req.get("afterSeconds", 3),
        source_kinds=req.get("sourceKinds", ["logcat", "kernel_log"]),
        additional_tags=req.get("additionalTags", []),
        reason=req.get("reason", "manager requested narrower window"),
    )


def _execute_re_probe(
    groups: list[dict[str, Any]],
    request: ReProbeRequest,
    strategy: AnrTypeStrategy,
) -> list[EvidenceSlice]:
    """Re-filter evidence with a narrower window + additional tags.

    Builds new ESS slices where delta_t is within the re-probe window.
    """
    slices = build_evidence_slices(groups)
    annotated = annotate_slices_with_tags(
        slices,
        event_tags=frozenset(strategy.event_tags),
        logcat_patterns=frozenset(strategy.logcat_patterns),
    )
    # Filter by source kinds
    filtered = filter_slices_by_source(annotated, source_kinds=request.source_kinds)
    # Filter by narrower delta_t window
    filtered = filter_slices_by_delta_t(
        filtered,
        min_delta_t=-request.before_seconds,
        max_delta_t=request.after_seconds,
    )
    return filtered


# ── Result Types ───────────────────────────────────────────────────────────


@dataclass
class AiAgentResult:
    """Complete result of an AI agent analysis run."""

    package_id: str
    config: AgentConfig
    iterations: list[dict[str, AgentTurn]] = field(default_factory=list)
    final_turn: AgentTurn | None = None
    integrated_report: dict[str, Any] = field(default_factory=dict)
    total_tokens: int = 0
    re_probe_requests: list[ReProbeRequest] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ── Main Entry Point ───────────────────────────────────────────────────────


def run_ai_agent_analysis(
    package: dict[str, Any],
    *,
    provider_config: ProviderConfig,
    agent_config: AgentConfig | None = None,
    ai_context_options: AiContextOptions | None = None,
) -> AiAgentResult:
    """Run the multi-agent ANR analysis loop.

    1. Build AI context (groups + cache)
    2. Build ESS + EntityMap
    3. For each iteration:
       a. Dispatch 3 sub-agents with focused evidence
       b. Run manager to synthesize
       c. If manager requests re-probe, execute and loop
       d. If satisfied, break
    4. Return AiAgentResult with integrated report
    """
    agent_cfg = agent_config or AgentConfig(provider=provider_config)
    ai_opts = ai_context_options or AiContextOptions()
    strategy = strategy_for_package(package, ai_opts.anr_type)
    client = LlmClient(provider_config)

    result = AiAgentResult(
        package_id=str(package.get("package_id", "unknown")),
        config=agent_cfg,
    )

    # Step 1: Build AI context
    ctx_result = build_ai_context(package, ai_opts)
    groups = ctx_result.groups

    # Step 2: Build ESS + EntityMap + Truncation
    slices = annotate_slices_with_tags(
        build_evidence_slices(groups),
        event_tags=frozenset(strategy.event_tags),
        logcat_patterns=frozenset(strategy.logcat_patterns),
    )
    entity_map = build_entity_map(package)

    # Apply context flooding prevention
    trunc_config = TruncationConfig(
        max_total_lines=200,
        min_importance="warning",
        preserve_critical=True,
    )
    trunc_result = truncate_evidence(slices, trunc_config)
    slices = trunc_result.retained_slices

    # Step 3: Iterative agent loop
    total_tokens = 0
    active_slices = list(slices)

    for iteration in range(agent_cfg.max_iterations):
        iteration_turns: dict[str, AgentTurn] = {}

        # 3a. Dispatch sub-agents
        for spec_name, spec in SUB_AGENT_SPECS.items():
            focused = _focus_slices_for_sub_agent(active_slices, spec)
            prompt = _build_sub_agent_prompt(spec, focused, entity_map)

            messages = [
                AgentMessage(role="system", content=spec.system_prompt),
                AgentMessage(role="user", content=prompt),
            ]
            response_text, tokens = client.complete_with_tokens(messages)
            total_tokens += tokens

            turn = AgentTurn(
                agent_name=spec_name,
                iteration=iteration,
                messages=messages,
                response=response_text,
                tokens_used=tokens,
            )
            iteration_turns[spec_name] = turn

            if agent_cfg.verbose:
                print(f"[{spec_name}] iter={iteration} tokens={tokens}")

        # 3b. Run manager
        manager_prompt = _build_manager_prompt(
            iteration_turns, groups, entity_map, strategy,
        )
        manager_messages = [
            AgentMessage(role="system", content=MANAGER_SYSTEM_PROMPT),
            AgentMessage(role="user", content=manager_prompt),
        ]
        manager_text, manager_tokens = client.complete_with_tokens(manager_messages)
        total_tokens += manager_tokens

        manager_turn = AgentTurn(
            agent_name="manager",
            iteration=iteration,
            messages=manager_messages,
            response=manager_text,
            tokens_used=manager_tokens,
        )
        iteration_turns["manager"] = manager_turn
        result.iterations.append(iteration_turns)

        if agent_cfg.verbose:
            print(f"[manager] iter={iteration} tokens={manager_tokens}")

        # 3c. Parse manager output
        parsed = _parse_manager_json(manager_text)
        if parsed is None:
            result.errors.append(f"Iteration {iteration}: failed to parse manager JSON")
            continue

        # 3d. Check for re-probe request
        re_probe = _parse_re_probe_request(parsed)
        if re_probe is not None and iteration < agent_cfg.max_iterations - 1:
            result.re_probe_requests.append(re_probe)
            active_slices = _execute_re_probe(groups, re_probe, strategy)
            if agent_cfg.verbose:
                print(f"[re-probe] iter={iteration} window={re_probe.before_seconds}s/{re_probe.after_seconds}s tags={re_probe.additional_tags}")
            continue

        # 3e. Accept the result
        result.final_turn = manager_turn
        result.integrated_report = parsed
        result.total_tokens = total_tokens
        return result

    # Max iterations reached without resolution
    if result.iterations and "manager" in result.iterations[-1]:
        result.final_turn = result.iterations[-1]["manager"]
        parsed = _parse_manager_json(result.iterations[-1]["manager"].response or "")
        if parsed:
            result.integrated_report = parsed
    result.total_tokens = total_tokens
    result.errors.append("Max iterations reached without definitive conclusion.")
    return result


def _build_manager_prompt(
    sub_results: dict[str, AgentTurn],
    groups: list[dict[str, Any]],
    entity_map: EntityMap | None,
    strategy: AnrTypeStrategy,
) -> str:
    """Build the user prompt for the manager agent."""
    lines = ["# Manager: 综合根因分析", ""]

    # Strategy context
    lines.append("## ANR 类型")
    lines.append(f"- 类型: `{strategy.anr_type}` ({strategy.label})")
    lines.append(f"- 分析重点: {'; '.join(strategy.analysis_focus)}")
    lines.append("")

    # Sub-agent reports
    lines.append("## 子分析报告")
    for name, turn in sub_results.items():
        if name == "manager":
            continue
        lines.append(f"### {name}")
        if turn.response:
            lines.append(turn.response)
        else:
            lines.append("_(无响应)_")
        lines.append("")

    # Entity map
    if entity_map:
        lines.append(entity_summary_for_ai(entity_map))
        lines.append("")

    # Group summary
    summary_lines = [f"- `{g.get('id', '?')}` complete=`{g.get('completeness', {}).get('complete', '?')}`"
                     for g in groups]
    lines.append("## ANR 组完整性")
    lines.extend(summary_lines)
    lines.append("")

    lines.append("## 要求")
    lines.append("请严格按照 system prompt 中的 JSON 格式输出综合结论。只输出 JSON。")

    return "\n".join(lines)


def _parse_manager_json(text: str) -> dict[str, Any] | None:
    """Parse JSON output from the manager agent. Handles ```json fences."""
    if not text:
        return None
    # Try to extract JSON from code fences
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    json_text = fence_match.group(1) if fence_match else text
    # Find the outermost JSON object
    brace_start = json_text.find("{")
    if brace_start == -1:
        return None
    brace_end = json_text.rfind("}")
    if brace_end == -1:
        return None
    try:
        return json.loads(json_text[brace_start:brace_end + 1])
    except json.JSONDecodeError:
        return None


# ── Convenience: run agent from CLI ────────────────────────────────────────


def run_ai_agent_from_cli(
    input_path: str,
    *,
    provider: str = "anthropic",
    model: str | None = None,
    api_key: str | None = None,
    max_iterations: int = 3,
    verbose: bool = False,
    out_dir: str | None = None,
) -> AiAgentResult:
    """Convenience entry point for running the AI agent from a CLI or script.

    Loads a package from the given path (fixture/directory/archive), configures
    the LLM provider, and runs the full multi-agent analysis.
    """
    from .extractor import ArchiveLoadError, load_package_from_archive, load_package_from_directory, load_package_from_fixture
    from pathlib import Path

    path = Path(input_path)
    if path.is_dir():
        package = load_package_from_directory(path)
    elif _is_archive_path(path):
        package = load_package_from_archive(path)
    else:
        package = load_package_from_fixture(path)

    provider_kind = ProviderKind.ANTHROPIC if provider.lower() == "anthropic" else ProviderKind.OPENAI
    default_model = "claude-sonnet-4-20250514" if provider_kind == ProviderKind.ANTHROPIC else "gpt-4o"
    provider_config = ProviderConfig(
        kind=provider_kind,
        model=model or default_model,
        api_key=api_key,
    )
    agent_config = AgentConfig(
        provider=provider_config,
        max_iterations=max_iterations,
        verbose=verbose,
    )
    ai_opts = AiContextOptions(out_dir=out_dir) if out_dir else AiContextOptions()

    return run_ai_agent_analysis(
        package,
        provider_config=provider_config,
        agent_config=agent_config,
        ai_context_options=ai_opts,
    )


def _is_archive_path(path) -> bool:
    from pathlib import Path
    path = Path(path) if isinstance(path, str) else path
    suffixes = [s.lower() for s in path.suffixes]
    if not suffixes:
        return False
    if suffixes[-1] == ".zip":
        return True
    return any(s in {".tar", ".gz", ".tgz", ".bz2", ".xz"} for s in suffixes)
