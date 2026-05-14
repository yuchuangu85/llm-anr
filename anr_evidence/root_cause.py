"""Conservative root-cause report v1 built on top of Phase 5 causal drafts."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha1
from typing import Any

REQUIRED_FIELDS = ("metadata", "classification", "anchors", "candidateChains", "warnings")

CATEGORY_STATEMENTS = {
    "focus_window_issue": "当前更像是焦点窗口相关链路导致的 ANR 候选结论。",
    "focus_window_signal": "当前更像是焦点窗口不可用/未建立链路导致的 ANR 候选结论。",
    "input_dispatch_timeout": "当前更像是输入分发超时链路导致的 ANR 候选结论。",
    "input_dispatch_signal": "当前更像是输入分发阻塞/超时链路导致的 ANR 候选结论。",
    "binder_or_ipc_pressure": "当前存在 Binder / IPC 压力信号，可能放大或触发 ANR。",
    "scheduler_pressure": "当前存在调度压力信号，可能放大线程阻塞或响应迟滞。",
    None: "当前证据不足，只能保守保留候选结论，不能形成明确 root-cause 候选。",
}


class RootCauseError(ValueError):
    """Raised when the input is not a valid Phase 5 causal draft package."""


def generate_root_cause_report(package: dict[str, Any]) -> dict[str, Any]:
    draft = _validate_phase5_package(package)
    candidate_conclusions = _candidate_conclusions(draft)
    top_conclusion = candidate_conclusions[0] if candidate_conclusions else None
    return {
        "metadata": {
            "packageId": draft["metadata"]["packageId"],
            "phase": "phase6-root-cause-report-v1",
            "schemaVersion": "phase6-root-cause-v1",
            "upstreamPhase": draft["metadata"].get("phase", "phase5-causal-draft"),
            "status": draft["metadata"]["status"],
            "conclusionMode": "conservative",
            "finalJudgment": False,
        },
        "classification": deepcopy(draft["classification"]),
        "anchors": deepcopy(draft["anchors"]),
        "traceInsights": deepcopy(draft.get("traceInsights", {})),
        "candidateChains": deepcopy(draft.get("candidateChains", [])),
        "topConclusion": deepcopy(top_conclusion),
        "candidateConclusions": candidate_conclusions,
        "globalLimitations": _global_limitations(draft),
        "warnings": deepcopy(draft.get("warnings", [])),
    }


def _validate_phase5_package(package: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(package, dict):
        raise RootCauseError("Root-cause report generator expects a dict-like Phase 5 causal draft package.")
    missing = [field for field in REQUIRED_FIELDS if field not in package]
    if missing:
        raise RootCauseError(f"Phase 5 causal draft package is missing required fields: {', '.join(missing)}")
    metadata = package.get("metadata", {})
    if metadata.get("phase") != "phase5-causal-draft":
        raise RootCauseError("Root-cause report generator expects a Phase 5 causal draft package.")
    return package


def _candidate_conclusions(draft: dict[str, Any]) -> list[dict[str, Any]]:
    conclusions = []
    for chain in draft.get("candidateChains", []):
        signal_category = chain.get("signalCategory")
        dominant_hint = chain.get("traceDominantBlockHint")
        conclusions.append(
            {
                "conclusionId": _stable_id(draft["metadata"]["packageId"], chain.get("chainId", "")),
                "rank": chain.get("rank"),
                "score": chain.get("score"),
                "confidenceLevel": chain.get("confidenceLevel"),
                "statement": _statement_for(signal_category, dominant_hint),
                "signalCategory": signal_category,
                "traceDominantBlockHint": dominant_hint,
                "supportingEvidenceRefs": list(chain.get("evidenceRefs", [])),
                "supportingSnippets": deepcopy(chain.get("evidenceSnippets", [])),
                "whyNotFinal": _why_not_final(draft, chain),
                "unresolvedQuestions": _unresolved_questions(draft, chain),
                "tentative": True,
            }
        )
    return conclusions


def _statement_for(signal_category: str | None, dominant_hint: str | None) -> str:
    statement = CATEGORY_STATEMENTS.get(signal_category, f"当前更像是 `{signal_category}` 相关链路导致的 ANR 候选结论。")
    if dominant_hint:
        statement += f" 结合 trace 侧主导阻塞模式 `{dominant_hint}`，该候选结论值得优先排查。"
    return statement


def _why_not_final(draft: dict[str, Any], chain: dict[str, Any]) -> list[str]:
    reasons = ["该结论来自候选因果链排序结果，不是最终根因裁决。"]
    if chain.get("confidenceLevel") == "low":
        reasons.append("当前候选链置信度较低，只适合作为排查入口。")
    if draft["metadata"].get("status") != "complete":
        reasons.append(f"上游状态为 `{draft['metadata'].get('status')}`，输入完整性不足。")
    fallback = draft.get("classification", {}).get("fallbackMode")
    if fallback and fallback != "none":
        reasons.append(f"当前仍处于 fallback 模式：`{fallback}`。")
    if chain.get("traceDominantBlockHint"):
        reasons.append(f"trace 主导阻塞模式 `{chain.get('traceDominantBlockHint')}` 只能增强候选可信度，不能单独完成定责。")
    return reasons


def _unresolved_questions(draft: dict[str, Any], chain: dict[str, Any]) -> list[str]:
    questions = ["是否存在尚未纳入当前 evidence package 的关键 trace / logcat / kernel 片段？"]
    if chain.get("signalCategory") in {"focus_window_issue", "focus_window_signal"}:
        questions.append("焦点窗口未建立是主因，还是被其他阻塞链路放大后的表象？")
    if chain.get("signalCategory") == "input_dispatch_timeout":
        questions.append("输入分发超时是主链路阻塞，还是被 Binder/调度压力间接放大？")
    if chain.get("signalCategory") == "binder_or_ipc_pressure":
        questions.append("Binder / IPC 压力是根因候选，还是伴随信号？")
    if chain.get("traceDominantBlockHint"):
        questions.append(f"trace 主导阻塞模式 `{chain.get('traceDominantBlockHint')}` 是根因链路本身，还是只是下游表征？")
    if draft.get("classification", {}).get("fallbackMode") not in (None, "none"):
        questions.append("当前类型识别不稳定，是否需要人工复核 ANR 类型与时间锚点？")
    return questions


def _global_limitations(draft: dict[str, Any]) -> list[str]:
    limitations = ["本输出是保守版 root-cause report v1，提供候选结论，不提供最终定责。"]
    if draft.get("warnings"):
        limitations.append("上游存在 warnings，所有候选结论都需要结合原始证据复核。")
    if draft.get("metadata", {}).get("status") != "complete":
        limitations.append("由于输入不是 complete 状态，结论可靠性天然受限。")
    if any(chain.get("traceDominantBlockHint") for chain in draft.get("candidateChains", [])):
        limitations.append("trace 阻塞模式只作为辅助证据使用，仍不能替代人工复核 wait chain / lock owner / binder 上下文。")
    return limitations


def _stable_id(*parts: str) -> str:
    return sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]
