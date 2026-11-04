"""Attach untrusted AI explanations without changing reconstructed evidence."""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
import json
import re


INPUT_CLAIM_FIELDS = ("inputEventAssessments", "anrModel", "simulationSteps")
_CLAIM_FIELDS = ("candidateChains", "candidateConclusions", "events", *INPUT_CLAIM_FIELDS)
_REFERENCE_FIELDS = ("evidenceRefs", "evidence", "supportingEventIds")
_CONSERVATIVE_FLAGS = {
    "finalJudgment": False,
    "notRootCauseYet": True,
    "requiresHumanConfirmation": True,
}
_FENCE = re.compile(r"^[ \t]*(`{3,})([^`]*)$")
_VALIDATION_NOTE = (
    "Reference validation only checks links to existing events; "
    "it does not verify the claim or establish causality."
)


def attach_ai_analysis(
    report: dict,
    analyses: dict[str, str] | None = None,
    ai_report: dict | None = None,
) -> dict:
    """Return a deep copy with a separate, conservative AI explanation layer.

    ``aiAnalyses`` retains slot text verbatim. Each ``aiClaims`` entry preserves
    its candidate fields, but overrides source/kind, certainty, validation and
    conservative flags. ``resolvedEventIds`` contains only locally resolved
    IDs; the original reference fields remain available for inspection.

    ``references_resolved`` means citation lookup succeeded, not that the claim
    is factually verified. No AI event participates in subsequent lookups.
    Existing AI-layer fields are replaced, so refreshing cannot retain stale
    candidates. All other report fields, including flags, remain unchanged.
    """
    result = deepcopy(report)
    claims: list[dict] = []
    slot_analyses: list[dict] = []
    warnings: list[dict] = []
    event_ids, raw_lines = _evidence_index(report)

    if analyses is not None:
        if not isinstance(analyses, dict):
            _warn(warnings, "ai-invalid-analyses", "analyses must be an object of slot text.")
        else:
            for slot, text in analyses.items():
                if not isinstance(slot, str) or not isinstance(text, str):
                    _warn(warnings, "ai-invalid-analysis", "Analysis slot names and text must be strings.")
                    continue
                slot_analyses.append({
                    "slot": slot,
                    "text": text,
                    "certainty": "inferred",
                    "validation": "unverified",
                })
                for source, body in _json_blocks(text, slot, warnings):
                    try:
                        payload = json.loads(body, parse_constant=_reject_constant)
                    except (ValueError, RecursionError) as exc:
                        _warn(warnings, "ai-invalid-json", f"{source}: {exc}")
                        continue
                    _add_report(payload, report.get("groupId"), source, event_ids, raw_lines, claims, warnings)

    if ai_report is not None:
        _add_report(ai_report, report.get("groupId"), "ai_report", event_ids, raw_lines, claims, warnings)

    result["aiAnalyses"] = slot_analyses
    result["aiClaims"] = claims
    result["aiWarnings"] = warnings
    result["aiStatus"] = (
        "has_candidates" if claims
        else "unverified" if slot_analyses or warnings or ai_report is not None
        else "absent"
    )
    return result


def _warn(warnings: list[dict], code: str, message: str) -> None:
    warnings.append({"code": code, "message": message})


def _reject_constant(value: str) -> None:
    raise ValueError(f"Non-standard JSON number: {value}")


def _json_blocks(text: str, slot: str, warnings: list[dict]) -> Iterator[tuple[str, str]]:
    fence = ""
    is_json = False
    body: list[str] = []
    source = slot
    for line_number, line in enumerate(text.splitlines(keepends=True), start=1):
        match = _FENCE.fullmatch(line.rstrip("\r\n"))
        if not fence:
            if match:
                fence = match[1]
                is_json = match[2].strip().lower() == "json"
                source = f"{slot}:json@{line_number}"
                body = []
            continue
        if match and not match[2].strip() and len(match[1]) >= len(fence):
            if is_json:
                yield source, "".join(body)
            fence = ""
            continue
        if is_json:
            body.append(line)
    if fence and is_json:
        _warn(warnings, "ai-invalid-json", f"{source}: Unterminated JSON fence.")


def _evidence_index(report: dict) -> tuple[set[str], dict[str, list[str | None]]]:
    event_ids: set[str] = set()
    raw_lines: dict[str, list[str | None]] = {}
    events = report.get("events", [])
    if not isinstance(events, list):
        return event_ids, raw_lines
    for event in events:
        if not isinstance(event, dict):
            continue
        event_id = event.get("eventId")
        if isinstance(event_id, str) and event_id:
            event_ids.add(event_id)
        else:
            event_id = None
        raw_line = event.get("rawLine")
        if isinstance(raw_line, str) and raw_line:
            raw_lines.setdefault(raw_line, []).append(event_id)
    return event_ids, raw_lines


def _add_report(
    payload: object,
    group_id: object,
    source: str,
    event_ids: set[str],
    raw_lines: dict[str, list[str | None]],
    claims: list[dict],
    warnings: list[dict],
) -> None:
    if not isinstance(payload, dict):
        _warn(warnings, "ai-invalid-report", f"{source}: AI report must be a JSON object.")
        return
    if "groupId" in payload:
        if not isinstance(payload["groupId"], str):
            _warn(warnings, "ai-invalid-field", f"{source}.groupId must be a string; report rejected.")
            return
        if payload["groupId"] != group_id:
            _warn(warnings, "ai-group-mismatch", f"{source}: groupId does not match this group; report rejected.")
            return
    try:
        # Also reject non-finite values in pre-parsed reports and float overflow.
        json.dumps(payload, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        _warn(warnings, "ai-invalid-json", f"{source}: Not a standard JSON object: {exc}")
        return

    for flag in _CONSERVATIVE_FLAGS:
        if flag in payload and not isinstance(payload[flag], bool):
            _warn(warnings, "ai-invalid-field", f"{source}.{flag} must be a boolean; ignored.")
    for field in _CLAIM_FIELDS:
        if field not in payload:
            continue
        candidates = payload[field]
        if not isinstance(candidates, list):
            _warn(warnings, "ai-invalid-field", f"{source}.{field} must be a list; ignored.")
            continue
        for index, candidate in enumerate(candidates):
            location = f"{source}.{field}[{index}]"
            if not isinstance(candidate, dict):
                _warn(warnings, "ai-invalid-claim", f"{location} must be an object; ignored.")
                continue
            resolved_ids, validation = _resolve_references(candidate, location, event_ids, raw_lines, warnings)
            claim = deepcopy(candidate)
            claim.update(_CONSERVATIVE_FLAGS)
            claim.update({
                "source": source,
                "kind": field,
                "certainty": "inferred",
                "validation": validation,
                "validationNote": _VALIDATION_NOTE,
                "resolvedEventIds": resolved_ids,
            })
            if field in INPUT_CLAIM_FIELDS:
                claim.update({
                    "referenceValidation": validation,
                    "validation": "unverified",
                    "executed": False,
                })
            claims.append(claim)


def _resolve_references(
    candidate: dict,
    location: str,
    event_ids: set[str],
    raw_lines: dict[str, list[str | None]],
    warnings: list[dict],
) -> tuple[list[str], str]:
    resolved_ids: list[str] = []
    invalid = False
    for field in _REFERENCE_FIELDS:
        if field not in candidate:
            continue
        references = candidate[field]
        if not isinstance(references, list):
            invalid = True
            _warn(warnings, "ai-invalid-references", f"{location}.{field} must be a list of strings.")
            continue
        for index, reference in enumerate(references):
            event_id = None
            if isinstance(reference, str):
                if reference in event_ids:
                    event_id = reference
                else:
                    matches = raw_lines.get(reference, [])
                    if len(matches) == 1:
                        event_id = matches[0]
            if event_id is None:
                invalid = True
                _warn(
                    warnings,
                    "ai-invalid-references",
                    f"{location}.{field}[{index}] must exactly identify an existing eventId "
                    "or a unique, complete rawLine in this group.",
                )
            elif event_id not in resolved_ids:
                resolved_ids.append(event_id)
    validation = "invalid_references" if invalid else "references_resolved" if resolved_ids else "unverified"
    return resolved_ids, validation
