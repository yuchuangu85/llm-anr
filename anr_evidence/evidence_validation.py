"""Scoped citation checks, not semantic proof of model-written causal claims."""
from __future__ import annotations

from typing import Any

from .evidence_slice import EvidenceSlice
from .evidence_store import EvidenceStore, EvidenceStoreError
from .model_output import validate_manager_report


def validate_report_references(
    report: object, *, store: EvidenceStore, slices: list[EvidenceSlice],
) -> list[str]:
    """Accept only references offered in this iteration and still readable.

    Until deterministic identity/time/blocker and contradiction qualification
    is wired in, high confidence is unsupported even for a valid citation.
    The caller must supply freshly bound slices, not model/JSONL references.
    """
    errors = validate_manager_report(report, require_store_refs=True)
    if errors:
        return errors
    offered = {}
    for item in slices:
        if item.evidence_ref:
            ref = item.evidence_ref
            offered[(item.group_id, ref["spanId"])] = item
    for field in ("candidateChains", "candidateConclusions"):
        for index, candidate in enumerate(report[field]):
            path = f"$.{field}[{index}]"
            if candidate["confidenceLevel"] == "high":
                errors.append(f"{path}: high confidence requires unavailable causal qualification")
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
                        span_id, group_id=candidate["groupId"], source_kind=source,
                        max_bytes=EvidenceStore.DEFAULT_MAX_REFERENCE_READ_BYTES,
                        **read_kwargs,
                    )
                    if (any(value.get(key) != expected for key, expected in item.evidence_ref.items())
                            or value["content"].decode("utf-8").rstrip("\r\n") != item.content):
                        raise EvidenceStoreError("Reference differs from offered evidence")
                except (EvidenceStoreError, UnicodeError):
                    errors.append(f"{path}: reference failed scoped original-byte readback")
    return errors


def reference_catalog(slices: list[EvidenceSlice]) -> str:
    """Render no filesystem paths, original blobs, or unoffered byte ranges."""
    import json

    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for item in slices:
        if item.evidence_ref:
            ref = item.evidence_ref
            rows[(item.group_id, ref["spanId"])] = {
                "groupId": item.group_id, "spanId": ref["spanId"],
                "sourceKind": ref["sourceKind"], "content": item.content,
            }
    return "\n".join(json.dumps(row, ensure_ascii=False) for row in rows.values())
