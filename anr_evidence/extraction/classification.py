"""ANR type classification for Phase 1 extraction."""

from __future__ import annotations

from typing import Any

from ..constants import SUPPORTED_TYPES, TYPE_PATTERNS
from ..root_cause_hints import infer_root_cause_pattern_hints


def classify_anr_type(package: dict[str, Any]) -> dict[str, Any]:
    provided_type = package.get("provided_type")
    root_cause_hints = infer_root_cause_pattern_hints(package)
    is_silent_anr = _is_silent_anr(package)
    if provided_type in SUPPORTED_TYPES:
        return _classification(
            detected_type=provided_type,
            supported=True,
            confidence=1.0,
            fallback_mode="none",
            root_cause_hints=root_cause_hints,
            is_silent_anr=is_silent_anr,
        )

    scores = {anr_type: 0 for anr_type in TYPE_PATTERNS}
    for source in package["sources"].values():
        content = source.get("content", "").lower()
        for anr_type, patterns in TYPE_PATTERNS.items():
            scores[anr_type] += sum(content.count(pattern) for pattern in patterns)

    matched = [anr_type for anr_type, score in scores.items() if score > 0]
    if len(matched) == 1:
        confidence = min(1.0, 0.55 + (scores[matched[0]] * 0.1))
        return _classification(
            detected_type=matched[0],
            supported=True,
            confidence=round(confidence, 2),
            fallback_mode="none",
            root_cause_hints=root_cause_hints,
            is_silent_anr=is_silent_anr,
        )
    if len(matched) > 1:
        return _classification(
            detected_type=None,
            supported=False,
            confidence=0.0,
            fallback_mode="ambiguous_type",
            root_cause_hints=root_cause_hints,
            is_silent_anr=is_silent_anr,
            warnings=[{"code": "ambiguous-type", "message": "Conflicting supported ANR type signals; falling back to baseline extraction."}],
        )
    return _classification(
        detected_type=None,
        supported=False,
        confidence=0.0,
        fallback_mode="unknown_type",
        root_cause_hints=root_cause_hints,
        is_silent_anr=is_silent_anr,
        warnings=[{"code": "unknown-type", "message": "No supported ANR type confidently detected; falling back to baseline extraction."}],
    )


def _classification(
    *,
    detected_type: str | None,
    supported: bool,
    confidence: float,
    fallback_mode: str,
    root_cause_hints: list[str],
    is_silent_anr: bool,
    warnings: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "detected_type": detected_type,
        "trigger_type": detected_type or "unknown",
        "supported": supported,
        "confidence": confidence,
        "fallback_mode": fallback_mode,
        "root_cause_pattern_hints": root_cause_hints,
        "is_silent_anr": is_silent_anr,
        "warnings": warnings or [],
    }


def _is_silent_anr(package: dict[str, Any]) -> bool:
    for source in package.get("sources", {}).values():
        if "issilentanr=true" in source.get("content", "").lower().replace(" ", ""):
            return True
    return False
