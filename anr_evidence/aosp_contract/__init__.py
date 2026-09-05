"""Additive, version-pinned trigger observations; never a blocker inference."""
from __future__ import annotations

from importlib.resources import files
import json
import re
from typing import Any


class ContractValidationError(ValueError):
    """Invalid local profile, not an unsupported device input."""


def load_manifest() -> dict[str, Any]:
    manifest = json.loads(files(__package__).joinpath("android-17.json").read_text(encoding="utf-8"))
    if manifest.get("androidRelease") != 17:
        raise ContractValidationError("Unexpected profile release")
    for source in manifest["sources"].values():
        if not re.fullmatch(r"[0-9a-f]{40}", source["revision"]):
            raise ContractValidationError("Source revision must be an immutable commit")
    return manifest


_PATTERNS = {
    "INPUT_DISPATCH_NO_FOCUSED_WINDOW": re.compile(r"\bdoes not have a focused window\b"),
    "INPUT_DISPATCH_WINDOW_UNRESPONSIVE": re.compile(r"\bis not responding\. Waited \d+ms for \S"),
}
_ANR_REASON = re.compile(r"\bam_anr\s*:\s*|\bReason:\s*")


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
    for kind in ("event_log", "logcat"):
        source = package.get("sources", {}).get(kind, {})
        for index, line in enumerate(source.get("content", "").splitlines()):
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
