"""Final delivery markdown template on top of remediation drafts."""

from __future__ import annotations

from typing import Any

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
    """Raised when the input is not a valid Phase 7 remediation draft package."""



def render_final_delivery(package: dict[str, Any]) -> str:
    draft = _validate_phase7_package(package)
    metadata = draft["metadata"]
    classification = draft["classification"]
    anchors = draft["anchors"]
    top = draft.get("topConclusion")
    remediations = draft.get("remediationDrafts", [])
    warnings = draft.get("warnings", [])
    limitations = draft.get("globalLimitations", [])
    trace_insights = draft.get("traceInsights", {})

    lines: list[str] = []
    lines.append("# ANR 分析交付稿")
    lines.append("")
    lines.append("> 交付说明：本稿为结构化 ANR 分析交付模板，包含候选结论与修复建议草稿。**不是最终定责结论，所有修复建议都需要人工确认。**")
    lines.append("")

    lines.append("## 一、执行摘要")
    lines.append("")
    if top:
        lines.append(f"- 当前最高优先级候选结论：{top.get('statement')}")
        lines.append(f"- 置信度：`{top.get('confidenceLevel')}`")
        lines.append(f"- 候选排序：rank `{top.get('rank')}` / score `{top.get('score')}`")
        lines.append(f"- Final Judgment: `{metadata.get('finalAdvice', False)}` / `finalAdvice` 仍为 false")
    else:
        lines.append("- 当前没有足够证据形成可用的候选结论。")
    lines.append("")

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
    lines.append("- 该交付稿适合作为人工排查、评审和修复讨论的起点。")
    lines.append("- 若要形成最终根因结论，必须继续做人工/规则复核。")
    lines.append("- 若要形成最终修复方案，必须在结论确认后再审阅 remediation drafts。")
    lines.append("")
    return "\n".join(lines)



def _validate_phase7_package(package: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(package, dict):
        raise DeliveryError("Final delivery renderer expects a dict-like Phase 7 remediation draft package.")
    missing = [field for field in REQUIRED_FIELDS if field not in package]
    if missing:
        raise DeliveryError(f"Phase 7 remediation draft package is missing required fields: {', '.join(missing)}")
    metadata = package.get("metadata", {})
    if metadata.get("phase") != "phase7-remediation-draft":
        raise DeliveryError("Final delivery renderer expects a Phase 7 remediation draft package.")
    return package
