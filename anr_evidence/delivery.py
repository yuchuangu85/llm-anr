"""Final delivery markdown template on top of remediation drafts."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .incident_scenario import build_incident_scenario, render_incident_sections
from .input_anr import build_input_anr

REQUIRED_FIELDS = (
    "metadata",
    "classification",
    "anchors",
    "topConclusion",
    "remediationDrafts",
    "globalLimitations",
    "warnings",
)


class DeliveryError(ValueError):
    """Raised when a Phase 7 package cannot be delivered or safely rendered."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "DELIVERY_INVALID_PACKAGE",
        acceptance: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.acceptance = acceptance
        super().__init__(f"{code}: {message}")


def render_final_delivery(package: dict[str, Any], *, draft: bool = False) -> str:
    """Gate candidate delivery; explicit drafts never claim delivery readiness."""
    from .report_validation import DELIVERABLE_STATUSES, validate_phase_report

    try:
        acceptance = validate_phase_report(package)
    except (AttributeError, KeyError, TypeError) as exc:
        raise DeliveryError("Phase 7 validation encountered malformed report containers.") from exc
    if (
        not isinstance(acceptance, dict)
        or not isinstance(acceptance.get("status"), str)
        or acceptance["status"] not in {"blocked", "needs_review", *DELIVERABLE_STATUSES}
        or any(not isinstance(acceptance.get(field), list) for field in ("blockers", "reviewItems", "warnings"))
        or not isinstance(acceptance.get("checks"), dict)
        or not isinstance(acceptance.get("policyVersion"), str)
        or not acceptance["policyVersion"]
        or (acceptance["status"] in DELIVERABLE_STATUSES and (acceptance["blockers"] or acceptance["reviewItems"]))
    ):
        raise DeliveryError("The Phase validator did not return a valid acceptance result.", code="DELIVERY_INVALID_ACCEPTANCE")
    if acceptance["status"] not in DELIVERABLE_STATUSES and not draft:
        issues = acceptance["blockers"] + acceptance["reviewItems"]
        details = "; ".join(_acceptance_issue_text(issue) for issue in issues[:3])
        raise DeliveryError(
            f"Candidate report is {acceptance['status']}. {details}".strip(),
            code=f"DELIVERY_{acceptance['status'].upper()}",
            acceptance=acceptance,
        )
    try:
        return _render_delivery(_validate_phase7_package(package), acceptance, draft=draft)
    except (AttributeError, KeyError, TypeError) as exc:
        raise DeliveryError(
            "Phase 7 package contains malformed report containers.", acceptance=acceptance,
        ) from exc


def _render_delivery(package: dict[str, Any], acceptance: dict[str, Any], *, draft: bool) -> str:
    metadata = package["metadata"]
    classification = package["classification"]
    anchors = package["anchors"]
    top = package.get("topConclusion")
    remediations = package.get("remediationDrafts", [])
    warnings = package.get("warnings", [])
    limitations = package.get("globalLimitations", [])
    trace_insights = package.get("traceInsights", {})
    input_anr = package["inputAnr"] if "inputAnr" in package else build_input_anr(
        classification.get("detectedType"),
        (anchors or {}).get("primaryAnchor"),
        [],
    )
    incident_scenario = package.get("incidentScenario")
    if not incident_scenario:
        incident_scenario = build_incident_scenario(
            classification.get("detectedType"),
            (anchors or {}).get("primaryAnchor"),
            package.get("timeline") or [],
            input_anr=input_anr,
        )
    incident_scenario = deepcopy(incident_scenario)
    for field in ("anrModel", "simulationSteps", "missingParameters", "evidenceGaps"):
        if incident_scenario.get(field) is None:
            incident_scenario[field] = []

    lines: list[str] = []
    lines.append("# ANR 分析草稿（非交付稿）" if draft else "# ANR 分析交付稿")
    lines.append("")
    if draft:
        lines.append("> 草稿说明：本稿仅供检查，未作为可交付候选报告；`not_ready`。**不授予最终定责、修复验证或模拟执行结论。**")
    else:
        lines.append("> 交付说明：本稿为结构化 ANR 分析交付模板，包含候选结论与修复建议草稿。**不是最终定责结论，所有修复建议都需要人工确认。**")
    lines.append("")

    lines.append("## 一、执行摘要")
    lines.append("")
    if top:
        lines.append(f"- 当前最高优先级候选结论：{top.get('statement')}")
        lines.append(f"- 置信度：`{top.get('confidenceLevel')}`")
        lines.append(f"- 候选排序：rank `{top.get('rank')}` / score `{top.get('score')}`")
        if draft:
            lines.append(f"- finalAdvice（输入原值）: `{metadata.get('finalAdvice')}` / 草稿不授权最终建议")
        else:
            lines.append(f"- Final Judgment: `{metadata.get('finalAdvice', False)}` / `finalAdvice` 仍为 false")
    else:
        lines.append("- 当前没有足够证据形成可用的候选结论。")
    lines.append("")

    lines.extend(render_incident_sections(incident_scenario, deepcopy(input_anr)))

    lines.append("## 二、上下文与分类")
    lines.append("")
    lines.append(f"- Package ID: `{metadata.get('packageId')}`")
    lines.append(f"- Delivery Phase: `{metadata.get('phase')}` / `{metadata.get('schemaVersion')}`")
    lines.append(f"- Status: `{metadata.get('status')}`")
    lines.append(f"- Detected Type: `{classification.get('detectedType')}`")
    lines.append(f"- Supported: `{classification.get('supported')}`")
    lines.append(f"- Fallback Mode: `{classification.get('fallbackMode')}`")
    lines.append("")

    lines.append("## 三、时间锚点与输入完整性")
    lines.append("")
    primary = anchors.get("primaryAnchor")
    if primary:
        lines.append(f"- Primary Anchor: `{primary.get('sourceKind')}` @ `{primary.get('timestamp')}`")
    else:
        lines.append("- Primary Anchor: `None`")
    secondary = anchors.get("secondaryAnchors") or []
    lines.append(f"- Secondary Anchors: `{len(secondary)}`")
    if warnings:
        lines.append("- Warnings:")
        for warning in warnings:
            lines.append(f"  - `{warning.get('code')}`: {warning.get('message')}")
    else:
        lines.append("- Warnings: `None`")
    lines.append("")

    lines.append("## 四、最高优先级候选结论")
    lines.append("")
    if top:
        lines.append(f"- Statement: {top.get('statement')}")
        lines.append(f"- Signal Category: `{top.get('signalCategory')}`")
        lines.append(f"- Confidence: `{top.get('confidenceLevel')}`")
        lines.append("- Why Not Final:")
        for item in top.get("whyNotFinal", []):
            lines.append(f"  - {item}")
        lines.append("- Unresolved Questions:")
        for item in top.get("unresolvedQuestions", []):
            lines.append(f"  - {item}")
        snippets = top.get("supportingSnippets", [])
        if snippets:
            lines.append("- Supporting Snippets:")
            for snippet in snippets:
                lines.append(
                    f"  - `[{snippet.get('timestamp')}] {snippet.get('sourceKind')}/{snippet.get('recordType')}`: {snippet.get('summary')}"
                )
    else:
        lines.append("- 无可用候选结论")
    lines.append("")

    lines.append("## 五、主线程关键信息")
    lines.append("")
    main_thread = trace_insights.get("mainThread", {})
    if main_thread.get("captured"):
        lines.append(f"- Thread: `{main_thread.get('threadName')}` tid=`{main_thread.get('tid')}` sysTid=`{main_thread.get('sysTid')}`")
        lines.append(f"- Block Hint: `{main_thread.get('blockHint')}`")
        lines.append(f"- Native Top Frame: `{main_thread.get('nativeTopFrame')}`")
        lines.append(f"- Java Top Frame: `{main_thread.get('javaTopFrame')}`")
        lines.append(f"- Looper Frame: `{main_thread.get('looperFrame')}`")
    else:
        lines.append("- 当前无主线程专门摘要")
    lines.append("")

    lines.append("## 六、修复建议草稿（需人工确认）")
    lines.append("")
    if remediations:
        for draft_item in remediations:
            lines.append(f"### {draft_item.get('title')}")
            lines.append("")
            lines.append(f"- Rank: `{draft_item.get('rank')}` / Priority: `{draft_item.get('priority')}`")
            lines.append(f"- Derived From: `{draft_item.get('derivedFromSignalCategory')}`")
            lines.append(f"- requiresHumanConfirmation: `{draft_item.get('requiresHumanConfirmation')}`")
            lines.append(f"- Action Draft: {draft_item.get('actionDraft')}")
            if draft_item.get("supportingSnippets"):
                lines.append("- Supporting Snippets:")
                for snippet in draft_item.get("supportingSnippets", []):
                    lines.append(
                        f"  - `[{snippet.get('timestamp')}] {snippet.get('sourceKind')}/{snippet.get('recordType')}`: {snippet.get('summary')}"
                    )
            lines.append("- Why Gated:")
            for item in draft_item.get("whyGated", []):
                lines.append(f"  - {item}")
            lines.append("")
    else:
        lines.append("- 当前无修复建议草稿")
        lines.append("")

    lines.append("## 七、全局限制")
    lines.append("")
    for item in limitations:
        lines.append(f"- {item}")
    if not limitations:
        lines.append("- 当前无额外全局限制")
    lines.append("")

    lines.append("## 八、交付说明")
    lines.append("")
    lines.append("- 该草稿未获准交付，仅供检查验收问题。" if draft else "- 该交付稿适合作为人工排查、评审和修复讨论的起点。")
    lines.append("- 若要形成最终根因结论，必须继续做人工/规则复核。")
    lines.append("- 若要形成最终修复方案，必须在结论确认后再审阅 remediation drafts。")
    lines.append("")
    lines.extend(_acceptance_section(acceptance, draft=draft))
    return "\n".join(lines)


def _acceptance_issue_text(issue: Any) -> str:
    if isinstance(issue, dict):
        return f"{issue.get('code', 'UNSPECIFIED')}: {issue.get('message', '')}"
    return str(issue)


def _acceptance_section(acceptance: dict[str, Any], *, draft: bool) -> list[str]:
    readiness = "not_ready (draft)" if draft else acceptance["status"]
    lines = [
        "## 九、交付验收", "",
        f"- Candidate readiness: `{readiness}` / policyVersion: `{acceptance['policyVersion']}`",
        "- 就绪仅指候选报告可供人工评审，不表示根因已确认、修复已验证或模拟已执行。",
    ]
    if draft:
        lines.append(f"- Validation result（仅诊断）: `{acceptance['status']}`；草稿不作为验收通过的交付物。")
    for field in ("blockers", "reviewItems", "warnings"):
        issues = acceptance[field]
        lines.append(f"- {field}: `{len(issues)}`")
        for issue in issues[:3]:
            lines.append(f"  - {_acceptance_issue_text(issue)}")
        if len(issues) > 3:
            lines.append(f"  - 另有 {len(issues) - 3} 项；完整结果见 validate_phase_report。")
    return lines + [""]


def _validate_phase7_package(package: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(package, dict):
        raise DeliveryError("Final delivery renderer expects a dict-like Phase 7 remediation draft package.")
    missing = [field for field in REQUIRED_FIELDS if field not in package]
    if missing:
        raise DeliveryError(f"Phase 7 remediation draft package is missing required fields: {', '.join(missing)}")
    metadata = package.get("metadata", {})
    if not isinstance(metadata, dict) or metadata.get("phase") != "phase7-remediation-draft":
        raise DeliveryError("Final delivery renderer expects a Phase 7 remediation draft package.")
    for field in ("classification", "anchors", "traceInsights"):
        if not isinstance(package.get(field, {}), dict):
            raise DeliveryError(f"Phase 7 report container {field} must be an object.")
    for field in ("topConclusion", "incidentScenario", "inputAnr"):
        if package.get(field) is not None and not isinstance(package[field], dict):
            raise DeliveryError(f"Phase 7 report container {field} must be an object or null.")
    for field in ("remediationDrafts", "globalLimitations", "warnings", "timeline"):
        if not isinstance(package.get(field, []), list):
            raise DeliveryError(f"Phase 7 report container {field} must be an array.")
    return package
