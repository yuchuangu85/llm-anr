"""Gated remediation draft generation on top of conservative root-cause reports."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha1
from typing import Any

REQUIRED_FIELDS = (
    "metadata",
    "classification",
    "anchors",
    "candidateChains",
    "topConclusion",
    "candidateConclusions",
    "globalLimitations",
    "warnings",
)

REMEDIATION_TEMPLATES = {
    "focus_window_issue": [
        ("确认焦点窗口建立链路", "检查窗口创建、显示与焦点切换时序，确认焦点窗口是否在 ANR 前已经可见并可接收输入。"),
        ("核对窗口焦点丢失条件", "检查最近的 activity/window 切换、弹窗、overlay 或 keyguard 状态，确认是否存在异常抢占焦点。"),
    ],
    "focus_window_signal": [
        ("复核焦点窗口前置条件", "检查 UI 初始化、窗口 attach、resume 与 first draw 是否在输入到达前完成。"),
        ("排查表象型焦点缺失", "结合 Binder/调度压力证据确认焦点缺失是否只是更上游阻塞的表象。"),
    ],
    "input_dispatch_timeout": [
        ("检查主线程/输入处理阻塞", "重点复核主线程、InputDispatcher 相关调用链和输入事件处理耗时。"),
        ("对照 Binder/调度信号", "确认输入分发超时是直接阻塞，还是被 IPC 或调度压力间接放大。"),
    ],
    "input_dispatch_signal": [
        ("检查输入分发信号来源", "确认 trace/logcat 中的输入分发阻塞信号是否对应真实超时主链路，而不是伴随表象。"),
        ("复核输入处理与主线程耦合", "检查输入事件处理逻辑与主线程阻塞点是否发生耦合。"),
    ],
    "binder_or_ipc_pressure": [
        ("排查 IPC 背压源头", "检查 binder backlog、跨进程调用密度和等待链，识别可能的阻塞源。"),
        ("区分主因与伴随信号", "确认 Binder/IP C 压力是根因候选，还是由上游 UI/输入链路问题引发。"),
    ],
    "scheduler_pressure": [
        ("检查调度与资源竞争", "复核 CPU 抢占、调度延迟和线程优先级，确认是否存在明显资源竞争。"),
        ("关联其他候选链路", "确认调度压力是否只是放大器，而非最初触发点。"),
    ],
    None: [
        ("先补充证据", "当前只能保守建议先补充更完整的 trace/logcat/kernel/event 证据，再进入进一步分析。"),
    ],
}


class RemediationError(ValueError):
    """Raised when the input is not a valid Phase 6 root-cause report package."""



def generate_remediation_drafts(package: dict[str, Any]) -> dict[str, Any]:
    report = _validate_phase6_package(package)
    drafts = _build_remediation_drafts(report)
    return {
        "metadata": {
            "packageId": report["metadata"]["packageId"],
            "phase": "phase7-remediation-draft",
            "schemaVersion": "phase7-remediation-v1",
            "upstreamPhase": report["metadata"].get("phase", "phase6-root-cause-report-v1"),
            "status": report["metadata"]["status"],
            "adviceMode": "gated-draft",
            "finalAdvice": False,
        },
        "classification": deepcopy(report["classification"]),
        "anchors": deepcopy(report["anchors"]),
        "traceInsights": deepcopy(report.get("traceInsights", {})),
        "candidateChains": deepcopy(report.get("candidateChains", [])),
        "topConclusion": deepcopy(report.get("topConclusion")),
        "candidateConclusions": deepcopy(report.get("candidateConclusions", [])),
        "remediationDrafts": drafts,
        "globalLimitations": deepcopy(report.get("globalLimitations", [])),
        "warnings": deepcopy(report.get("warnings", [])),
    }



def _validate_phase6_package(package: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(package, dict):
        raise RemediationError("Remediation draft generator expects a dict-like Phase 6 root-cause report package.")
    missing = [field for field in REQUIRED_FIELDS if field not in package]
    if missing:
        raise RemediationError(f"Phase 6 root-cause report package is missing required fields: {', '.join(missing)}")
    metadata = package.get("metadata", {})
    if metadata.get("phase") != "phase6-root-cause-report-v1":
        raise RemediationError("Remediation draft generator expects a Phase 6 root-cause report package.")
    return package



def _build_remediation_drafts(report: dict[str, Any]) -> list[dict[str, Any]]:
    drafts: list[dict[str, Any]] = []
    for conclusion in report.get("candidateConclusions", [])[:3]:
        signal_category = conclusion.get("signalCategory")
        templates = REMEDIATION_TEMPLATES.get(signal_category, REMEDIATION_TEMPLATES[None])
        for index, (title, action) in enumerate(templates, start=1):
            priority = _priority(conclusion.get("rank"), conclusion.get("confidenceLevel"), index)
            drafts.append(
                {
                    "draftId": _stable_id(report["metadata"]["packageId"], conclusion.get("conclusionId", ""), str(index)),
                    "rank": len(drafts) + 1,
                    "priority": priority,
                    "title": title,
                    "derivedFromConclusionId": conclusion.get("conclusionId"),
                    "derivedFromSignalCategory": signal_category,
                    "actionDraft": action,
                    "supportingEvidenceRefs": deepcopy(conclusion.get("supportingEvidenceRefs", []))[:3],
                    "supportingSnippets": deepcopy(conclusion.get("supportingSnippets", []))[:2],
                    "whyGated": _why_gated(report, conclusion),
                    "requiresHumanConfirmation": True,
                }
            )
    drafts.sort(key=lambda item: (item["priority"], item["rank"], item["draftId"]))
    for idx, draft in enumerate(drafts, start=1):
        draft["rank"] = idx
    return drafts



def _priority(rank: Any, confidence: str | None, template_index: int) -> int:
    confidence_base = {"medium": 10, "low": 20, "high": 5}.get(confidence or "low", 20)
    rank_penalty = (int(rank) if isinstance(rank, int) else 9) * 10
    return confidence_base + rank_penalty + template_index



def _why_gated(report: dict[str, Any], conclusion: dict[str, Any]) -> list[str]:
    reasons = ["修复建议基于候选结论草稿生成，不能直接当作最终修复方案。"]
    if not report.get("metadata", {}).get("finalJudgment", False):
        reasons.append("当前 root-cause 仍是 conservative 模式，尚未最终定责。")
    reasons.extend(conclusion.get("whyNotFinal", []))
    return reasons



def _stable_id(*parts: str) -> str:
    return sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]
