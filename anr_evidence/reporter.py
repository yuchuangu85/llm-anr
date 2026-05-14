"""Markdown draft report generation for Phase 3/5/6/7 analysis outputs."""

from __future__ import annotations

from typing import Any

PHASE3_REQUIRED_FIELDS = ("metadata", "classification", "anchors", "signalSummary", "timeline", "findings", "warnings")
PHASE5_REQUIRED_FIELDS = ("metadata", "classification", "anchors", "candidateChains", "warnings")
PHASE6_REQUIRED_FIELDS = (
    "metadata",
    "classification",
    "anchors",
    "topConclusion",
    "candidateConclusions",
    "globalLimitations",
    "warnings",
)
PHASE7_REQUIRED_FIELDS = (
    "metadata",
    "classification",
    "anchors",
    "topConclusion",
    "remediationDrafts",
    "globalLimitations",
    "warnings",
)


class ReportError(ValueError):
    """Raised when the input is not a valid Phase 3/5/6/7 package."""


def render_analysis_report(package: dict[str, Any]) -> str:
    analysis = _coerce_report_input(package)
    lines: list[str] = []
    metadata = analysis["metadata"]
    classification = analysis["classification"]
    anchors = analysis["anchors"]
    summary = analysis.get("signalSummary", {})
    warnings = analysis.get("warnings", [])
    candidate_chains = analysis.get("candidateChains", [])
    candidate_conclusions = analysis.get("candidateConclusions", [])
    top_conclusion = analysis.get("topConclusion")
    remediation_drafts = analysis.get("remediationDrafts", [])

    lines.append("# ANR 辅助分析报告草稿")
    lines.append("")
    lines.append("> 说明：这是基于结构化证据生成的辅助分析草稿，用于帮助阅读证据、时间线、候选结论与修复建议草稿；**不是最终根因裁决，也不包含最终修复结论**。")
    lines.append("")
    lines.append("## 1. 基本信息")
    lines.append("")
    lines.append(f"- Package ID: `{metadata.get('packageId')}`")
    lines.append(f"- Analysis Phase: `{metadata.get('phase')}` / `{metadata.get('schemaVersion')}`")
    lines.append(f"- Status: `{metadata.get('status')}`")
    lines.append(f"- Detected Type: `{classification.get('detectedType')}`")
    lines.append(f"- Supported: `{classification.get('supported')}`")
    lines.append(f"- Fallback Mode: `{classification.get('fallbackMode')}`")
    if metadata.get("finalJudgment") is not None:
        lines.append(f"- Final Judgment: `{metadata.get('finalJudgment')}`")
    if metadata.get("conclusionMode"):
        lines.append(f"- Conclusion Mode: `{metadata.get('conclusionMode')}`")
    if metadata.get("adviceMode"):
        lines.append(f"- Advice Mode: `{metadata.get('adviceMode')}`")
    if metadata.get("finalAdvice") is not None:
        lines.append(f"- Final Advice: `{metadata.get('finalAdvice')}`")
    lines.append("")

    lines.append("## 2. 范围声明")
    lines.append("")
    lines.append("- 本报告只总结证据结构、时间线、候选链路、保守版候选结论与 gated 修复建议草稿。")
    lines.append("- 本报告**不**输出最终根因裁决。")
    lines.append("- 本报告**不**输出最终修复结论。")
    lines.append("")

    lines.append("## 3. 锚点与输入完整性")
    lines.append("")
    primary_anchor = anchors.get("primaryAnchor")
    if primary_anchor:
        lines.append(f"- Primary Anchor: `{primary_anchor.get('sourceKind')}` @ `{primary_anchor.get('timestamp')}`")
    else:
        lines.append("- Primary Anchor: `None`")
    secondary = anchors.get("secondaryAnchors") or []
    lines.append(f"- Secondary Anchors: `{len(secondary)}`")
    lines.append(f"- Missing Sources: `{', '.join(summary.get('missingSources', [])) or 'None'}`")
    lines.append(f"- Unreadable Sources: `{', '.join(summary.get('unreadableSources', [])) or 'None'}`")
    if warnings:
        lines.append("- Warnings:")
        for warning in warnings:
            lines.append(f"  - `{warning.get('code')}`: {warning.get('message')}")
    lines.append("")

    lines.append("## 4. 信号概览")
    lines.append("")
    lines.append(f"- Normalized Record Count: `{summary.get('recordCount')}`")
    lines.append("- Source Counts:")
    for source_kind, count in sorted(summary.get("sourceCounts", {}).items()):
        lines.append(f"  - `{source_kind}`: {count}")
    lines.append("- Signal Categories:")
    categories = summary.get("signalCategoryCounts", {})
    if categories:
        for category, count in sorted(categories.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"  - `{category}`: {count}")
    else:
        lines.append("  - `None`")
    trace_insights = summary.get("traceInsights", {})
    lines.append("- Trace Insights:")
    if trace_insights:
        lines.append(f"  - Suspicious Record Count: `{trace_insights.get('suspiciousRecordCount', 0)}`")
        lines.append(f"  - Suspicious Thread Total: `{trace_insights.get('suspiciousThreadTotal', 0)}`")
        main_thread = trace_insights.get("mainThread", {})
        if main_thread.get("captured"):
            lines.append("  - Main Thread:")
            lines.append(f"    - thread=`{main_thread.get('threadName')}` tid=`{main_thread.get('tid')}` sysTid=`{main_thread.get('sysTid')}`")
            lines.append(f"    - blockHint=`{main_thread.get('blockHint')}`")
            lines.append(f"    - nativeTop=`{main_thread.get('nativeTopFrame')}`")
            lines.append(f"    - javaTop=`{main_thread.get('javaTopFrame')}`")
            lines.append(f"    - looperFrame=`{main_thread.get('looperFrame')}`")
        else:
            lines.append("  - Main Thread: `None`")
        dominant_hints = trace_insights.get("dominantBlockHintCounts", {})
        if dominant_hints:
            lines.append("  - Dominant Block Hints:")
            for hint, count in sorted(dominant_hints.items(), key=lambda item: (-item[1], item[0])):
                lines.append(f"    - `{hint}`: {count}")
        else:
            lines.append("  - Dominant Block Hints: `None`")
        top_trace_records = trace_insights.get("topSuspiciousRecords", [])
        if top_trace_records:
            lines.append("  - Top Suspicious Trace Records:")
            for item in top_trace_records:
                lines.append(
                    "    - "
                    f"`{item.get('recordRef')}` | thread=`{item.get('threadName')}` | "
                    f"dominant=`{item.get('dominantBlockHint') or item.get('blockHint')}` | "
                    f"suspiciousThreads=`{item.get('suspiciousThreadCount')}`"
                )
        else:
            lines.append("  - Top Suspicious Trace Records: `None`")
    else:
        lines.append("  - `None`")
    lines.append("")

    lines.append("## 5. 关键发现（辅助分析）")
    lines.append("")
    for finding in analysis.get("findings", []):
        lines.append(f"- [{finding.get('severity')}] `{finding.get('type')}/{finding.get('key')}`: {finding.get('message')}")
    if not analysis.get("findings"):
        lines.append("- 当前无额外辅助分析 finding")
    lines.append("")

    lines.append("## 6. 保守版候选结论")
    lines.append("")
    if top_conclusion:
        lines.append(f"### Top Conclusion — Rank `{top_conclusion.get('rank')}` / Score `{top_conclusion.get('score')}`")
        lines.append("")
        lines.append(f"- Statement: {top_conclusion.get('statement')}")
        lines.append(f"- Confidence: `{top_conclusion.get('confidenceLevel')}`")
        lines.append(f"- Tentative: `{top_conclusion.get('tentative')}`")
        lines.append("- Why Not Final:")
        for item in top_conclusion.get("whyNotFinal", []):
            lines.append(f"  - {item}")
        lines.append("- Unresolved Questions:")
        for item in top_conclusion.get("unresolvedQuestions", []):
            lines.append(f"  - {item}")
        lines.append("")
    else:
        lines.append("- 当前无可用候选结论")
        lines.append("")

    if candidate_conclusions:
        lines.append("### Candidate Conclusions")
        lines.append("")
        for conclusion in candidate_conclusions:
            lines.append(
                f"- [rank={conclusion.get('rank')}/score={conclusion.get('score')}] "
                f"`{conclusion.get('signalCategory')}` | {conclusion.get('statement')}"
            )
        lines.append("")

    lines.append("## 7. 候选因果链草稿")
    lines.append("")
    if candidate_chains:
        for chain in candidate_chains:
            lines.append(f"### {chain.get('title')}")
            lines.append("")
            lines.append(f"- Rank: `{chain.get('rank')}`")
            lines.append(f"- Score: `{chain.get('score')}`")
            lines.append(f"- Confidence: `{chain.get('confidenceLevel')}`")
            lines.append(f"- Signal Category: `{chain.get('signalCategory')}`")
            lines.append(f"- notRootCauseYet: `{chain.get('notRootCauseYet')}`")
            lines.append(f"- Rationale: {chain.get('rationale')}")
            lines.append("- Evidence Refs:")
            for ref in chain.get("evidenceRefs", []):
                lines.append(f"  - `{ref}`")
            snippets = chain.get("evidenceSnippets", [])
            if snippets:
                lines.append("- Evidence Snippets:")
                for snippet in snippets:
                    lines.append(
                        "  - "
                        f"[#{snippet.get('rank')}/score={snippet.get('score')}] "
                        f"`{snippet.get('timestamp')}` | `{snippet.get('sourceKind')}` | "
                        f"`{snippet.get('recordType')}` | {snippet.get('summary')}"
                    )
            lines.append("- Limitations:")
            for limitation in chain.get("limitations", []):
                lines.append(f"  - {limitation}")
            lines.append("")
    else:
        lines.append("- 当前未生成候选因果链草稿")
        lines.append("")

    lines.append("## 8. 修复建议草稿（需人工确认）")
    lines.append("")
    if remediation_drafts:
        for draft in remediation_drafts:
            lines.append(f"### {draft.get('title')}")
            lines.append("")
            lines.append(f"- Rank: `{draft.get('rank')}`")
            lines.append(f"- Priority: `{draft.get('priority')}`")
            lines.append(f"- Derived From: `{draft.get('derivedFromSignalCategory')}`")
            lines.append(f"- requiresHumanConfirmation: `{draft.get('requiresHumanConfirmation')}`")
            lines.append(f"- Action Draft: {draft.get('actionDraft')}")
            lines.append("- Supporting Evidence Refs:")
            for ref in draft.get("supportingEvidenceRefs", []):
                lines.append(f"  - `{ref}`")
            if draft.get("supportingSnippets"):
                lines.append("- Supporting Snippets:")
                for snippet in draft.get("supportingSnippets", []):
                    lines.append(
                        "  - "
                        f"`{snippet.get('timestamp')}` | `{snippet.get('sourceKind')}` | `{snippet.get('recordType')}` | {snippet.get('summary')}"
                    )
            lines.append("- Why Gated:")
            for item in draft.get("whyGated", []):
                lines.append(f"  - {item}")
            lines.append("")
    else:
        lines.append("- 当前未生成修复建议草稿")
        lines.append("")

    lines.append("## 9. 证据时间线")
    lines.append("")
    timeline = analysis.get("timeline", [])
    if not timeline:
        lines.append("- 无可用时间线条目")
    else:
        for item in timeline:
            extras: list[str] = []
            if item.get("dominantBlockHint"):
                extras.append(f"dominantBlockHint=`{item.get('dominantBlockHint')}`")
            if item.get("suspiciousThreadCount"):
                extras.append(f"suspiciousThreads=`{item.get('suspiciousThreadCount')}`")
            suffix = f" | {' | '.join(extras)}" if extras else ""
            lines.append(
                f"- `{item.get('timestamp')}` | `{item.get('sourceKind')}` | `{item.get('recordType')}` | "
                f"`{item.get('signalCategory')}` | {item.get('summary')}{suffix}"
            )
    lines.append("")

    lines.append("## 10. 全局限制与后续人工分析建议")
    lines.append("")
    for limitation in analysis.get("globalLimitations", []):
        lines.append(f"- {limitation}")
    lines.append("- 优先检查 P0 相关 record 对应的原始证据片段。")
    lines.append("- 若存在候选因果链、候选结论或修复建议草稿，只能作为后续人工分析入口，不能直接当最终结论。")
    lines.append("- 若存在 fallback / degraded / missing source，请先确认输入完整性。")
    lines.append("- 若要形成正式修复方案，应先完成最终 root-cause 复核，再对 remediation draft 做人工确认。")
    lines.append("")
    return "\n".join(lines)


def _coerce_report_input(package: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(package, dict):
        raise ReportError("Report generator expects a dict-like Phase 3/5/6/7 package.")
    metadata = package.get("metadata", {})
    phase = metadata.get("phase")
    if phase == "phase3-assisted-analysis":
        from .hypothesis import generate_causal_draft
        from .root_cause import generate_root_cause_report
        from .remediation import generate_remediation_drafts

        _assert_required(package, PHASE3_REQUIRED_FIELDS, "Phase 3 analysis")
        phase5 = generate_causal_draft(package)
        phase6 = generate_root_cause_report(phase5)
        phase7 = generate_remediation_drafts(phase6)
        return {
            "metadata": phase7["metadata"],
            "classification": package["classification"],
            "anchors": package["anchors"],
            "signalSummary": package.get("signalSummary", {}),
            "timeline": package.get("timeline", []),
            "findings": package.get("findings", []),
            "candidateChains": phase5.get("candidateChains", []),
            "topConclusion": phase6.get("topConclusion"),
            "candidateConclusions": phase6.get("candidateConclusions", []),
            "remediationDrafts": phase7.get("remediationDrafts", []),
            "globalLimitations": phase7.get("globalLimitations", []),
            "warnings": package.get("warnings", []),
        }
    if phase == "phase5-causal-draft":
        from .root_cause import generate_root_cause_report
        from .remediation import generate_remediation_drafts

        _assert_required(package, PHASE5_REQUIRED_FIELDS, "Phase 5 causal draft")
        phase6 = generate_root_cause_report(package)
        phase7 = generate_remediation_drafts(phase6)
        return {
            "metadata": phase7["metadata"],
            "classification": package["classification"],
            "anchors": package["anchors"],
            "signalSummary": {"traceInsights": package.get("traceInsights", {})},
            "timeline": [],
            "findings": [],
            "candidateChains": package["candidateChains"],
            "topConclusion": phase6.get("topConclusion"),
            "candidateConclusions": phase6.get("candidateConclusions", []),
            "remediationDrafts": phase7.get("remediationDrafts", []),
            "globalLimitations": phase7.get("globalLimitations", []),
            "warnings": package.get("warnings", []),
        }
    if phase == "phase6-root-cause-report-v1":
        from .remediation import generate_remediation_drafts

        _assert_required(package, PHASE6_REQUIRED_FIELDS, "Phase 6 root-cause report")
        phase7 = generate_remediation_drafts(package)
        return {
            "metadata": phase7["metadata"],
            "classification": package["classification"],
            "anchors": package["anchors"],
            "signalSummary": {"traceInsights": package.get("traceInsights", {})},
            "timeline": [],
            "findings": [],
            "candidateChains": package.get("candidateChains", []),
            "topConclusion": package.get("topConclusion"),
            "candidateConclusions": package.get("candidateConclusions", []),
            "remediationDrafts": phase7.get("remediationDrafts", []),
            "globalLimitations": phase7.get("globalLimitations", []),
            "warnings": package.get("warnings", []),
        }
    if phase == "phase7-remediation-draft":
        _assert_required(package, PHASE7_REQUIRED_FIELDS, "Phase 7 remediation draft")
        return {
            "metadata": package["metadata"],
            "classification": package["classification"],
            "anchors": package["anchors"],
            "signalSummary": {"traceInsights": package.get("traceInsights", {})},
            "timeline": [],
            "findings": [],
            "candidateChains": package.get("candidateChains", []),
            "topConclusion": package.get("topConclusion"),
            "candidateConclusions": package.get("candidateConclusions", []),
            "remediationDrafts": package.get("remediationDrafts", []),
            "globalLimitations": package.get("globalLimitations", []),
            "warnings": package.get("warnings", []),
        }
    raise ReportError("Report generator expects a Phase 3/5/6/7 package.")


def _assert_required(package: dict[str, Any], required: tuple[str, ...], label: str) -> None:
    missing = [field for field in required if field not in package]
    if missing:
        raise ReportError(f"{label} package is missing required fields: {', '.join(missing)}")
