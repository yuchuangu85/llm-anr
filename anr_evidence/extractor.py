"""Core extraction logic for the ANR evidence extraction MVP."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .extraction import (
    AnchorCandidate,
    apply_type_template,
    build_evidence,
    build_source_summary,
    classify_anr_type,
    collect_anchor_candidates,
    dedupe_dicts,
    determine_status,
    extract_baseline_evidence,
    matching_lines,
    normalize_package,
    parse_timestamp,
    resolve_anchor,
    summarize_tiers,
    timestamp_to_raw_value,
    window_summary,
)
from .loaders import (
    ArchiveLoadError,
    build_package_from_entries,
    find_archives_in_directory,
    is_archive_path,
    load_package_from_archive,
    load_package_from_directory,
    load_package_from_fixture,
    load_package_from_path,
    trace_anr_timestamp_from_entry_list,
)
from .sources.shared import select_preceding_entries_for_anchor
from .sources.shared.detection import (
    dedupe_and_rank_entries,
    detect_source_kind,
    source_entry_priority,
)


def _trace_anr_timestamp_from_entries(entries: list[dict[str, Any]]) -> datetime | None:
    return trace_anr_timestamp_from_entry_list(entries)


def _select_preceding_file_for_anchor(entries: list[dict[str, Any]], anchor_dt: datetime | None) -> list[dict[str, Any]]:
    return select_preceding_entries_for_anchor(entries, anchor_dt)


_find_archives_in_directory = find_archives_in_directory
_is_archive_path = is_archive_path
_build_package_from_entries = build_package_from_entries


def _dedupe_and_rank_entries(source_kind: str, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return dedupe_and_rank_entries(source_kind, entries)


def _source_entry_priority(source_kind: str, path: str, content: str) -> int:
    return source_entry_priority(source_kind, path, content)


def _detect_source_kind(relative_path: Path, content: str) -> str | None:
    return detect_source_kind(relative_path, content)


def extract_evidence_package(package: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_package(package)
    classification = classify_anr_type(normalized)
    candidates = collect_anchor_candidates(normalized)
    anchor_summary = resolve_anchor(candidates)
    baseline_evidence, warnings = extract_baseline_evidence(normalized, anchor_summary)
    evidence = list(baseline_evidence)
    evidence.extend(apply_type_template(normalized, classification, anchor_summary, baseline_evidence))
    status = determine_status(normalized, classification, evidence)
    sources_summary = build_source_summary(normalized, evidence)
    warnings.extend(anchor_summary["warnings"])
    warnings.extend(classification["warnings"])
    warnings = _dedupe_dicts(warnings)
    return {
        "metadata": {
            "packageId": normalized["package_id"],
            "phase": "phase1-evidence-extraction-mvp",
            "status": status,
            "generatedAt": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        },
        "classification": {
            "detectedType": classification["detected_type"],
            "triggerType": classification.get("trigger_type", classification["detected_type"] or "unknown"),
            "supported": classification["supported"],
            "confidence": classification["confidence"],
            "fallbackMode": classification["fallback_mode"],
            "rootCausePatternHints": classification.get("root_cause_pattern_hints", []),
            "isSilentAnr": classification.get("is_silent_anr", False),
        },
        "anchors": {
            "primaryAnchor": anchor_summary["primary_anchor"],
            "secondaryAnchors": anchor_summary["secondary_anchors"],
            "normalizationWarnings": anchor_summary["warnings"],
        },
        "sources": sources_summary,
        "evidenceTierSummary": summarize_tiers(evidence),
        "evidence": evidence,
        "warnings": warnings,
    }


def extract_baseline_package(package: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_package(package)
    classification = classify_anr_type(normalized)
    candidates = collect_anchor_candidates(normalized)
    anchor_summary = resolve_anchor(candidates)
    baseline_evidence, warnings = extract_baseline_evidence(normalized, anchor_summary)
    return {
        "classification": classification,
        "anchors": anchor_summary,
        "evidence": baseline_evidence,
        "warnings": warnings,
    }


# Backward-compatible private helper aliases retained for existing tests and
# downstream users that reached into extractor internals. New code should import
# from anr_evidence.extraction or anr_evidence.loaders directly.
_normalize_package = normalize_package
_build_evidence = build_evidence
_window_summary = window_summary
_matching_lines = matching_lines
_parse_timestamp = parse_timestamp
_timestamp_to_raw = timestamp_to_raw_value
_dedupe_dicts = dedupe_dicts
