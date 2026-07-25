"""Shared deterministic phase detection and forward-only execution."""

from __future__ import annotations

from typing import Any

from .analyzer import analyze_normalized_package
from .extractor import extract_evidence_package
from .hypothesis import generate_causal_draft
from .normalizer import normalize_evidence_package
from .remediation import generate_remediation_drafts
from .root_cause import generate_root_cause_report


PHASE_BY_NAME = {
    "phase1-evidence-extraction-mvp": 1,
    "phase2-evidence-normalization": 2,
    "phase3-assisted-analysis": 3,
    "phase5-causal-draft": 5,
    "phase6-root-cause-report-v1": 6,
    "phase7-remediation-draft": 7,
}

NEXT_PHASE = {0: 1, 1: 2, 2: 3, 3: 5, 5: 6, 6: 7}


class PipelineError(ValueError):
    """Raised when a requested phase cannot be reached by moving forward."""


def payload_phase(payload: dict[str, Any]) -> int:
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return 0
    return PHASE_BY_NAME.get(metadata.get("phase"), 0)


def run_until(payload: dict[str, Any], target_phase: int) -> dict[str, Any]:
    """Advance *payload* to ``target_phase`` without computing later phases."""

    if target_phase not in set(PHASE_BY_NAME.values()):
        raise PipelineError(f"Unsupported target phase `{target_phase}`.")

    current = payload_phase(payload)
    if current > target_phase or (current not in NEXT_PHASE and current != target_phase):
        raise PipelineError(f"Cannot move phase {current} payload backward to phase {target_phase}.")

    result = payload
    while current != target_phase:
        next_phase = NEXT_PHASE.get(current)
        if next_phase is None or next_phase > target_phase:
            raise PipelineError(f"Cannot advance phase {current} payload to phase {target_phase}.")
        if current == 0:
            result = extract_evidence_package(result)
        elif current == 1:
            result = normalize_evidence_package(result)
        elif current == 2:
            result = analyze_normalized_package(result)
        elif current == 3:
            result = generate_causal_draft(result)
        elif current == 5:
            result = generate_root_cause_report(result)
        elif current == 6:
            result = generate_remediation_drafts(result)
        current = next_phase
    return result


__all__ = ["PHASE_BY_NAME", "PipelineError", "payload_phase", "run_until"]
