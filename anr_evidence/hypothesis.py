"""Deterministic candidate causal-chain drafts on top of Phase 3 assisted analysis."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha1
from typing import Any

REQUIRED_FIELDS = ("metadata", "classification", "anchors", "signalSummary", "timeline", "findings", "warnings")

CATEGORY_TITLES = {
    "focus_window_issue": "焦点窗口链路候选",
    "input_dispatch_timeout": "输入分发阻塞链路候选",
    "input_dispatch_signal": "输入分发信号链路候选",
    "binder_or_ipc_pressure": "Binder/IP C 压力链路候选",
    "scheduler_pressure": "调度压力链路候选",
}

CATEGORY_TRACE_HINTS = {
    "focus_window_issue": {"focus_window_wait"},
    "focus_window_signal": {"focus_window_wait"},
    "input_dispatch_timeout": {"input_dispatch_wait"},
    "input_dispatch_signal": {"input_dispatch_wait"},
    "binder_or_ipc_pressure": {"binder_wait", "binder_backlog", "binder_reply_wait"},
    "scheduler_pressure": {"futex_wait", "native_poll_wait", "gpu_wait", "lock_contention", "monitor_contention"},
}


class HypothesisError(ValueError):
    """Raised when the input is not a valid Phase 3 assisted analysis package."""


def generate_causal_draft(package: dict[str, Any]) -> dict[str, Any]:
    analysis = _validate_phase3_package(package)
    chains = _build_candidate_chains(analysis)
    return {
        "metadata": {
            "packageId": analysis["metadata"]["packageId"],
            "phase": "phase5-causal-draft",
            "schemaVersion": "phase5-causal-draft-v1",
            "upstreamPhase": analysis["metadata"].get("phase", "phase3-assisted-analysis"),
            "status": analysis["metadata"]["status"],
        },
        "classification": deepcopy(analysis["classification"]),
        "anchors": deepcopy(analysis["anchors"]),
        "traceInsights": deepcopy(analysis.get("signalSummary", {}).get("traceInsights", {})),
        "candidateChains": chains,
        "warnings": deepcopy(analysis.get("warnings", [])),
    }


def _validate_phase3_package(package: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(package, dict):
        raise HypothesisError("Causal draft generator expects a dict-like Phase 3 assisted analysis package.")
    missing = [field for field in REQUIRED_FIELDS if field not in package]
    if missing:
        raise HypothesisError(f"Phase 3 analysis package is missing required fields: {', '.join(missing)}")
    metadata = package.get("metadata", {})
    if metadata.get("phase") != "phase3-assisted-analysis":
        raise HypothesisError("Causal draft generator expects a Phase 3 assisted analysis package.")
    return package


def _build_candidate_chains(package: dict[str, Any]) -> list[dict[str, Any]]:
    categories = package.get("signalSummary", {}).get("signalCategoryCounts", {})
    trace_insights = package.get("signalSummary", {}).get("traceInsights", {})
    timeline = package.get("timeline", [])
    classification = package.get("classification", {})
    chains: list[dict[str, Any]] = []

    sorted_categories = sorted(categories.items(), key=lambda item: (-item[1], item[0]))
    for category, count in sorted_categories[:3]:
        related = [entry for entry in timeline if entry.get("signalCategory") == category]
        evidence_refs = [entry["recordRef"] for entry in related[:5]]
        confidence = _confidence_level(classification, count, package["metadata"]["status"])
        trace_context = _trace_context_for(category, trace_insights)
        score = _chain_score(confidence, count, related, trace_context)
        chains.append(
            {
                "chainId": _stable_id(package["metadata"]["packageId"], category),
                "title": CATEGORY_TITLES.get(category, f"{category} 候选链路"),
                "rank": 0,
                "score": score,
                "confidenceLevel": confidence,
                "signalCategory": category,
                "traceDominantBlockHint": trace_context.get("dominantBlockHint"),
                "traceSuspiciousThreadTotal": trace_context.get("suspiciousThreadTotal"),
                "evidenceRefs": evidence_refs,
                "evidenceSnippets": _evidence_snippets(related),
                "rationale": _rationale_for(category, count, related, trace_context),
                "limitations": _limitations_for(package, classification, trace_context),
                "notRootCauseYet": True,
            }
        )

    if not chains:
        chains.append(
            {
                "chainId": _stable_id(package["metadata"]["packageId"], "fallback"),
                "title": "证据不足的保守候选链路",
                "rank": 0,
                "score": 10,
                "confidenceLevel": "low",
                "signalCategory": None,
                "traceDominantBlockHint": trace_insights.get("topSuspiciousRecords", [{}])[0].get("dominantBlockHint") if trace_insights.get("topSuspiciousRecords") else None,
                "traceSuspiciousThreadTotal": trace_insights.get("suspiciousThreadTotal"),
                "evidenceRefs": [entry["recordRef"] for entry in timeline[:3]],
                "evidenceSnippets": _evidence_snippets(timeline[:3]),
                "rationale": "当前辅助分析未提取到足够稳定的信号类别，只能保守保留证据结构，不形成明确候选链路。",
                "limitations": _limitations_for(package, classification, _trace_context_for(None, trace_insights)),
                "notRootCauseYet": True,
            }
        )
    chains = sorted(chains, key=lambda chain: (-chain["score"], chain["title"], chain["chainId"]))
    for index, chain in enumerate(chains, start=1):
        chain["rank"] = index
    return chains


def _confidence_level(classification: dict[str, Any], signal_count: int, status: str) -> str:
    if status == "degraded" or classification.get("fallbackMode") not in (None, "none"):
        return "low"
    if signal_count >= 2 and classification.get("supported"):
        return "medium"
    return "low"


def _rationale_for(category: str, count: int, related: list[dict[str, Any]], trace_context: dict[str, Any]) -> str:
    first = related[0] if related else None
    first_summary = first.get("summary") if first else "无具体摘要"
    rationale = (
        f"观察到 {count} 条与 `{category}` 相关的标准化信号；"
        f"最早相关条目摘要为：{first_summary}。"
        "这提示可以把该信号链路作为后续显式 root-cause 分析的候选入口，但当前还不能据此下最终结论。"
    )
    dominant_hint = trace_context.get("dominantBlockHint")
    if dominant_hint:
        rationale += f" 同时，trace 侧主导阻塞模式为 `{dominant_hint}`，为该候选链路提供了附加上下文。"
    return rationale


def _limitations_for(package: dict[str, Any], classification: dict[str, Any], trace_context: dict[str, Any]) -> list[str]:
    limitations = ["该结果只是候选因果链草稿，不是最终根因结论。"]
    status = package["metadata"].get("status")
    if status != "complete":
        limitations.append(f"上游 package 状态为 `{status}`，说明输入完整性不足。")
    fallback = classification.get("fallbackMode")
    if fallback and fallback != "none":
        limitations.append(f"当前仍处于 fallback 模式：`{fallback}`。")
    if package.get("warnings"):
        limitations.append("上游存在 warnings，候选链路应结合原始证据复核。")
    dominant_hint = trace_context.get("dominantBlockHint")
    if dominant_hint:
        limitations.append(f"trace 侧主导阻塞模式为 `{dominant_hint}`，但它仍可能只是放大信号而非主因。")
    return limitations


def _evidence_snippets(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked_entries = sorted(entries, key=lambda entry: (-_entry_score(entry), entry.get("recordRef") or ""))
    snippets = []
    for index, entry in enumerate(ranked_entries[:3], start=1):
        snippets.append(
            {
                "rank": index,
                "score": _entry_score(entry),
                "recordRef": entry.get("recordRef"),
                "timestamp": entry.get("timestamp"),
                "sourceKind": entry.get("sourceKind"),
                "recordType": entry.get("recordType"),
                "summary": entry.get("summary"),
            }
        )
    return snippets


def _chain_score(confidence: str, signal_count: int, related: list[dict[str, Any]], trace_context: dict[str, Any]) -> int:
    confidence_score = {"low": 20, "medium": 50, "high": 80}.get(confidence, 0)
    trace_bonus = 0
    if trace_context.get("dominantBlockHint"):
        trace_bonus += 8 if trace_context.get("hintAligned") else 3
    suspicious_total = trace_context.get("suspiciousThreadTotal") or 0
    trace_bonus += min(suspicious_total, 3)
    return confidence_score + min(signal_count, 5) * 10 + sum(_entry_score(entry) for entry in related[:3]) + trace_bonus


def _trace_context_for(category: str | None, trace_insights: dict[str, Any]) -> dict[str, Any]:
    dominant_counts = trace_insights.get("dominantBlockHintCounts", {})
    dominant_hint = None
    if dominant_counts:
        dominant_hint = sorted(dominant_counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
    relevant_hints = CATEGORY_TRACE_HINTS.get(category, set())
    return {
        "dominantBlockHint": dominant_hint,
        "suspiciousThreadTotal": trace_insights.get("suspiciousThreadTotal", 0),
        "hintAligned": bool(dominant_hint and dominant_hint in relevant_hints),
    }


def _entry_score(entry: dict[str, Any]) -> int:
    tier_score = {"P0": 30, "P1": 15, "P2": 5}.get(entry.get("tier"), 0)
    source_bonus = {"event_log": 8, "trace": 7, "logcat": 6, "kernel_log": 5}.get(entry.get("sourceKind"), 0)
    category_bonus = 5 if entry.get("signalCategory") else 0
    return tier_score + source_bonus + category_bonus


def _stable_id(*parts: str) -> str:
    return sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]
