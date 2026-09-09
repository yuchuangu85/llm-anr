"""Reference-preserving redaction views for explicitly approved private outbound use.

This module intentionally does *not* certify that arbitrary text is anonymous or
safe to disclose.  It constructs a minimized, tokenized view of already
store-verified evidence and makes an outbound release contingent on a separate
human/organizational admission bound to that exact view.  Replacement maps stay
local; neither the map nor its original values are serializable outbound data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
import secrets
from typing import Any, Callable, Iterable

from .evidence_slice import EvidenceSlice


REDACTION_RULESET_VERSION = "anr-redaction-v1"


class RedactionError(ValueError):
    """Raised when a redacted view or its explicit outbound admission is invalid."""


@dataclass(frozen=True)
class RedactionOccurrence:
    """A local coordinate of one replacement; it never contains raw content."""

    group_id: str
    source: str
    line_index: int
    span_id: str
    field: str
    start: int
    end: int


@dataclass(frozen=True)
class RedactionReplacement:
    """A local-only reversible replacement entry.

    ``original_value`` intentionally has no repr so routine logs or assertion
    failures cannot accidentally expose it.  Callers must not serialize this
    object or package it alongside the outbound view.
    """

    token: str
    category: str
    original_value: str = field(repr=False)
    original_sha256: str
    occurrences: tuple[RedactionOccurrence, ...]


@dataclass(frozen=True)
class RedactedEvidenceView:
    """A tokenized view retaining the immutable coordinates of raw evidence."""

    view_id: str
    ruleset_version: str
    redacted_slices: tuple[EvidenceSlice, ...]
    replacement_map: tuple[RedactionReplacement, ...] = field(repr=False)
    map_commitment: str = ""
    view_commitment: str = ""

    def audit_metadata(self) -> dict[str, Any]:
        """Return metadata safe for a release ledger; no raw replacement values."""
        return {
            "viewId": self.view_id,
            "rulesetVersion": self.ruleset_version,
            "viewCommitment": self.view_commitment,
            "mapCommitment": self.map_commitment,
            "redactedSliceCount": len(self.redacted_slices),
            "replacementCount": len(self.replacement_map),
        }


@dataclass(frozen=True)
class RedactedOutboundAdmission:
    """An explicit release authorization for exactly one redacted view.

    This is intentionally an attestation/approval record, not an inference from
    regex matches.  A caller must obtain it after review of the particular view.
    """

    purpose_id: str
    recipient_id: str
    approval_id: str
    approved_by: str
    ruleset_version: str
    expires_at: str
    view_commitment: str
    map_commitment: str
    # Opaque authorization material issued and verified outside this library.
    # It is intentionally excluded from repr and all audit/export metadata.
    authorization_token: str = field(repr=False)

    def audit_metadata(self) -> dict[str, str]:
        """Return an outbound-safe approval record without raw evidence."""
        return {
            "purposeId": self.purpose_id,
            "recipientId": self.recipient_id,
            "approvalId": self.approval_id,
            "approvedBy": self.approved_by,
            "rulesetVersion": self.ruleset_version,
            "expiresAt": self.expires_at,
            "viewCommitment": self.view_commitment,
            "mapCommitment": self.map_commitment,
        }


# The verifier is the trust-root seam.  This package never treats admission
# fields, including approval_id/approved_by, as a self-authenticating release.
# A deployment must connect it to a signed capability or authorization service.
RedactedAdmissionVerifier = Callable[[RedactedOutboundAdmission, RedactedEvidenceView, str, datetime], bool]


# Every rule is a minimization transform, not a complete PII classifier.  The
# ordering avoids nested values (e.g. a URL's host) yielding several tokens.
_REDACTION_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I | re.S)),
    ("bearer", re.compile(r"\bBearer\s+[^\s\"']+", re.I)),
    ("credential_assignment", re.compile(r"\b(?:api[_-]?key|access[_-]?token|secret|password|passwd)\s*[=:]\s*[^\s,;\]\}]+", re.I)),
    ("url", re.compile(r"\b(?:https?|ftp)://[^\s\]\}\)\"']+", re.I)),
    ("email", re.compile(r"(?<![\w.+-])[\w.+-]{1,64}@[\w.-]{1,253}\.[a-z]{2,63}\b", re.I)),
    ("mac", re.compile(r"\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b", re.I)),
    ("ipv4", re.compile(r"(?<![\w.])(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\w.])")),
    ("ipv6", re.compile(r"(?<![\w:])(?:[0-9a-f]{1,4}:){2,}[0-9a-f:]+(?![\w:])", re.I)),
    ("windows_path", re.compile(r"(?<![\w])(?:[a-z]:\\|\\\\)[^\s\"']+", re.I)),
    ("unix_path", re.compile(r"(?<![\w.])/(?:[^\s/]+/)*[^\s\"']+")),
    ("android_package", re.compile(r"(?<![\w.])(?:[a-z][a-z0-9_]*\.)+[a-z][a-z0-9_]*(?![\w.])", re.I)),
    ("device_or_identity", re.compile(r"\b(?:imei|imsi|meid|serial(?:no)?|android[_ -]?id|device[_ -]?id|account(?:name)?|phone(?:number)?|msisdn)\s*[=:]\s*[^\s,;\]\}]+", re.I)),
    ("process_identity", re.compile(r"\b(?:pid|(?:sys)?tid|uid|userId)\s*(?:[=:]|\s)\s*\d+\b", re.I)),
    ("phone", re.compile(r"(?<!\w)(?:\+?\d[\d .()-]{6,}\d)(?!\w)")),
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _expected_ref_source(slice_: EvidenceSlice) -> str:
    return "logcat" if slice_.source == "anr_manager" else slice_.source


def _slice_identity(slice_: EvidenceSlice) -> tuple[str, str, int, str]:
    if not isinstance(slice_.group_id, str) or not slice_.group_id:
        raise RedactionError("Redacted outbound view requires a nonempty group_id for every slice")
    if type(slice_.line_index) is not int or slice_.line_index < 0:
        raise RedactionError("Redacted outbound view requires a nonnegative line_index for every slice")
    ref = slice_.evidence_ref
    if slice_.evidence_ref_error is not None or not isinstance(ref, dict):
        raise RedactionError("Redacted outbound view requires verified store references for every slice")
    span_id = ref.get("spanId")
    if not isinstance(span_id, str) or not span_id:
        raise RedactionError("Redacted outbound view requires a valid spanId for every slice")
    if ref.get("groupId") != slice_.group_id or ref.get("sourceKind") != _expected_ref_source(slice_):
        raise RedactionError("Redacted outbound view reference coordinates do not match the slice")
    return slice_.group_id, slice_.source, slice_.line_index, span_id


def _public_ref(ref: dict[str, Any]) -> dict[str, Any]:
    """Retain only the prompt-needed immutable identity, never store internals."""
    # The raw local slice keeps artifact IDs, byte ranges, commitments and
    # digests for readback.  A remote redacted view needs only the citation
    # identity that the model may return: group, source kind and original span.
    return {key: ref[key] for key in ("spanId", "groupId", "sourceKind") if key in ref}


def _slice_payload(slice_: EvidenceSlice) -> dict[str, Any]:
    _slice_identity(slice_)
    return {
        "source": slice_.source,
        "timestampIso": slice_.timestamp_iso,
        "deltaTSeconds": slice_.delta_t_seconds,
        "tag": slice_.tag,
        "content": slice_.content,
        "importance": slice_.importance,
        "groupId": slice_.group_id,
        "lineIndex": slice_.line_index,
        "evidenceRef": _public_ref(slice_.evidence_ref or {}),
    }


def _map_payload(replacements: Iterable[RedactionReplacement]) -> list[dict[str, Any]]:
    return [
        {
            "token": item.token,
            "category": item.category,
            "originalSha256": item.original_sha256,
            "occurrences": [
                {
                    "groupId": occurrence.group_id,
                    "source": occurrence.source,
                    "lineIndex": occurrence.line_index,
                    "spanId": occurrence.span_id,
                    "field": occurrence.field,
                    "start": occurrence.start,
                    "end": occurrence.end,
                }
                for occurrence in item.occurrences
            ],
        }
        for item in sorted(replacements, key=lambda value: value.token)
    ]


def _find_matches(content: str) -> list[tuple[int, int, str, str]]:
    candidates: list[tuple[int, int, int, str, str]] = []
    for priority, (category, pattern) in enumerate(_REDACTION_RULES):
        for match in pattern.finditer(content):
            if match.start() == match.end():
                continue
            candidates.append((match.start(), match.end(), priority, category, match.group(0)))
    candidates.sort(key=lambda item: (item[0], -(item[1] - item[0]), item[2]))
    result: list[tuple[int, int, str, str]] = []
    next_start = -1
    for start, end, _, category, value in candidates:
        if start < next_start:
            continue
        result.append((start, end, category, value))
        next_start = end
    return result


def build_reference_preserving_redacted_view(
    slices: Iterable[EvidenceSlice],
    *,
    ruleset_version: str = REDACTION_RULESET_VERSION,
    view_id: str | None = None,
) -> RedactedEvidenceView:
    """Tokenize recognized direct identifiers while keeping original span IDs.

    This function is intentionally fail-closed about store references.  It does
    not change the immutable store bytes; only newly-created outbound slices are
    redacted.  The returned replacement map is local-only and must never be
    passed to a remote client or saved beside the view artifact.
    """
    if not isinstance(ruleset_version, str) or not ruleset_version:
        raise RedactionError("ruleset_version must be a nonempty string")
    if view_id is None:
        view_id = secrets.token_urlsafe(18)
    if not isinstance(view_id, str) or not view_id:
        raise RedactionError("view_id must be a nonempty string")

    original_slices = tuple(slices)
    replacements: dict[tuple[str, str, str], dict[str, Any]] = {}
    redacted_slices: list[EvidenceSlice] = []
    token_counter = 0

    for slice_ in original_slices:
        group_id, source, line_index, span_id = _slice_identity(slice_)
        content = slice_.content
        chunks: list[str] = []
        cursor = 0
        for start, end, category, value in _find_matches(content):
            chunks.append(content[cursor:start])
            key = (group_id, category, value)
            entry = replacements.get(key)
            if entry is None:
                token_counter += 1
                token = f"[[R{token_counter:04d}-{secrets.token_hex(6)}]]"
                entry = {
                    "token": token,
                    "category": category,
                    "original_value": value,
                    "occurrences": [],
                }
                replacements[key] = entry
            chunks.append(entry["token"])
            entry["occurrences"].append(RedactionOccurrence(
                group_id=group_id,
                source=source,
                line_index=line_index,
                span_id=span_id,
                field="content",
                start=start,
                end=end,
            ))
            cursor = end
        chunks.append(content[cursor:])
        redacted_slices.append(EvidenceSlice(
            source=slice_.source,
            timestamp_iso=slice_.timestamp_iso,
            delta_t_seconds=slice_.delta_t_seconds,
            tag=slice_.tag,
            content="".join(chunks),
            importance=slice_.importance,
            group_id=slice_.group_id,
            line_index=slice_.line_index,
            evidence_ref=_public_ref(slice_.evidence_ref or {}),
            evidence_ref_error=None,
        ))

    replacement_map = tuple(
        RedactionReplacement(
            token=entry["token"],
            category=entry["category"],
            original_value=entry["original_value"],
            original_sha256=hashlib.sha256(entry["original_value"].encode("utf-8")).hexdigest(),
            occurrences=tuple(entry["occurrences"]),
        )
        for entry in sorted(replacements.values(), key=lambda value: value["token"])
    )
    map_commitment = _sha256(_map_payload(replacement_map))
    view_commitment = _sha256({
        "viewId": view_id,
        "rulesetVersion": ruleset_version,
        "mapCommitment": map_commitment,
        "slices": [_slice_payload(slice_) for slice_ in redacted_slices],
    })
    return RedactedEvidenceView(
        view_id=view_id,
        ruleset_version=ruleset_version,
        redacted_slices=tuple(redacted_slices),
        replacement_map=replacement_map,
        map_commitment=map_commitment,
        view_commitment=view_commitment,
    )


def _validate_view_commitments(view: RedactedEvidenceView) -> None:
    if not isinstance(view, RedactedEvidenceView):
        raise RedactionError("A RedactedEvidenceView is required")
    for slice_ in view.redacted_slices:
        _slice_identity(slice_)
    expected_map = _sha256(_map_payload(view.replacement_map))
    if not secrets.compare_digest(view.map_commitment, expected_map):
        raise RedactionError("Redacted view replacement-map commitment mismatch")
    expected_view = _sha256({
        "viewId": view.view_id,
        "rulesetVersion": view.ruleset_version,
        "mapCommitment": view.map_commitment,
        "slices": [_slice_payload(slice_) for slice_ in view.redacted_slices],
    })
    if not secrets.compare_digest(view.view_commitment, expected_view):
        raise RedactionError("Redacted view commitment mismatch")


def validate_redacted_view_against_slices(
    view: RedactedEvidenceView,
    raw_slices: Iterable[EvidenceSlice],
) -> None:
    """Verify a caller-provided local view exactly transforms current raw slices."""
    _validate_view_commitments(view)
    original = tuple(raw_slices)
    if len(view.redacted_slices) != len(original):
        raise RedactionError("Redacted view does not cover the current evidence slice set")

    replacements = {item.token: item for item in view.replacement_map}
    replacements_by_location: dict[tuple[str, str, int, str], list[tuple[RedactionOccurrence, RedactionReplacement]]] = {}
    for replacement in view.replacement_map:
        if hashlib.sha256(replacement.original_value.encode("utf-8")).hexdigest() != replacement.original_sha256:
            raise RedactionError("Redacted view replacement hash mismatch")
        for occurrence in replacement.occurrences:
            key = (occurrence.group_id, occurrence.source, occurrence.line_index, occurrence.span_id)
            replacements_by_location.setdefault(key, []).append((occurrence, replacement))

    seen_occurrences: set[tuple[str, str, int, str, int, int, str]] = set()
    for raw, redacted in zip(original, view.redacted_slices):
        identity = _slice_identity(raw)
        if _slice_identity(redacted) != identity:
            raise RedactionError("Redacted view changed immutable slice coordinates")
        if (raw.timestamp_iso, raw.delta_t_seconds, raw.tag, raw.importance) != (
            redacted.timestamp_iso, redacted.delta_t_seconds, redacted.tag, redacted.importance,
        ):
            raise RedactionError("Redacted view changed non-content evidence metadata")
        occurrences = sorted(replacements_by_location.get(identity, []), key=lambda pair: pair[0].start)
        expected_matches = _find_matches(raw.content)
        expected = [(start, end, category, value) for start, end, category, value in expected_matches]
        actual = []
        for occurrence, replacement in occurrences:
            if occurrence.field != "content" or occurrence.start < 0 or occurrence.end < occurrence.start:
                raise RedactionError("Redacted view contains invalid replacement coordinates")
            if raw.content[occurrence.start:occurrence.end] != replacement.original_value:
                raise RedactionError("Redacted view replacement does not match current raw content")
            actual.append((occurrence.start, occurrence.end, replacement.category, replacement.original_value))
            signature = (*identity, occurrence.start, occurrence.end, replacement.token)
            if signature in seen_occurrences:
                raise RedactionError("Redacted view duplicates replacement coordinates")
            seen_occurrences.add(signature)
        if actual != expected:
            raise RedactionError("Redacted view does not apply the current redaction rules exactly")
        cursor = 0
        expected_content: list[str] = []
        for occurrence, replacement in occurrences:
            expected_content.append(raw.content[cursor:occurrence.start])
            expected_content.append(replacement.token)
            cursor = occurrence.end
        expected_content.append(raw.content[cursor:])
        if redacted.content != "".join(expected_content):
            raise RedactionError("Redacted view content differs from its replacement map")
        for token, replacement in replacements.items():
            if replacement.original_value and replacement.original_value in redacted.content:
                raise RedactionError("Redacted view retains a mapped raw replacement value")


def recipient_id_for_provider(kind: object, model: str) -> str:
    """Render the stable provider/model identity an approval must bind to."""
    kind_value = getattr(kind, "value", kind)
    if not isinstance(kind_value, str) or not isinstance(model, str) or not model:
        raise RedactionError("Provider recipient identity is invalid")
    return f"{kind_value}:{model}"


def validate_redacted_outbound_admission(
    view: RedactedEvidenceView,
    admission: RedactedOutboundAdmission,
    *,
    recipient_id: str,
    verifier: RedactedAdmissionVerifier | None,
    now: datetime | None = None,
) -> None:
    """Require a current, independently verified release for a verified view."""
    _validate_view_commitments(view)
    if not isinstance(admission, RedactedOutboundAdmission):
        raise RedactionError("A RedactedOutboundAdmission is required for private outbound use")
    for name in ("purpose_id", "recipient_id", "approval_id", "approved_by", "ruleset_version", "expires_at", "view_commitment", "map_commitment", "authorization_token"):
        value = getattr(admission, name)
        if not isinstance(value, str) or not value.strip():
            raise RedactionError(f"Redacted outbound admission requires {name}")
    if admission.recipient_id != recipient_id:
        raise RedactionError("Redacted outbound admission recipient does not match the configured provider/model")
    if admission.ruleset_version != view.ruleset_version:
        raise RedactionError("Redacted outbound admission ruleset does not match the view")
    if not secrets.compare_digest(admission.view_commitment, view.view_commitment):
        raise RedactionError("Redacted outbound admission view commitment mismatch")
    if not secrets.compare_digest(admission.map_commitment, view.map_commitment):
        raise RedactionError("Redacted outbound admission map commitment mismatch")
    try:
        expiry = datetime.fromisoformat(admission.expires_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RedactionError("Redacted outbound admission expires_at must be ISO-8601") from exc
    if expiry.tzinfo is None:
        raise RedactionError("Redacted outbound admission expires_at must include a timezone")
    current = now or datetime.now(timezone.utc)
    current = current.astimezone(timezone.utc)
    if expiry <= current:
        raise RedactionError("Redacted outbound admission has expired")
    if verifier is None:
        raise RedactionError("An independent redacted outbound admission verifier is required")
    try:
        verified = verifier(admission, view, recipient_id, current)
    except Exception as exc:
        raise RedactionError("Redacted outbound admission verifier failed") from exc
    if verified is not True:
        raise RedactionError("Redacted outbound admission was not independently verified")


def redacted_slices_for(view: RedactedEvidenceView, raw_slices: Iterable[EvidenceSlice]) -> list[EvidenceSlice]:
    """Return redacted counterparts in the supplied raw-slice order."""
    by_identity = {_slice_identity(slice_): slice_ for slice_ in view.redacted_slices}
    result: list[EvidenceSlice] = []
    for raw in raw_slices:
        identity = _slice_identity(raw)
        redacted = by_identity.get(identity)
        if redacted is None:
            raise RedactionError("Redacted view has no counterpart for a selected outbound slice")
        result.append(redacted)
    return result


def raw_replacement_values(view: RedactedEvidenceView) -> tuple[str, ...]:
    """Local defense-in-depth input for an HTTP sender; never serialize these values."""
    return tuple(item.original_value for item in view.replacement_map if item.original_value)
