"""Additive dump-association observations over the existing FusionScope gate.

Window overlap is neither process lifetime nor freshness. This module does not
promote hints or grant model causal confidence; unsupported lifecycle facts
remain unknown. Store references prove only the explicitly named source fields.
"""
from __future__ import annotations

import copy
import re
from typing import Any

from .cross_source_fusion import FusionScope
from .evidence_slice import EvidenceSlice
from .evidence_store import EvidenceStore, EvidenceStoreError
from .log_filter import parse_log_timestamp


def audit_dump_quality(scope: FusionScope) -> dict[str, Any]:
    """Describe existing scope eligibility without changing its policy."""
    reason = scope.rejection_reason()
    identity = "unknown"
    if scope.identity_conflict:
        identity = "conflict"
    elif all((scope.target_pid is not None, scope.trace_pid is not None,
              scope.target_process, scope.trace_process)):
        if not str(scope.target_pid).isdigit() or not str(scope.trace_pid).isdigit():
            identity = "unknown"
        elif str(scope.target_pid) != str(scope.trace_pid):
            identity = "wrong_pid"
        elif scope.target_process != scope.trace_process:
            identity = "wrong_process"
        else:
            identity = "matched"
    delta = None
    timing = "unknown"
    if scope.anchor_timestamp is not None and scope.trace_timestamp is not None:
        delta = (scope.trace_timestamp - scope.anchor_timestamp).total_seconds()
        if reason != "invalid_window":
            timing = "within_window" if scope.contains(scope.trace_timestamp) else "outside_window"
    gaps = [reason] if reason else []
    gaps.extend(["process_lifetime_unproven", "dump_lifecycle_unavailable", "immutable_field_references_unavailable"])
    return {
        "groupId": scope.group_id,
        "target": {"pid": str(scope.target_pid) if scope.target_pid is not None else None,
                   "processName": scope.target_process,
                   "anchorTimestamp": scope.anchor_timestamp.isoformat() if scope.anchor_timestamp else None},
        "dump": {"pid": str(scope.trace_pid) if scope.trace_pid is not None else None,
                 "processName": scope.trace_process,
                 "timestamp": scope.trace_timestamp.isoformat() if scope.trace_timestamp else None},
        "identityStatus": identity,
        "timeStatus": timing,
        "deltaSeconds": delta,
        "associationWindow": {"beforeSeconds": scope.before_seconds, "afterSeconds": scope.after_seconds},
        "scopeEligibility": "eligible" if reason is None else "ineligible",
        "scopeRejectionReason": reason,
        "freshnessStatus": "unknown",
        "processLifetimeStatus": "unknown",
        "referenceStatus": "unavailable",
        "fieldReferences": {},
        "gaps": gaps,
        "eligibleForHighCausalConfidence": False,
        "finalJudgment": False, "notRootCauseYet": True, "requiresHumanConfirmation": True,
    }


def bind_dump_quality_references(
    quality: dict[str, Any], slices: list[EvidenceSlice], store: EvidenceStore,
    *, anchor_line: str | None,
) -> dict[str, Any]:
    """Bind only read-back references that spell out the reported fields.

    A trace PID is read only from an ART dump header, never a TID or a random
    numeric mention. The process name must be an exact Cmd line match. Missing
    or transformed header lines leave field provenance partial/unavailable.
    """
    result = copy.deepcopy(quality)
    refs: dict[str, set[str]] = {}
    invalid = False
    for item in slices:
        if item.group_id != quality["groupId"]:
            continue
        if item.evidence_ref_error:
            invalid = True
            continue
        if not item.evidence_ref:
            continue
        source = "logcat" if item.source == "anr_manager" else item.source
        try:
            value = store.read_span(
                item.evidence_ref["spanId"], group_id=quality["groupId"],
                source_kind=source,
                max_bytes=EvidenceStore.DEFAULT_MAX_REFERENCE_READ_BYTES,
                expected_artifact_commitment=item.evidence_ref["artifactCommitment"],
            )
            if (any(value.get(key) != expected for key, expected in item.evidence_ref.items())
                    or value["content"].decode("utf-8").rstrip("\r\n") != item.content):
                raise EvidenceStoreError("Dump field reference mismatch")
        except (EvidenceStoreError, KeyError, UnicodeError):
            invalid = True
            continue
        fields = []
        if item.source in ("event_log", "logcat", "anr_manager") and anchor_line and item.content == anchor_line:
            fields.append("anchor")
        if item.source == "trace":
            header = re.fullmatch(r"-+\s+pid\s+(\d+)\s+at\s+(.+?)\s+-+", item.content.strip())
            if header and header.group(1) == quality["dump"]["pid"]:
                fields.append("dumpPid")
                timestamp = parse_log_timestamp(header.group(2))
                if timestamp and timestamp.isoformat() == quality["dump"]["timestamp"]:
                    fields.append("dumpTimestamp")
            process = re.fullmatch(r"Cmd line:\s*(\S+)\s*", item.content.strip())
            if process and process.group(1) == quality["dump"]["processName"]:
                fields.append("dumpProcess")
        for field in fields:
            refs.setdefault(field, set()).add(value["spanId"])
    result["fieldReferences"] = {field: sorted(ids) for field, ids in refs.items()}
    complete = {"anchor", "dumpPid", "dumpTimestamp", "dumpProcess"} <= refs.keys()
    result["referenceStatus"] = "invalid" if invalid else "verified" if complete else "partial" if refs else "unavailable"
    result["gaps"] = [gap for gap in result["gaps"] if gap != "immutable_field_references_unavailable"]
    if invalid:
        result["gaps"].append("dump_reference_integrity_failed")
    if not complete:
        result["gaps"].append("immutable_field_references_incomplete")
    return result
