"""Shared DQ1 dump identity, association, and provenance auditor.

This module is the single boundary for deciding whether a selected dump can be
associated with an ANR group.  A configured time window is an association
filter, not proof of dump freshness, process lifetime, queue state, or root
cause.  Thread IDs are reported as metadata only and never establish process
identity.  Immutable store references prove only the explicitly named fields.
"""
from __future__ import annotations

import copy
import math
import re
from typing import Any, Mapping

from .cross_source_fusion import FusionScope
from .evidence_slice import EvidenceSlice
from .evidence_store import EvidenceStore, EvidenceStoreError
from .log_filter import parse_log_timestamp


_DQ1_SCHEMA_VERSION = "dq1-v1"
_DQ1_DUMP_LIFECYCLE_SCHEMA_VERSION = "dq1-dump-lifecycle-v1"
_REQUIRED_REFERENCE_FIELDS = frozenset({"anchor", "dumpPid", "dumpTimestamp", "dumpProcess"})
_TRACE_DUMP_HEADER = re.compile(r"-+\s+pid\s+(\d+)\s+at\s+(.+?)\s+-+")
_TRACE_PROCESS_LINE = re.compile(r"Cmd line:\s*(\S+)\s*")


def _string_or_none(value: str | int | None) -> str | None:
    return str(value) if value is not None else None


def _finite_or_none(value: float) -> float | None:
    return value if math.isfinite(value) else None


def _append_gap(result: dict[str, Any], gap: str) -> None:
    if gap not in result["gaps"]:
        result["gaps"].append(gap)


def _identity_status(scope: FusionScope) -> str:
    if scope.identity_conflict:
        return "conflict"
    if not all((scope.target_pid is not None, scope.trace_pid is not None,
                scope.target_process, scope.trace_process)):
        return "unknown"
    if not str(scope.target_pid).isdigit() or not str(scope.trace_pid).isdigit():
        return "unknown"
    if str(scope.target_pid) != str(scope.trace_pid):
        return "wrong_pid"
    if scope.target_process != scope.trace_process:
        return "wrong_process"
    return "matched"


def _time_status(scope: FusionScope, rejection_reason: str | None) -> tuple[str, float | None]:
    if scope.anchor_timestamp is None or scope.trace_timestamp is None:
        return "unknown", None
    delta = (scope.trace_timestamp - scope.anchor_timestamp).total_seconds()
    if rejection_reason == "invalid_window":
        return "unknown", delta
    return ("within_window" if scope.contains(scope.trace_timestamp) else "outside_window"), delta


def _association_status(identity: str, timing: str, rejection_reason: str | None) -> str:
    """Classify group association without claiming freshness or staleness."""
    if identity == "matched" and timing == "within_window" and rejection_reason is None:
        return "associated"
    if identity in {"wrong_pid", "wrong_process", "conflict"} or timing == "outside_window":
        return "rejected"
    # Invalid windows and missing evidence make association unknown rather than
    # silently treating a process/dump as stale or unrelated.
    return "unknown"


def _provenance(status: str, field_references: dict[str, list[str]]) -> dict[str, Any]:
    return {"status": status, "fieldReferences": field_references}


def _status_from_quality(quality: Mapping[str, Any], key: str, default: str = "unknown") -> str:
    value = quality.get(key)
    return value if isinstance(value, str) else default


def dump_lifecycle_projection(quality: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the canonical source-local lifecycle view attached to DQ1.

    This is a projection, not a second identity, freshness, staleness, or PID
    classifier.  Consumers (D1, F2, and any future dump/graph adapter) copy
    this view rather than reconstructing conflicting local quality decisions.
    """
    source_quality = quality if isinstance(quality, Mapping) else {}
    current = source_quality.get("dumpLifecycle")
    result = copy.deepcopy(dict(current)) if isinstance(current, Mapping) else {}
    result.setdefault("schemaVersion", _DQ1_DUMP_LIFECYCLE_SCHEMA_VERSION)
    result.setdefault("status", "unavailable")
    result.setdefault("source", "none")
    result.setdefault("captureTimeStatus", "unavailable")
    result.setdefault("inputStatus", "unknown")
    result.setdefault("windowStatus", "unknown")
    result.setdefault("queueStatus", "unknown")
    result.setdefault("transferStatus", "unknown")
    result.setdefault("blameTransferStatus", "unavailable")
    result.setdefault("gaps", ["source_local_dump_lifecycle_unavailable"])
    # These are exact DQ1 copies, never adapter-local decisions.  Preserve the
    # independent generic lifecycle field so a source-local snapshot cannot
    # upgrade freshness, staleness, process lifetime, or ANR-time lifecycle.
    for key in (
        "identityStatus", "timeStatus", "associationStatus", "freshnessStatus",
        "stalenessStatus", "processLifetimeStatus", "lifecycleStatus",
        "provenanceStatus",
    ):
        result[key] = _status_from_quality(source_quality, key)
    return result


def _snapshot_lifecycle_status(input_status: str, window_status: str) -> str:
    if input_status == "observed" and window_status == "observed":
        return "observed"
    if input_status == "absent" and window_status == "absent":
        return "unavailable"
    return "partial"


def audit_dump_quality(
    scope: FusionScope,
    *,
    target_tid: str | int | None = None,
    trace_tid: str | int | None = None,
) -> dict[str, Any]:
    """Return the DQ1 contract for one ANR group and selected dump.

    ``target_tid`` and ``trace_tid`` are retained to diagnose repeated TIDs but
    are deliberately excluded from identity and association decisions.  The
    result remains fail-closed for high causal confidence until a future,
    independently verified lifecycle/freshness auditor can supply that proof.
    """
    reason = scope.rejection_reason()
    identity = _identity_status(scope)
    timing, delta = _time_status(scope, reason)
    association = _association_status(identity, timing, reason)
    gaps = [reason] if reason else []
    gaps.extend([
        "freshness_unproven",
        "process_lifetime_unproven",
        "dump_lifecycle_unavailable",
        "immutable_field_references_unavailable",
    ])
    if target_tid is not None or trace_tid is not None:
        gaps.append("thread_id_not_identity_proof")

    reference_status = "unavailable"
    field_references: dict[str, list[str]] = {}
    result = {
        "schemaVersion": _DQ1_SCHEMA_VERSION,
        "groupId": scope.group_id,
        "target": {
            "pid": _string_or_none(scope.target_pid),
            "processName": scope.target_process,
            "tid": _string_or_none(target_tid),
            "anchorTimestamp": scope.anchor_timestamp.isoformat() if scope.anchor_timestamp else None,
        },
        "dump": {
            "pid": _string_or_none(scope.trace_pid),
            "processName": scope.trace_process,
            "tid": _string_or_none(trace_tid),
            "timestamp": scope.trace_timestamp.isoformat() if scope.trace_timestamp else None,
        },
        "identityStatus": identity,
        "timeStatus": timing,
        "associationStatus": association,
        "deltaSeconds": delta,
        "associationWindow": {
            "beforeSeconds": _finite_or_none(scope.before_seconds),
            "afterSeconds": _finite_or_none(scope.after_seconds),
        },
        "scopeEligibility": "eligible" if reason is None else "ineligible",
        "scopeRejectionReason": reason,
        # These four fields intentionally remain independent.  In particular,
        # an out-of-window dump is not automatically stale.
        "freshnessStatus": "unknown",
        "stalenessStatus": "unknown",
        "processLifetimeStatus": "unknown",
        "lifecycleStatus": "unknown",
        "referenceStatus": reference_status,
        "provenanceStatus": reference_status,
        "fieldReferences": field_references,
        "provenance": _provenance(reference_status, field_references),
        "gaps": list(dict.fromkeys(gaps)),
        "eligibleForHighCausalConfidence": False,
        "finalJudgment": False,
        "notRootCauseYet": True,
        "requiresHumanConfirmation": True,
    }
    result["dumpLifecycle"] = dump_lifecycle_projection(result)
    return result


def annotate_dump_quality_with_dumpsys_snapshot(
    quality: dict[str, Any],
    snapshot: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Project a parsed Input/Window snapshot onto the shared DQ1 record.

    The adapter supplies only source-local observations.  This helper does not
    alter DQ1 identity, time, association, freshness, process-lifetime, or
    generic lifecycle status: a dumpsys body normally has no trustworthy
    capture timestamp, so it cannot prove that its snapshot existed at the
    selected ANR anchor.
    """
    result = copy.deepcopy(quality)
    if not isinstance(snapshot, Mapping):
        return result
    input_view = snapshot.get("input") if isinstance(snapshot.get("input"), Mapping) else {}
    window_view = snapshot.get("window") if isinstance(snapshot.get("window"), Mapping) else {}
    # Preserve pre-D2 report shape when no optional dumpsys source was supplied.
    # A pair of adapter-generated ``absent`` placeholders is not evidence.
    if input_view.get("status") == "absent" and window_view.get("status") == "absent":
        return result
    transfer = snapshot.get("transfer") if isinstance(snapshot.get("transfer"), Mapping) else {}
    candidate = {
        "kind": "window",
        "name": transfer.get("windowId"),
        "pid": transfer.get("pid"),
        "processName": transfer.get("processName"),
        "displayId": transfer.get("displayId"),
        "inputChannelToken": transfer.get("inputChannelToken"),
    }
    input_status = input_view.get("status", "unknown")
    window_status = window_view.get("status", "unknown")
    queue_status = (input_view.get("queue") or {}).get("status", "unknown")
    transfer_status = transfer.get("status", "unknown")
    result["dumpsysSnapshot"] = {
        "schemaVersion": snapshot.get("schemaVersion"),
        "inputStatus": input_status,
        "windowStatus": window_status,
        "queueStatus": queue_status,
        "transferStatus": transfer_status,
        # A matched source-local token/display mapping is never itself a blame
        # conclusion.  Its capture time and ANR-group association are absent.
        "blameTransfer": {
            "status": "unqualified",
            "candidate": candidate if transfer_status == "matched" else None,
            "reason": "dumpsys_capture_time_unavailable",
        },
        "captureTimestamp": None,
        "lifecycleStatus": "unknown",
        "gaps": list(snapshot.get("gaps") or []),
    }
    lifecycle = dump_lifecycle_projection(result)
    lifecycle.update({
        "status": _snapshot_lifecycle_status(input_status, window_status),
        "source": "dumpsys_input_window",
        "captureTimeStatus": "unavailable",
        "inputStatus": input_status,
        "windowStatus": window_status,
        "queueStatus": queue_status,
        "transferStatus": transfer_status,
        "blameTransferStatus": "unqualified",
        "gaps": list(dict.fromkeys([
            *(snapshot.get("gaps") or []),
            "dumpsys_capture_time_unavailable",
            "source_local_snapshot_not_anr_lifecycle_proof",
        ])),
    })
    result["dumpLifecycle"] = lifecycle
    return result


def bind_dump_quality_references(
    quality: dict[str, Any], slices: list[EvidenceSlice], store: EvidenceStore,
    *, anchor_line: str | None,
) -> dict[str, Any]:
    """Bind immutable references that explicitly spell out DQ1 reported fields.

    PID binds only from an ART dump header; process binds only from an exact
    ``Cmd line`` record.  A cross-group reference is an integrity failure and
    never gets reused for the selected group's dump.  The binding proves
    provenance for named fields, never freshness, lifecycle, or root cause.
    """
    result = copy.deepcopy(quality)
    result.setdefault("schemaVersion", _DQ1_SCHEMA_VERSION)
    result.setdefault("gaps", [])
    result.setdefault("fieldReferences", {})
    refs: dict[str, set[str]] = {}
    invalid = False

    for item in slices:
        # Callers may pass slices for multiple groups.  They are not evidence
        # for this audit and are therefore ignored rather than reclassified.
        if item.group_id != result["groupId"]:
            continue
        if item.evidence_ref_error:
            invalid = True
            _append_gap(result, "dump_reference_binding_error")
            continue
        if not item.evidence_ref:
            continue

        evidence_ref = item.evidence_ref
        if evidence_ref.get("groupId") != result["groupId"]:
            invalid = True
            _append_gap(result, "wrong_group_reference")
            continue

        source = "logcat" if item.source == "anr_manager" else item.source
        try:
            value = store.read_span(
                evidence_ref["spanId"], group_id=result["groupId"],
                source_kind=source,
                max_bytes=EvidenceStore.DEFAULT_MAX_REFERENCE_READ_BYTES,
                expected_artifact_commitment=evidence_ref["artifactCommitment"],
            )
            if (any(value.get(key) != expected for key, expected in evidence_ref.items())
                    or value["content"].decode("utf-8").rstrip("\r\n") != item.content):
                raise EvidenceStoreError("Dump field reference mismatch")
        except (EvidenceStoreError, KeyError, UnicodeError):
            invalid = True
            _append_gap(result, "dump_reference_binding_error")
            continue

        fields: list[str] = []
        if item.source in ("event_log", "logcat", "anr_manager") and anchor_line and item.content == anchor_line:
            fields.append("anchor")
        if item.source == "trace":
            header = _TRACE_DUMP_HEADER.fullmatch(item.content.strip())
            if header and header.group(1) == result["dump"]["pid"]:
                fields.append("dumpPid")
                timestamp = parse_log_timestamp(header.group(2))
                if timestamp and timestamp.isoformat() == result["dump"]["timestamp"]:
                    fields.append("dumpTimestamp")
            process = _TRACE_PROCESS_LINE.fullmatch(item.content.strip())
            if process and process.group(1) == result["dump"]["processName"]:
                fields.append("dumpProcess")
        for field in fields:
            refs.setdefault(field, set()).add(value["spanId"])

    field_references = {field: sorted(ids) for field, ids in refs.items()}
    complete = _REQUIRED_REFERENCE_FIELDS <= refs.keys()
    status = "invalid" if invalid else "verified" if complete else "partial" if refs else "unavailable"
    result["fieldReferences"] = field_references
    # referenceStatus is a compatibility field.  provenanceStatus is the DQ1
    # name and must always agree with it.
    result["referenceStatus"] = status
    result["provenanceStatus"] = status
    result["provenance"] = _provenance(status, field_references)
    result["gaps"] = [gap for gap in result["gaps"] if gap != "immutable_field_references_unavailable"]
    if invalid:
        _append_gap(result, "dump_reference_integrity_failed")
    if not complete:
        _append_gap(result, "immutable_field_references_incomplete")
    # Keep all consumer projections synchronized with the sole DQ1 authority
    # after immutable provenance changes; do not derive a separate lifecycle.
    result["dumpLifecycle"] = dump_lifecycle_projection(result)
    return result
