"""Additive, version-pinned trigger observations; never a blocker inference."""
from __future__ import annotations

from importlib.resources import files
import json
import re
from typing import Any


class ContractValidationError(ValueError):
    """Invalid local profile, not an unsupported device input."""


ANDROID_17_TIMEOUT_KINDS = (
    "INPUT_DISPATCH_NO_FOCUSED_WINDOW",
    "INPUT_DISPATCH_WINDOW_UNRESPONSIVE",
    "BROADCAST_RECEIVER",
    "SERVICE_START",
    "SERVICE_EXEC",
    "CONTENT_PROVIDER",
    "APP_REGISTERED",
    "SHORT_FGS_TIMEOUT",
    "JOB_SERVICE",
    "APP_START",
)

_MAPPED_KINDS = {
    "INPUT_DISPATCH_NO_FOCUSED_WINDOW": (
        1,
        "input.no_focused_window",
        "aosp17.input.no_focused_window",
    ),
    "INPUT_DISPATCH_WINDOW_UNRESPONSIVE": (
        2,
        "input.window_unresponsive",
        "aosp17.input.window_unresponsive",
    ),
}
_SOURCE_FIELDS = {"repository", "revision", "path", "sha256", "symbol", "url"}
_EXPECTED_PROFILE_ID = "aosp-android-17.0.0_r1-input"
_EXPECTED_RELEASE_TAG = "android-17.0.0_r1"
_EXPECTED_MAPPED_SOURCE_REFS = ["base", "native"]
_EXPECTED_SOURCES = {
    "base": {
        "repository": "platform/frameworks/base",
        "revision": "94b4c163b7dfe5ce3607f7bb8456f9573f7de57d",
        "path": "core/java/com/android/internal/os/TimeoutRecord.java",
        "sha256": "e2f14dbc125ba7e3d2f5de4f9d8a5bfdaf5980a1a9a4f1013cc8088be3116832",
        "symbol": "TimeoutRecord.TimeoutKind",
    },
    "native": {
        "repository": "platform/frameworks/native",
        "revision": "ae266dcb706d083868578cfedce381ef44488a07",
        "path": "services/inputflinger/dispatcher/InputDispatcher.cpp",
        "sha256": "4598e3a96e0196a5965be2a4232736ba7e14616c8435b201ab6d394e44b762c9",
        "symbol": "InputDispatcher::onAnrLocked",
    },
}
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


def _require_nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{field} must be a non-empty string")
    return value


def validate_manifest_data(manifest: Any) -> dict[str, Any]:
    """Validate the complete, auditable Android 17 B1 manifest schema.

    Extra metadata is allowed so the contract remains additive, but every B1
    field and enum member is checked. Only the two Input kinds may be mapped;
    the remaining Android 17 kinds must be declared unsupported.
    """
    if not isinstance(manifest, dict):
        raise ContractValidationError("Manifest must be a JSON object")

    profile_id = _require_nonempty_string(manifest.get("profileId"), "profileId")
    if manifest.get("androidRelease") != 17:
        raise ContractValidationError("androidRelease must be 17")
    release_tag = _require_nonempty_string(manifest.get("releaseTag"), "releaseTag")
    if release_tag != _EXPECTED_RELEASE_TAG:
        raise ContractValidationError(f"releaseTag must be {_EXPECTED_RELEASE_TAG}")
    if profile_id != _EXPECTED_PROFILE_ID:
        raise ContractValidationError(f"profileId must be {_EXPECTED_PROFILE_ID}")

    sources = manifest.get("sources")
    if not isinstance(sources, dict) or set(sources) != {"base", "native"}:
        raise ContractValidationError("sources must contain exactly base and native")
    for source_name, source in sources.items():
        if not isinstance(source, dict):
            raise ContractValidationError(f"sources.{source_name} must be an object")
        missing = _SOURCE_FIELDS - set(source)
        if missing:
            raise ContractValidationError(
                f"sources.{source_name} missing required fields: {', '.join(sorted(missing))}"
            )
        for field in _SOURCE_FIELDS:
            _require_nonempty_string(source.get(field), f"sources.{source_name}.{field}")
        revision = source["revision"]
        if not _COMMIT_RE.fullmatch(revision):
            raise ContractValidationError("Source revision must be an immutable 40-character commit")
        if not _SHA256_RE.fullmatch(source["sha256"]):
            raise ContractValidationError("Source sha256 must be a lowercase 64-character digest")
        canonical_url = (
            f"https://android.googlesource.com/{source['repository']}/+/"
            f"{revision}/{source['path']}"
        )
        if source["url"] != canonical_url:
            raise ContractValidationError(
                f"sources.{source_name}.url must equal the canonical Android source URL"
            )
        for field, expected in _EXPECTED_SOURCES[source_name].items():
            if source[field] != expected:
                raise ContractValidationError(
                    f"sources.{source_name}.{field} conflicts with the pinned Android 17 source"
                )

    kinds = manifest.get("kinds")
    if not isinstance(kinds, dict):
        raise ContractValidationError("kinds must be an object")
    if tuple(kinds) != ANDROID_17_TIMEOUT_KINDS:
        raise ContractValidationError("kinds must enumerate the ten Android 17 TimeoutKind values in order")
    if "FGS_TIMEOUT" in kinds:
        raise ContractValidationError("FGS_TIMEOUT is not an Android 17 TimeoutKind")

    values: list[int] = []
    mapped_count = 0
    unsupported_count = 0
    for expected_value, kind_name in enumerate(ANDROID_17_TIMEOUT_KINDS, start=1):
        rule = kinds[kind_name]
        if not isinstance(rule, dict):
            raise ContractValidationError(f"kinds.{kind_name} must be an object")
        value = rule.get("value")
        if type(value) is not int or value != expected_value:
            raise ContractValidationError(f"kinds.{kind_name}.value must be {expected_value}")
        values.append(value)

        status = rule.get("status")
        if kind_name in _MAPPED_KINDS:
            mapped_count += 1
            expected_number, trigger, rule_id = _MAPPED_KINDS[kind_name]
            if value != expected_number or status != "mapped":
                raise ContractValidationError(f"kinds.{kind_name} must be mapped")
            if rule.get("trigger") != trigger or rule.get("ruleId") != rule_id:
                raise ContractValidationError(f"kinds.{kind_name} has conflicting mapping metadata")
            if rule.get("sourceRefs") != _EXPECTED_MAPPED_SOURCE_REFS:
                raise ContractValidationError(
                    f"kinds.{kind_name}.sourceRefs must equal ['base', 'native']"
                )
        else:
            unsupported_count += 1
            if status != "unsupported":
                raise ContractValidationError(f"kinds.{kind_name} must remain unsupported in B1")
            _require_nonempty_string(rule.get("reason"), f"kinds.{kind_name}.reason")
            if any(field in rule for field in ("trigger", "ruleId", "sourceRefs")):
                raise ContractValidationError(
                    f"kinds.{kind_name} must not expose mapping metadata while unsupported"
                )

    if values != list(range(1, 11)) or len(set(values)) != 10:
        raise ContractValidationError("TimeoutKind values must be unique and contiguous from 1 through 10")
    if mapped_count != 2 or unsupported_count != 8:
        raise ContractValidationError("B1 requires exactly two mapped and eight unsupported kinds")
    return manifest


def load_manifest() -> dict[str, Any]:
    manifest = json.loads(files(__package__).joinpath("android-17.json").read_text(encoding="utf-8"))
    return validate_manifest_data(manifest)


_PATTERNS = {
    "INPUT_DISPATCH_NO_FOCUSED_WINDOW": re.compile(r"\bdoes not have a focused window\b", re.IGNORECASE),
    "INPUT_DISPATCH_WINDOW_UNRESPONSIVE": re.compile(
        r"\bis not responding\.\s*Waited \d+ms for \S", re.IGNORECASE
    ),
}
_ANR_REASON = re.compile(r"\bam_anr\s*:\s*|\bReason:\s*", re.IGNORECASE)


def resolve_timeout(package: dict[str, Any]) -> dict[str, Any]:
    """Resolve only explicit ANR reason observations in a declared release.

    Input offsets refer to the provided source view, not immutable artifact
    byte ranges. Multi-reason packages fail closed pending group-level D1.
    No device release is guessed from generic log wording or provided_type.
    """
    result: dict[str, Any] = {
        "profileId": None, "canonicalTrigger": None, "timeoutKind": None,
        "directBlocker": None, "evidenceStatus": "unknown",
        "matchedRuleIds": [], "observationEvidenceRefs": [], "provenanceRefs": [],
        "gaps": [], "finalJudgment": False, "notRootCauseYet": True,
        "requiresHumanConfirmation": True,
    }
    release = package.get("android_release")
    if type(release) is not int or release != 17:
        result["gaps"] = ["android_release_unknown" if release is None else "unsupported_android_release"]
        return result
    if package.get("vendor_overlays"):
        result["gaps"] = ["vendor_overlay_unsupported"]
        return result
    try:
        manifest = load_manifest()
    except (OSError, ValueError, KeyError, TypeError):
        result["gaps"] = ["profile_unavailable_or_invalid"]
        return result
    result["profileId"] = manifest["profileId"]
    revisions = package.get("aosp_source_revisions")
    if revisions is not None and (
        not isinstance(revisions, dict)
        or any(revisions.get(repo) != src["revision"] for repo, src in manifest["sources"].items())
    ):
        result["gaps"] = ["source_revision_conflict"]
        return result
    declared = package.get("timeout_kind")
    if declared is not None and (not isinstance(declared, str) or declared not in _PATTERNS):
        result["gaps"] = ["timeout_kind_unsupported"]
        return result
    observations = []
    sources = package.get("sources", {})
    if not isinstance(sources, dict):
        result["gaps"] = ["supported_reason_not_observed"]
        return result
    for kind in ("event_log", "logcat"):
        source = sources.get(kind, {})
        if not isinstance(source, dict):
            continue
        content = source.get("content", "")
        if not isinstance(content, str):
            continue
        for index, line in enumerate(content.splitlines()):
            anchor = _ANR_REASON.search(line)
            if not anchor:
                continue
            reason = line[anchor.end():].strip()
            matches = [name for name, pattern in _PATTERNS.items() if pattern.search(reason)]
            observations.append((reason, matches, {"sourceKind": kind, "path": source.get("path", kind),
                                                   "lineIndex": index, "content": line,
                                                   "coordinateSpace": "provided_source_view"}))
    matched = {name for _, names, _ in observations for name in names}
    # EventLog and AnrManager may quote the same reason differently; do not
    # silently merge separate ANRs without a proven group identity.
    reasons = {reason for reason, _, _ in observations}
    if len(matched) > 1 or len(reasons) > 1:
        result["gaps"] = ["ambiguous_or_multiple_anr_reasons"]
        return result
    if not matched:
        result["gaps"] = ["supported_reason_not_observed"]
        return result
    name = next(iter(matched))
    if declared is not None and declared != name:
        result["gaps"] = ["declared_kind_conflict"]
        return result
    rule = manifest["kinds"][name]
    result.update(canonicalTrigger=rule["trigger"], timeoutKind=name, evidenceStatus="observed",
                  matchedRuleIds=[rule["ruleId"]],
                  observationEvidenceRefs=[ref for _, names, ref in observations if name in names],
                  provenanceRefs=[manifest["sources"][repo] for repo in rule["sourceRefs"]],
                  gaps=["direct_blocker_not_established", "device_profile_compatibility_not_verified"])
    return result
