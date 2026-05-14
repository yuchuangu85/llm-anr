"""Phase 1 status and source summary helpers."""

from __future__ import annotations

from typing import Any, Iterable

from ..constants import SOURCE_KINDS


def determine_status(package: dict[str, Any], classification: dict[str, Any], evidence: list[dict[str, Any]]) -> str:
    available_sources = {kind for kind, source in package["sources"].items() if source.get("content")}
    all_sources_present = all(kind in available_sources for kind in SOURCE_KINDS)
    all_sources_readable = all(package["sources"].get(kind, {}).get("readable", True) for kind in available_sources)
    if not classification["supported"]:
        return "degraded"
    if not all_sources_readable:
        return "degraded"
    if all_sources_present and any(item for item in evidence if item["tier"] == "P0"):
        return "complete"
    return "partial"


def build_source_summary(package: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    by_source: dict[str, list[dict[str, Any]]] = {kind: [] for kind in SOURCE_KINDS}
    for item in evidence:
        by_source[item["sourceKind"]].append(item)
    summary = {}
    for source_kind in SOURCE_KINDS:
        source = package["sources"].get(source_kind)
        summary[source_kind] = {
            "available": bool(source),
            "readable": source.get("readable", False) if source else False,
            "path": source.get("path") if source else None,
            "retainedEvidenceCount": len(by_source[source_kind]),
            "retainedTiers": sorted({item["tier"] for item in by_source[source_kind]}),
        }
    return summary


def summarize_tiers(evidence: Iterable[dict[str, Any]]) -> dict[str, int]:
    summary = {"P0": 0, "P1": 0, "P2": 0}
    for item in evidence:
        summary[item["tier"]] = summary.get(item["tier"], 0) + 1
    return summary
