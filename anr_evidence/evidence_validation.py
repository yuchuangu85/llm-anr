"""Scoped immutable-citation and deterministic V1 causal-qualification checks."""
from __future__ import annotations

import re
from typing import Any

from .evidence_slice import EvidenceSlice
from .evidence_store import EvidenceStore, EvidenceStoreError
from .model_output import validate_manager_report


# These signatures identify only an observed main-thread blocking point.  They
# do not turn a trigger, hint, score, or model prose into a root-cause claim.
_DIRECT_BLOCKER_RULES = (
    (
        re.compile(r"(?:IPCThreadState::waitForResponse|BinderProxy\.transact(?:Native)?)"),
        "binder_or_ipc_pressure",
    ),
    (
        re.compile(
            r"(?:HardwareRenderer\.nativeSyncAndDrawFrame|syncAndDrawFrame|applyTransactionOnDraw)"
        ),
        "scheduler_pressure",
    ),
    (
        re.compile(r"(?:waiting to lock|monitor contention|java\.lang\.Object\.wait|LockSupport\.park)", re.I),
        "scheduler_pressure",
    ),
)
_TRACE_THREAD_HEADER = re.compile(r'^\s*"(?P<name>[^"]+)"\s+prio=\d+\s+tid=\d+\b')
_COMPACT_MAIN_THREAD_HEADER = re.compile(r"^\s*main\s+tid=\d+\b", re.I)
_EVENT_ANR_ANCHOR = re.compile(r"\bam_anr\b", re.I)
_ANR_MANAGER_ANCHOR = re.compile(
    r"(?:AnrDumpRecord\{|^\s*ANR in\s+|^\s*Reason:\s*(?:Input dispatching timed out|.*focused window))",
    re.I,
)
_REQUIRED_QUALITY = {
    "identityStatus": "matched",
    "timeStatus": "within_window",
    "scopeEligibility": "eligible",
    "scopeRejectionReason": None,
    "referenceStatus": "verified",
    # dump_quality intentionally keeps this false until a future deterministic
    # lifecycle/freshness auditor proves it.  V1 must not invent that evidence.
    "eligibleForHighCausalConfidence": True,
}


def validate_structure_only_report(report: object) -> list[str]:
    """Preserve legacy low/medium output while denying unauthenticated high."""
    errors = validate_manager_report(report)
    if errors:
        return errors
    assert isinstance(report, dict)
    for field in ("candidateChains", "candidateConclusions"):
        for index, candidate in enumerate(report[field]):
            if candidate["confidenceLevel"] == "high":
                errors.append(
                    f"$.{field}[{index}]: high confidence requires store-backed deterministic qualification"
                )
    return errors


def validate_report_references(
    report: object,
    *,
    store: EvidenceStore,
    slices: list[EvidenceSlice],
    dump_quality_by_group: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    """Accept only current immutable references and locally qualified high confidence.

    Every store-mode candidate must use a span offered in this iteration and
    pass an original-byte readback.  V1 permits ``high`` only when a local
    qualification independently establishes matched identity, in-window time,
    eligible scope, immutable field provenance, a separately trusted high-
    confidence eligibility record, an ANR anchor, and an observed main-thread
    direct blocker of the candidate's signal category.  Scores,
    hints, relevance, specialist prose, and valid references alone never
    qualify a high-confidence causal claim.
    """
    errors = validate_manager_report(report, require_store_refs=True)
    if errors:
        return errors
    assert isinstance(report, dict)

    offered: dict[tuple[str | None, str], EvidenceSlice] = {}
    for item in slices:
        if item.evidence_ref:
            ref = item.evidence_ref
            offered[(item.group_id, ref["spanId"])] = item

    for field in ("candidateChains", "candidateConclusions"):
        for index, candidate in enumerate(report[field]):
            path = f"$.{field}[{index}]"
            if not candidate["evidenceRefs"]:
                errors.append(f"{path}: at least one verified evidence reference is required")
            for span_id in candidate["evidenceRefs"]:
                item = offered.get((candidate["groupId"], span_id))
                if item is None:
                    errors.append(f"{path}: reference not offered for this group and iteration")
                    continue
                source = "logcat" if item.source == "anr_manager" else item.source
                try:
                    read_kwargs = {}
                    if "artifactCommitment" in item.evidence_ref:
                        read_kwargs["expected_artifact_commitment"] = (
                            item.evidence_ref["artifactCommitment"]
                        )
                    value = store.read_span(
                        span_id,
                        group_id=candidate["groupId"],
                        source_kind=source,
                        max_bytes=EvidenceStore.DEFAULT_MAX_REFERENCE_READ_BYTES,
                        **read_kwargs,
                    )
                    if (
                        any(value.get(key) != expected for key, expected in item.evidence_ref.items())
                        or value["content"].decode("utf-8").rstrip("\r\n") != item.content
                    ):
                        raise EvidenceStoreError("Reference differs from offered evidence")
                except (EvidenceStoreError, UnicodeError):
                    errors.append(f"{path}: reference failed scoped original-byte readback")

    # Do not classify unverified/currently unbound content as a blocker.
    if errors:
        return errors
    _validate_high_confidence_qualification(
        report,
        offered=offered,
        slices=slices,
        dump_quality_by_group=dump_quality_by_group or {},
        errors=errors,
    )
    return errors


def _validate_high_confidence_qualification(
    report: dict[str, Any],
    *,
    offered: dict[tuple[str | None, str], EvidenceSlice],
    slices: list[EvidenceSlice],
    dump_quality_by_group: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    """Apply deterministic V1 gates without trusting manager-written semantics."""
    direct_blockers = _main_thread_direct_blockers(slices)
    anr_anchors = _anr_anchor_spans(slices)
    high_blockers_by_group: dict[str, list[tuple[str, frozenset[str]]]] = {}

    for field in ("candidateChains", "candidateConclusions"):
        for index, candidate in enumerate(report[field]):
            if candidate["confidenceLevel"] != "high":
                continue
            path = f"$.{field}[{index}]"
            group_id = candidate["groupId"]
            quality = dump_quality_by_group.get(group_id)
            if not isinstance(quality, dict):
                errors.append(f"{path}: high confidence requires local dump-quality qualification")
                continue
            for key, expected in _REQUIRED_QUALITY.items():
                if quality.get(key) != expected:
                    rendered = "true" if expected is True else "null" if expected is None else expected
                    errors.append(f"{path}: high confidence requires {key}={rendered}")

            refs = set(candidate["evidenceRefs"])
            direct_refs = {
                span_id: category
                for span_id in refs
                if (group_id, span_id) in direct_blockers
                for category in (direct_blockers[(group_id, span_id)],)
            }
            if not direct_refs:
                errors.append(
                    f"{path}: high confidence requires an immutable main-thread direct blocker reference"
                )
                continue
            categories = set(direct_refs.values())
            if len(categories) != 1:
                errors.append(f"{path}: high confidence cites contradictory immutable direct blockers")
                continue
            category = next(iter(categories))
            if candidate["signalCategory"] != category:
                errors.append(
                    f"{path}: high confidence direct blocker category conflicts with signalCategory"
                )
            if not refs & anr_anchors.get(group_id, set()):
                errors.append(f"{path}: high confidence requires an immutable ANR anchor reference")
            high_blockers_by_group.setdefault(group_id, []).append(
                (path, frozenset(direct_refs))
            )

    # Multiple high candidates in a group must identify the same observed main
    # blocking point.  Alternative blockers can be reported as low/medium;
    # declaring both high is an unresolved contradiction, not corroboration.
    for observations in high_blockers_by_group.values():
        if len(observations) < 2:
            continue
        common = set(observations[0][1])
        for _, refs in observations[1:]:
            common.intersection_update(refs)
        if not common:
            for path, _ in observations:
                errors.append(f"{path}: high confidence candidates cite conflicting immutable direct blockers")


def _main_thread_direct_blockers(
    slices: list[EvidenceSlice],
) -> dict[tuple[str, str], str]:
    """Return verified trace spans that locally show a main-thread blocker."""
    result: dict[tuple[str, str], str] = {}
    groups: dict[str, list[EvidenceSlice]] = {}
    for item in slices:
        if item.source == "trace" and item.group_id and item.evidence_ref:
            groups.setdefault(item.group_id, []).append(item)
    for group_id, trace_slices in groups.items():
        in_main_thread = False
        for item in sorted(
            trace_slices,
            key=lambda value: value.line_index if value.line_index is not None else -1,
        ):
            content = item.content.strip()
            header = _TRACE_THREAD_HEADER.match(content)
            if header:
                in_main_thread = header.group("name") == "main"
                continue
            if _COMPACT_MAIN_THREAD_HEADER.match(content):
                in_main_thread = True
            if not in_main_thread:
                continue
            for pattern, category in _DIRECT_BLOCKER_RULES:
                if pattern.search(content):
                    result[(group_id, item.evidence_ref["spanId"])] = category
                    break
    return result


def _anr_anchor_spans(slices: list[EvidenceSlice]) -> dict[str, set[str]]:
    """Return immutable spans that independently record an ANR trigger anchor."""
    result: dict[str, set[str]] = {}
    for item in slices:
        if not item.group_id or not item.evidence_ref:
            continue
        is_anchor = (
            item.source == "event_log" and bool(_EVENT_ANR_ANCHOR.search(item.content))
        ) or (
            item.source == "anr_manager" and bool(_ANR_MANAGER_ANCHOR.search(item.content))
        )
        if is_anchor:
            result.setdefault(item.group_id, set()).add(item.evidence_ref["spanId"])
    return result


def reference_catalog(slices: list[EvidenceSlice]) -> str:
    """Render no filesystem paths, original blobs, or unoffered byte ranges."""
    import json

    rows: dict[tuple[str | None, str], dict[str, Any]] = {}
    for item in slices:
        if item.evidence_ref:
            ref = item.evidence_ref
            rows[(item.group_id, ref["spanId"])] = {
                "groupId": item.group_id,
                "spanId": ref["spanId"],
                "sourceKind": ref["sourceKind"],
                "content": item.content,
            }
    return "\n".join(json.dumps(row, ensure_ascii=False) for row in rows.values())
