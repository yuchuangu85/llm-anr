"""Baseline and template evidence assembly for Phase 1 extraction."""

from __future__ import annotations

from typing import Any

from ..constants import TYPE_PATTERNS
from ..workflow import FilterWorkflowOptions, run_filter_workflow
from .common import build_evidence, matching_lines, parse_timestamp, window_summary


def extract_baseline_evidence(package: dict[str, Any], anchor_summary: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    workflow_result = run_filter_workflow(package, anchor_summary, FilterWorkflowOptions())
    return workflow_result.evidence, workflow_result.warnings


def apply_type_template(package: dict[str, Any], classification: dict[str, Any], anchor_summary: dict[str, Any], baseline_evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not classification["supported"] or not classification["detected_type"]:
        return []
    anchor_dt = None
    if anchor_summary["primary_anchor"]:
        anchor_dt = parse_timestamp(anchor_summary["primary_anchor"]["timestamp"])
    template_name = classification["detected_type"]
    evidence: list[dict[str, Any]] = []
    keywords = TYPE_PATTERNS[template_name]
    for source_kind in ("trace", "logcat", "event_log"):
        source = package["sources"].get(source_kind)
        if not source:
            continue
        lines = matching_lines(source.get("content", ""), keywords)
        if not lines:
            continue
        evidence.append(build_evidence(
            evidence_id=f"{template_name}_{source_kind}_context",
            source_kind=source_kind,
            tier="P1",
            extraction_mode="template-additive",
            rule_name=f"{template_name}-keywords",
            anchor=anchor_summary["primary_anchor"],
            source_path=source.get("path", source_kind),
            content="\n".join(lines[:10]),
            time_window=window_summary(anchor_dt, source_kind),
            label=f"{template_name}-template-context",
        ))
    return evidence
