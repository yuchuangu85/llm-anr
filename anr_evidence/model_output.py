"""Fail-closed validation for manager-agent JSON output.

This module validates only the structural and conservative-safety contract.
Store-reference existence is checked separately by evidence_validation;
semantic causal consistency remains an unimplemented V1 qualification gate.
"""

from __future__ import annotations

import math
from typing import Any, Callable

from .constants import SOURCE_KINDS


_CONFIDENCE_LEVELS = frozenset({"high", "medium", "low"})
_SAFETY_VALUES = {
    "finalJudgment": False,
    "notRootCauseYet": True,
    "requiresHumanConfirmation": True,
    "tentative": True,
}

_CHAIN_FIELDS = frozenset(
    {
        "title",
        "rank",
        "score",
        "confidenceLevel",
        "signalCategory",
        "rationale",
        "limitations",
        "evidenceRefs",
        "notRootCauseYet",
    }
)
_CONCLUSION_FIELDS = frozenset(
    {
        "rank",
        "confidenceLevel",
        "statement",
        "signalCategory",
        "whyNotFinal",
        "unresolvedQuestions",
        "tentative",
    }
)
_REMEDIATION_FIELDS = frozenset(
    {"rank", "priority", "title", "actionDraft", "requiresHumanConfirmation"}
)
_REPROBE_FIELDS = frozenset(
    {"beforeSeconds", "afterSeconds", "sourceKinds", "additionalTags", "reason"}
)
_TOP_FIELDS = frozenset(
    {
        "candidateChains",
        "candidateConclusions",
        "remediationDrafts",
        "globalLimitations",
        "reProbeRequest",
        "finalJudgment",
        "notRootCauseYet",
        "requiresHumanConfirmation",
    }
)


def validate_manager_report(data: object, *, require_store_refs: bool = False) -> list[str]:
    """Return all structural and safety errors in a manager report.

    An empty list means the value satisfies the current manager output shape.
    Unsupported fields are rejected so model output cannot smuggle command-like
    payloads through schema extensions.
    """

    errors: list[str] = []
    _validate_safety_values(data, "$", errors)
    if not isinstance(data, dict):
        errors.append("$ must be an object")
        return errors

    _validate_exact_fields(data, _TOP_FIELDS, "$", errors)
    _validate_object_list(
        data,
        "candidateChains",
        "$",
        _CHAIN_FIELDS | {"groupId"} if require_store_refs else _CHAIN_FIELDS,
        _validate_chain,
        errors,
    )
    _validate_object_list(
        data,
        "candidateConclusions",
        "$",
        _CONCLUSION_FIELDS | {"groupId", "evidenceRefs"} if require_store_refs else _CONCLUSION_FIELDS,
        _validate_conclusion,
        errors,
    )
    _validate_object_list(
        data,
        "remediationDrafts",
        "$",
        _REMEDIATION_FIELDS,
        _validate_remediation,
        errors,
    )
    _validate_string_array_field(data, "globalLimitations", "$", errors)
    _validate_expected_bool(data, "finalJudgment", False, "$", errors)
    _validate_expected_bool(data, "notRootCauseYet", True, "$", errors)
    _validate_expected_bool(data, "requiresHumanConfirmation", True, "$", errors)
    _validate_reprobe(data, errors)
    if require_store_refs:
        for field in ("candidateChains", "candidateConclusions"):
            items = data.get(field)
            if not isinstance(items, list):
                continue
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                path = f"$.{field}[{index}]"
                _validate_string_field(item, "groupId", path, errors)
                _validate_string_array_field(item, "evidenceRefs", path, errors)
    return errors


def _validate_chain(item: dict[str, Any], path: str, errors: list[str]) -> None:
    _validate_string_field(item, "title", path, errors)
    _validate_positive_int_field(item, "rank", path, errors)
    _validate_score_field(item, "score", path, errors)
    _validate_confidence_field(item, path, errors)
    _validate_string_field(item, "signalCategory", path, errors)
    _validate_string_field(item, "rationale", path, errors)
    _validate_string_array_field(item, "limitations", path, errors)
    _validate_string_array_field(item, "evidenceRefs", path, errors)
    _validate_expected_bool(item, "notRootCauseYet", True, path, errors)


def _validate_conclusion(item: dict[str, Any], path: str, errors: list[str]) -> None:
    _validate_positive_int_field(item, "rank", path, errors)
    _validate_confidence_field(item, path, errors)
    _validate_string_field(item, "statement", path, errors)
    _validate_string_field(item, "signalCategory", path, errors)
    _validate_string_array_field(item, "whyNotFinal", path, errors)
    _validate_string_array_field(item, "unresolvedQuestions", path, errors)
    _validate_expected_bool(item, "tentative", True, path, errors)


def _validate_remediation(item: dict[str, Any], path: str, errors: list[str]) -> None:
    _validate_positive_int_field(item, "rank", path, errors)
    _validate_bounded_int_field(item, "priority", 1, 100, path, errors)
    _validate_string_field(item, "title", path, errors)
    _validate_string_field(item, "actionDraft", path, errors)
    _validate_expected_bool(item, "requiresHumanConfirmation", True, path, errors)


def _validate_reprobe(data: dict[str, Any], errors: list[str]) -> None:
    if "reProbeRequest" not in data:
        return
    request = data["reProbeRequest"]
    path = "$.reProbeRequest"
    if request is None:
        return
    if not isinstance(request, dict):
        errors.append(f"{path} must be null or an object")
        return

    _validate_exact_fields(request, _REPROBE_FIELDS, path, errors)
    _validate_bounded_int_field(request, "beforeSeconds", 0, 300, path, errors)
    _validate_bounded_int_field(request, "afterSeconds", 0, 300, path, errors)
    _validate_enum_array_field(request, "sourceKinds", SOURCE_KINDS, path, errors)
    _validate_string_array_field(request, "additionalTags", path, errors)
    _validate_string_field(request, "reason", path, errors)


def _validate_object_list(
    data: dict[str, Any],
    field: str,
    parent_path: str,
    allowed_fields: frozenset[str],
    validator: Callable[[dict[str, Any], str, list[str]], None],
    errors: list[str],
) -> None:
    if field not in data:
        return
    value = data[field]
    path = f"{parent_path}.{field}"
    if not isinstance(value, list):
        errors.append(f"{path} must be an array")
        return
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_path} must be an object")
            continue
        _validate_exact_fields(item, allowed_fields, item_path, errors)
        validator(item, item_path, errors)


def _validate_exact_fields(
    data: dict[str, Any], allowed: frozenset[str], path: str, errors: list[str]
) -> None:
    for field in sorted(allowed - data.keys()):
        errors.append(f"{path}.{field} is required")
    for field in sorted(data.keys() - allowed, key=str):
        errors.append(f"{path}.{field} is not supported")


def _validate_string_field(
    data: dict[str, Any], field: str, path: str, errors: list[str]
) -> None:
    if field in data and not isinstance(data[field], str):
        errors.append(f"{path}.{field} must be a string")


def _validate_string_array_field(
    data: dict[str, Any], field: str, path: str, errors: list[str]
) -> None:
    if field not in data:
        return
    value = data[field]
    field_path = f"{path}.{field}"
    if not isinstance(value, list):
        errors.append(f"{field_path} must be an array")
        return
    for index, item in enumerate(value):
        if not isinstance(item, str):
            errors.append(f"{field_path}[{index}] must be a string")


def _validate_enum_array_field(
    data: dict[str, Any],
    field: str,
    allowed: tuple[str, ...],
    path: str,
    errors: list[str],
) -> None:
    if field not in data:
        return
    value = data[field]
    field_path = f"{path}.{field}"
    if not isinstance(value, list):
        errors.append(f"{field_path} must be an array")
        return
    for index, item in enumerate(value):
        if not isinstance(item, str) or item not in allowed:
            choices = ", ".join(allowed)
            errors.append(f"{field_path}[{index}] must be one of: {choices}")


def _validate_positive_int_field(
    data: dict[str, Any], field: str, path: str, errors: list[str]
) -> None:
    if field in data and (
        isinstance(data[field], bool) or not isinstance(data[field], int) or data[field] <= 0
    ):
        errors.append(f"{path}.{field} must be a positive integer")


def _validate_bounded_int_field(
    data: dict[str, Any],
    field: str,
    minimum: int,
    maximum: int,
    path: str,
    errors: list[str],
) -> None:
    if field not in data:
        return
    value = data[field]
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        errors.append(f"{path}.{field} must be an integer between {minimum} and {maximum}")


def _validate_score_field(
    data: dict[str, Any], field: str, path: str, errors: list[str]
) -> None:
    if field not in data:
        return
    value = data[field]
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 <= value <= 100
        or not math.isfinite(value)
    ):
        errors.append(f"{path}.{field} must be a finite number between 0 and 100")


def _validate_confidence_field(
    data: dict[str, Any], path: str, errors: list[str]
) -> None:
    if "confidenceLevel" not in data:
        return
    if not isinstance(data["confidenceLevel"], str) or data["confidenceLevel"] not in _CONFIDENCE_LEVELS:
        choices = ", ".join(sorted(_CONFIDENCE_LEVELS))
        errors.append(f"{path}.confidenceLevel must be one of: {choices}")


def _validate_expected_bool(
    data: dict[str, Any], field: str, expected: bool, path: str, errors: list[str]
) -> None:
    if field in data and data[field] is not expected:
        errors.append(f"{path}.{field} must be {str(expected).lower()}")


def _validate_safety_values(value: object, path: str, errors: list[str]) -> None:
    pending = [(value, path, 0)]
    while pending:
        node, node_path, depth = pending.pop()
        if depth > 64:
            errors.append("$ exceeds maximum nesting depth 64")
            return
        if isinstance(node, dict):
            for key, child in node.items():
                child_path = f"{node_path}.{key}"
                if key in _SAFETY_VALUES and child is not _SAFETY_VALUES[key]:
                    expected = str(_SAFETY_VALUES[key]).lower()
                    errors.append(f"{child_path} must be {expected}")
                pending.append((child, child_path, depth + 1))
        elif isinstance(node, list):
            pending.extend((child, f"{node_path}[{index}]", depth + 1)
                           for index, child in enumerate(node))
