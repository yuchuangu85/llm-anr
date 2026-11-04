"""Multi-agent AI reasoning pipeline for ANR root-cause analysis.

Provides a provider-agnostic LLM client (Anthropic + OpenAI, zero external
SDKs), dispatches 3 specialized sub-agents (CPU/Memory, Stack/Lock, I/O/Binder),
runs a manager agent to synthesize their findings, and supports iterative
re-probe for hypothesis verification.

Output is compatible with the existing Phase 5/6/7 schemas so the delivery
pipeline (reporter.py, delivery.py) works unchanged.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any

from .ai_context import (
    AiContextOptions,
    AiContextResult,
    ANR_CANDIDATE_FIELDS,
    ANR_REPORT_REQUIREMENTS,
    build_ai_context,
)
from .anr_strategy import AnrTypeStrategy, strategy_for_package
from .context_flooding import TruncationConfig, truncate_evidence
from .entity_linker import EntityMap, build_entity_map, entity_summary_for_ai
from .evidence_slice import (
    EvidenceSlice,
    annotate_slices_with_tags,
    build_evidence_slices,
    filter_slices_by_importance,
    filter_slices_by_source,
    group_slices_by_source,
)
from .report_validation import DELIVERABLE_STATUSES, strict_json_loads, validate_manager_report
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
    timeout_seconds: float = 120.0
    max_retries: int = 2         # retries on 429/5xx/network errors

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
    truncation: TruncationConfig = field(default_factory=TruncationConfig)

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
    error: str | None = None


# ── LLM Client ─────────────────────────────────────────────────────────────


class LlmRequestError(RuntimeError):
    """Raised when an LLM API request fails after exhausting retries."""


class LlmClient:
    """Provider-agnostic LLM client supporting Anthropic and OpenAI."""

    def __init__(self, config: ProviderConfig):
        self._config = config

    def _post_json(self, url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        """POST with timeout and bounded retries on 429/5xx/network errors."""

        last_error: Exception | None = None
        for attempt in range(self._config.max_retries + 1):
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self._config.timeout_seconds) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code not in (429, 500, 502, 503, 504) or attempt >= self._config.max_retries:
                    raise LlmRequestError(f"LLM API request to {url} failed with HTTP {exc.code}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
                if attempt >= self._config.max_retries:
                    raise LlmRequestError(f"LLM API request to {url} failed: {exc}") from exc
            time.sleep(min(2 ** attempt, 8))
        raise LlmRequestError(f"LLM API request to {url} failed: {last_error}")

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

        data = self._post_json(
            "https://api.anthropic.com/v1/messages",
            body,
            {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )

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
        data = self._post_json(
            "https://api.openai.com/v1/chat/completions",
            body,
            {
                "Authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
        )

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
""" + ANR_CANDIDATE_FIELDS + """
  "finalJudgment": false,
  "notRootCauseYet": true,
  "requiresHumanConfirmation": true,
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
如果不需要 re-probe，设置 reProbeRequest 为 null。

Final ANR 的 `## ANR 发生模型`、`## 模拟操作步骤` 适用于所有 ANR 类型；`## 超时输入事件` 仅 input。
本 manager 只输出 JSON，不输出 Markdown 章节；anrModel、simulationSteps 对所有类型给出候选或具体缺口及补证计划，非 input 仅令 inputEventAssessments=[]。
提示不保证响应合规；运行时保存 raw integrated_report 对应的原始响应，返回保守化副本并记录规范化警告，再执行结构、分组及引用验收。验收不代表事实或根因已验证。
每个候选条目用 groupId 标明所属事故，引用仅限该组。多个 ANR 时，顶层保留候选数组、globalLimitations 和保守标志，并增加 groups 数组，覆盖每个唯一 groupId；每个子报告都包含完整的候选数组、模型、步骤和保守标志。
""" + ANR_REPORT_REQUIREMENTS + """

三个候选数组的 JSON 示例（非 input 仅将 inputEventAssessments 改为空数组，保留两个公共数组的候选/缺口内容）：
```json
{
""" + ANR_CANDIDATE_FIELDS.rstrip(",") + """
}
```"""


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
            lines.append(f"[groupId={s.group_id}] {s.content}{tag_info}{delta_info}")
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
    additional_tags: list[str] | None = None,
) -> list[EvidenceSlice]:
    """Filter and focus evidence slices for a specific sub-agent.

    Keep relevant sources/tags, including the manager's additional probe tags.
    """
    # First filter by source kind
    source_filtered = filter_slices_by_source(slices, source_kinds=spec.source_kinds)

    # If no focus tags specified, return all
    if not spec.focus_tags:
        return source_filtered

    # For slices with tags, keep only those matching focus_tags for their source
    focused: list[EvidenceSlice] = []
    for s in source_filtered:
        if additional_tags and any(tag.lower() in s.content.lower() for tag in additional_tags):
            focused.append(s)
            continue
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
    group_id: str | None = None


def _parse_re_probe_request(data: dict[str, Any]) -> ReProbeRequest | None:
    """Parse a typed request; malformed requests are not equivalent to null."""
    req = data.get("reProbeRequest")
    if req is None:
        return None
    if not isinstance(req, dict) or not req:
        raise ValueError("reProbeRequest must be a nonempty object or null")
    for name in ("beforeSeconds", "afterSeconds"):
        value = req.get(name, 3)
        if type(value) is not int or value < 0:
            raise ValueError(f"reProbeRequest.{name} must be a nonnegative integer")
    sources = req.get("sourceKinds", ["logcat", "kernel_log"])
    allowed_sources = {"trace", "event_log", "logcat", "kernel_log", "anr_manager", "meminfo"}
    if not isinstance(sources, list) or not sources or any(not isinstance(item, str) or item not in allowed_sources for item in sources):
        raise ValueError("reProbeRequest.sourceKinds must be a nonempty list of known sources")
    tags = req.get("additionalTags", [])
    if not isinstance(tags, list) or any(not isinstance(item, str) or not item.strip() for item in tags):
        raise ValueError("reProbeRequest.additionalTags must be a list of nonempty strings")
    reason = req.get("reason", "manager requested narrower window")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reProbeRequest.reason must be a nonempty string")
    return ReProbeRequest(
        before_seconds=req.get("beforeSeconds", 3),
        after_seconds=req.get("afterSeconds", 3),
        source_kinds=list(sources),
        additional_tags=list(tags),
        reason=reason,
    )


def _probe_dict(request: ReProbeRequest) -> dict[str, Any]:
    value = {
        "beforeSeconds": request.before_seconds, "afterSeconds": request.after_seconds,
        "sourceKinds": list(request.source_kinds), "additionalTags": list(request.additional_tags),
        "reason": request.reason,
    }
    if request.group_id is not None:
        value["groupId"] = request.group_id
    return value


def _execute_re_probe(
    groups: list[dict[str, Any]],
    request: ReProbeRequest,
    strategy: AnrTypeStrategy,
) -> list[EvidenceSlice]:
    """Re-filter evidence with a narrower window + additional tags.

    Builds new ESS slices where delta_t is within the re-probe window.
    """
    if request.group_id is not None:
        groups = [group for group in groups if group.get("id") == request.group_id]
    slices = build_evidence_slices(groups)
    annotated = annotate_slices_with_tags(
        slices,
        event_tags=frozenset(strategy.event_tags),
        logcat_patterns=frozenset(strategy.logcat_patterns),
    )
    # Filter by source kinds
    filtered = filter_slices_by_source(annotated, source_kinds=request.source_kinds)
    selected = []
    for item in filtered:
        # Unknown clocks cannot safely be excluded by an anchor-relative window.
        if item.delta_t_seconds is not None and not -request.before_seconds <= item.delta_t_seconds <= request.after_seconds:
            continue
        matching_tag = next((tag for tag in request.additional_tags if tag.lower() in item.content.lower()), None)
        if matching_tag:
            item = replace(item, tag=matching_tag, importance="critical" if item.importance == "critical" else "warning")
        selected.append(item)
    return selected


def _merge_probe_slices(
    retained: list[EvidenceSlice], additional: list[EvidenceSlice], config: TruncationConfig,
) -> list[EvidenceSlice]:
    """Budget probe additions without evicting evidence already sent to agents."""
    identities = {(item.group_id, item.source, item.line_index, item.content) for item in retained}
    additions = []
    for item in additional:
        identity = (item.group_id, item.source, item.line_index, item.content)
        if identity not in identities:
            identities.add(identity)
            additions.append(item)
    # Existing evidence is protected for truncation only; its original metadata
    # is restored below, so protection does not become a new importance claim.
    protected = [replace(item, importance="critical") for item in retained]
    truncated = truncate_evidence(protected + additions, config)
    originals = {(item.group_id, item.source, item.line_index, item.content): item for item in retained}
    return [originals.get((item.group_id, item.source, item.line_index, item.content), item) for item in truncated.retained_slices]


def _normalize_manager_report(report: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Normalize candidate layers only, never evidence events or phase nodes."""
    normalized = deepcopy(report)
    warnings: list[dict[str, Any]] = []
    flags = {"finalJudgment": False, "notRootCauseYet": True, "requiresHumanConfirmation": True}
    model_flags = {"certainty": "inferred", "validation": "unverified", "executed": False}

    def force(item, values, path):
        for name, expected in values.items():
            if type(item.get(name)) is not type(expected) or item.get(name) != expected:
                warnings.append({"code": "AI_REPORT_NORMALIZED", "path": f"{path}.{name}",
                                 "message": f"Normalized {name} to conservative value {expected!r}; raw response retained in AgentTurn."})
            item[name] = expected

    def visit(payload, path):
        force(payload, flags, path)
        force(payload, {key: value for key, value in model_flags.items() if key in payload}, path)
        for field_name in ("candidateChains", "candidateConclusions", "remediationDrafts", "inputEventAssessments", "anrModel", "simulationSteps"):
            items = payload.get(field_name)
            if not isinstance(items, list):
                continue
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                candidate_path = f"{path}.{field_name}[{index}]"
                force(item, flags, candidate_path)
                if field_name in ("inputEventAssessments", "anrModel", "simulationSteps"):
                    force(item, model_flags, candidate_path)
                else:
                    force(item, {key: value for key, value in model_flags.items() if key in item}, candidate_path)
                if field_name == "candidateConclusions":
                    force(item, {"tentative": True}, candidate_path)
        children = payload.get("groups")
        if isinstance(children, list):
            for index, child in enumerate(children):
                if isinstance(child, dict):
                    visit(child, f"{path}.groups[{index}]")

    if normalized:
        visit(normalized, "report")
    return normalized, warnings


# ── Result Types ───────────────────────────────────────────────────────────


@dataclass
class AiAgentResult:
    """Analysis attempts and their candidate report, not finalized artifacts.

    ``final_turn`` is the last manager attempt, including failed attempts.
    Only ``delivery_readiness`` in DELIVERABLE_STATUSES denotes acceptance.
    """

    package_id: str
    config: AgentConfig
    iterations: list[dict[str, AgentTurn]] = field(default_factory=list)
    final_turn: AgentTurn | None = None
    integrated_report: dict[str, Any] = field(default_factory=dict)
    total_tokens: int = 0
    re_probe_requests: list[ReProbeRequest] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    delivery_readiness: dict[str, Any] = field(default_factory=dict)
    stop_reason: str | None = None
    pending_re_probe: dict[str, Any] | None = None


def _assess_result(
    result: AiAgentResult, groups: list[dict[str, Any]], warnings: list[dict[str, Any]],
    runtime_blockers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    readiness = validate_manager_report(
        result.integrated_report, groups=groups,
        pending_probe=result.pending_re_probe, stop_reason=result.stop_reason,
    )
    readiness["warnings"].extend(deepcopy(warnings))
    readiness["blockers"].extend(runtime_blockers or [])
    readiness["stopReason"] = result.stop_reason
    readiness["checks"]["runtime"] = "fail" if runtime_blockers else "pass"
    if readiness["blockers"]:
        readiness["status"] = "blocked"
    elif readiness["reviewItems"]:
        readiness["status"] = "needs_review"
    elif readiness["status"] == "ready" and readiness["warnings"]:
        readiness["status"] = "ready_with_limitations"
    result.delivery_readiness = readiness
    return readiness


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
       d. Retry invalid/review-required reports with validation feedback
       e. Accept only a deliverable readiness status with no pending probe
    4. Return attempts, a conservative candidate report and explicit readiness
    """
    agent_cfg = agent_config or AgentConfig(provider=provider_config)
    ai_opts = ai_context_options or AiContextOptions()
    strategy = strategy_for_package(package, ai_opts.anr_type)
    if type(agent_cfg.max_iterations) is not int or agent_cfg.max_iterations <= 0:
        raise ValueError("max_iterations must be a positive integer")
    clients = {
        role: LlmClient(replace(provider_config, model=agent_cfg.model_for(role)))
        for role in ("sub_agent", "manager")
    }

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
    trunc_result = truncate_evidence(slices, agent_cfg.truncation)
    slices = trunc_result.retained_slices

    # Step 3: Iterative agent loop
    active_slices = list(slices)
    active_probe_tags: list[str] = []
    normalization_warnings: list[dict[str, Any]] = []
    runtime_blockers: list[dict[str, Any]] = []
    feedback = None
    previous_manager_response = None

    def complete_turn(turn: AgentTurn, role: str) -> bool:
        try:
            turn.response, turn.tokens_used = clients[role].complete_with_tokens(turn.messages)
        except (LlmRequestError, OSError, ValueError) as exc:
            turn.error = str(exc)
            result.stop_reason = "transport_error" if isinstance(exc, (LlmRequestError, OSError)) else "provider_error"
            result.errors.append(f"Iteration {turn.iteration} {turn.agent_name}: {exc}")
            runtime_blockers.append({"code": "AGENT_REQUEST_FAILED", "path": turn.agent_name,
                                     "message": f"{result.stop_reason}: {exc}"})
            _assess_result(result, groups, normalization_warnings, runtime_blockers)
            return False
        result.total_tokens += turn.tokens_used
        if agent_cfg.verbose:
            print(f"[{turn.agent_name}] iter={turn.iteration} tokens={turn.tokens_used}")
        return True

    for iteration in range(agent_cfg.max_iterations):
        iteration_turns: dict[str, AgentTurn] = {}
        result.iterations.append(iteration_turns)

        # 3a. Dispatch sub-agents
        for spec_name, spec in SUB_AGENT_SPECS.items():
            focused = _focus_slices_for_sub_agent(active_slices, spec, active_probe_tags)
            prompt = _build_sub_agent_prompt(spec, focused, entity_map)

            messages = [
                AgentMessage(role="system", content=spec.system_prompt),
                AgentMessage(role="user", content=prompt),
            ]
            turn = AgentTurn(
                agent_name=spec_name,
                iteration=iteration,
                messages=messages,
            )
            iteration_turns[spec_name] = turn
            if not complete_turn(turn, "sub_agent"):
                return result

        # 3b. Run manager
        manager_prompt = _build_manager_prompt(
            iteration_turns, groups, entity_map, strategy,
            evidence_slices=active_slices, validation_feedback=feedback,
            previous_response=previous_manager_response,
        )
        manager_messages = [
            AgentMessage(role="system", content=MANAGER_SYSTEM_PROMPT),
            AgentMessage(role="user", content=manager_prompt),
        ]
        manager_turn = AgentTurn(
            agent_name="manager",
            iteration=iteration,
            messages=manager_messages,
        )
        iteration_turns["manager"] = manager_turn
        result.final_turn = manager_turn
        if not complete_turn(manager_turn, "manager"):
            return result
        manager_text = manager_turn.response or ""
        previous_manager_response = manager_text
        runtime_blockers = []

        # 3c. Parse manager output
        parsed = _parse_manager_json(manager_text)
        if parsed is None:
            message = "Manager must return one strict JSON object (raw or fenced); duplicate keys, non-finite numbers and trailing text are rejected."
            result.errors.append(f"Iteration {iteration}: failed to parse manager JSON")
            runtime_blockers.append({"code": "INVALID_MANAGER_JSON", "path": "report", "message": message})
            feedback = _assess_result(result, groups, normalization_warnings, runtime_blockers)
            continue

        try:
            normalized, warnings = _normalize_manager_report(parsed)
        except RecursionError:
            message = "Manager JSON exceeds supported normalization nesting; return a shallower complete report."
            result.errors.append(f"Iteration {iteration}: {message}")
            runtime_blockers.append({"code": "INVALID_MANAGER_JSON", "path": "report", "message": message})
            feedback = _assess_result(result, groups, normalization_warnings, runtime_blockers)
            continue
        result.integrated_report = normalized
        normalization_warnings.extend({**warning, "iteration": iteration} for warning in warnings)
        result.pending_re_probe = None
        # Group reports may also request probes; none may disappear merely
        # because the manager used the multi-ANR envelope.
        probe_reports = [("report", parsed, None)]
        children = parsed.get("groups")
        if isinstance(children, list):
            probe_reports.extend((f"report.groups[{index}]", child, child.get("groupId"))
                                 for index, child in enumerate(children) if isinstance(child, dict))
        re_probes = []
        pending = []
        for path, probe_report, group_id in probe_reports:
            if probe_report.get("reProbeRequest") is None:
                continue
            raw_probe = probe_report["reProbeRequest"]
            try:
                request = _parse_re_probe_request(probe_report)
                if path != "report" and (not isinstance(group_id, str) or group_id not in {group.get("id") for group in groups}):
                    raise ValueError("reProbeRequest must belong to a known groupId")
            except ValueError as exc:
                value = deepcopy(raw_probe) if isinstance(raw_probe, dict) else {"invalidRequest": deepcopy(raw_probe)}
                if group_id is not None:
                    value["groupId"] = group_id
                pending.append(value)
                result.errors.append(f"Iteration {iteration} {path}: {exc}")
                runtime_blockers.append({"code": "INVALID_REPROBE_REQUEST", "path": f"{path}.reProbeRequest", "message": str(exc)})
                continue
            request = replace(request, group_id=group_id)
            re_probes.append(request)
            result.re_probe_requests.append(request)
            pending.append(_probe_dict(request))
        if pending:
            result.pending_re_probe = pending[0] if len(pending) == 1 else {"requests": pending}
        if runtime_blockers:
            feedback = _assess_result(result, groups, normalization_warnings, runtime_blockers)
            continue

        feedback = _assess_result(result, groups, normalization_warnings)

        if re_probes:
            if iteration == agent_cfg.max_iterations - 1:
                break
            additional = []
            for request in re_probes:
                additional.extend(_execute_re_probe(groups, request, strategy))
                active_probe_tags.extend(request.additional_tags)
                if agent_cfg.verbose:
                    print(f"[re-probe] iter={iteration} group={request.group_id} window={request.before_seconds}s/{request.after_seconds}s tags={request.additional_tags}")
            active_slices = _merge_probe_slices(active_slices, additional, agent_cfg.truncation)
            active_probe_tags = list(dict.fromkeys(active_probe_tags))
            result.pending_re_probe = None
            continue

        if feedback["status"] in DELIVERABLE_STATUSES:
            result.stop_reason = "accepted"
            _assess_result(result, groups, normalization_warnings)
            return result
        result.errors.append(f"Iteration {iteration}: manager report validation status {feedback['status']}")

    result.stop_reason = "iteration_limit"
    limit_issue = {"code": "AGENT_ITERATION_LIMIT", "path": "max_iterations",
                   "message": "Iteration budget exhausted; retain unresolved requests and assess remaining assertions, not root-cause truth."}
    (normalization_warnings if result.integrated_report else runtime_blockers).append(limit_issue)
    _assess_result(result, groups, normalization_warnings, runtime_blockers)
    result.delivery_readiness["checks"]["runtime"] = "incomplete"
    result.errors.append("Max iterations reached without an accepted report.")
    return result


def _build_manager_prompt(
    sub_results: dict[str, AgentTurn],
    groups: list[dict[str, Any]],
    entity_map: EntityMap | None,
    strategy: AnrTypeStrategy,
    *,
    evidence_slices: list[EvidenceSlice] | None = None,
    validation_feedback: dict[str, Any] | None = None,
    previous_response: str | None = None,
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

    if evidence_slices is not None:
        event_ids: dict[tuple[str, str], list[str]] = {}
        for group in groups:
            for event in group.get("reconstruction", {}).get("events", []):
                if isinstance(event, dict) and isinstance(event.get("rawLine"), str) and isinstance(event.get("eventId"), str):
                    event_ids.setdefault((group.get("id"), event["rawLine"]), []).append(event["eventId"])
        lines.append("## Scoped evidence references (only current group IDs/raw lines may be cited)")
        lines.append(json.dumps([
            {"groupId": item.group_id, "source": item.source, "rawLine": item.content,
             "supportingEventIds": event_ids.get((item.group_id, item.content), [])}
            for item in evidence_slices
        ], ensure_ascii=False))
        lines.append("")

    incident_scenarios = []
    input_assessments = []
    for group in groups:
        scenario = group.get("incidentScenario")
        if scenario is None:
            scenario = group.get("reconstruction", {}).get("incidentScenario")
        if isinstance(scenario, dict):
            incident_scenarios.append({"groupId": group.get("id"), "incidentScenario": scenario})
        assessment = group.get("inputAnr")
        if assessment is None:
            assessment = group.get("reconstruction", {}).get("inputAnr")
        if isinstance(assessment, dict):
            input_assessments.append({"groupId": group.get("id"), "inputAnr": assessment})
    if incident_scenarios:
        lines.append("## 已有 ANR 发生场景（所有类型的派生摘要，不是新增观测）")
        lines.append(json.dumps(incident_scenarios, ensure_ascii=False, indent=2))
        lines.append("按 groupId 和当前 ANR 类型核验模型、步骤与缺失参数；派生摘要不升级为事实，不替代原始证据。")
        lines.append("")
    if input_assessments:
        lines.append("## 已有输入事件评估（派生摘要，不是新增观测）")
        lines.append(json.dumps(input_assessments, ensure_ascii=False, indent=2))
        lines.append("仅引用对应 groupId 内已有证据；派生字段或 AI 引用不升级为事实，不补造缺失身份。")
        lines.append("")

    if validation_feedback is not None:
        lines.append("## Runtime validation feedback")
        lines.append("The previous response was not accepted. Return a complete corrected report, preserving valid evidence and group scope; do not claim that references or causes are verified.")
        lines.append("Required arrays: candidateChains, candidateConclusions, remediationDrafts, globalLimitations, inputEventAssessments, anrModel, simulationSteps. Include conservative flags; anrModel and simulationSteps must contain candidates or explicit evidence gaps.")
        lines.append(json.dumps({"deliveryReadiness": validation_feedback, "previousResponse": previous_response}, ensure_ascii=False))
        lines.append("")

    lines.append("## 要求")
    lines.append(ANR_REPORT_REQUIREMENTS)
    lines.append("请严格按照 system prompt 中的 JSON 格式输出综合结论。只输出 JSON。")

    return "\n".join(lines)


def _parse_manager_json(text: str) -> dict[str, Any] | None:
    """Parse JSON output from the manager agent. Handles ```json fences."""
    if not text:
        return None
    json_text = text.strip()
    fence_match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", json_text, re.DOTALL | re.IGNORECASE)
    if fence_match:
        json_text = fence_match.group(1)
    try:
        parsed = strict_json_loads(json_text)
        return parsed if isinstance(parsed, dict) else None
    except ValueError:
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
