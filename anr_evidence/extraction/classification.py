"""ANR type classification for Phase 1 extraction."""

from __future__ import annotations

from typing import Any

from ..constants import SUPPORTED_TYPES, TYPE_PATTERNS


def classify_anr_type(package: dict[str, Any]) -> dict[str, Any]:
    provided_type = package.get("provided_type")
    if provided_type in SUPPORTED_TYPES:
        return {
            "detected_type": provided_type,
            "supported": True,
            "confidence": 1.0,
            "fallback_mode": "none",
            "warnings": [],
        }

    scores = {anr_type: 0 for anr_type in TYPE_PATTERNS}
    for source in package["sources"].values():
        content = source.get("content", "").lower()
        for anr_type, patterns in TYPE_PATTERNS.items():
            scores[anr_type] += sum(content.count(pattern) for pattern in patterns)

    matched = [anr_type for anr_type, score in scores.items() if score > 0]
    if len(matched) == 1:
        confidence = min(1.0, 0.55 + (scores[matched[0]] * 0.1))
        return {
            "detected_type": matched[0],
            "supported": True,
            "confidence": round(confidence, 2),
            "fallback_mode": "none",
            "warnings": [],
        }
    if len(matched) > 1:
        return {
            "detected_type": None,
            "supported": False,
            "confidence": 0.0,
            "fallback_mode": "ambiguous_type",
            "warnings": [{"code": "ambiguous-type", "message": "Conflicting supported ANR type signals; falling back to baseline extraction."}],
        }
    return {
        "detected_type": None,
        "supported": False,
        "confidence": 0.0,
        "fallback_mode": "unknown_type",
        "warnings": [{"code": "unknown-type", "message": "No supported ANR type confidently detected; falling back to baseline extraction."}],
    }
