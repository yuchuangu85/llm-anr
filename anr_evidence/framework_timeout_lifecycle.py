"""Version-pinned, fail-closed Framework timeout packs and observations.

A timeout label in a bugreport is an observation, not a root-cause conclusion.
This module therefore has two distinct stages:

* acquire/verify a pack from the *specific* release tag, peeled framework/base
  commit and canonical Gitiles source blobs; and
* derive a component lifecycle only from exact timeout-kind/component/record
  observations that are supported by that verified pack.

The built-in recipes intentionally contain no copied Android 17 byte hash.
Each Android 14--17 recipe has an independent tag and commit.  A kind remains
unverified until the caller obtains and checks its own release's bytes.
"""
from __future__ import annotations

from base64 import b64decode
from copy import deepcopy
from datetime import datetime
import hashlib
import re
from typing import Any, Callable, Iterable
from urllib.request import urlopen

from .aosp_contract.gitiles import (
    GitilesProvenanceError,
    immutable_file_url,
    revision_from_tag_json,
    tag_ref_url,
)
from .log_filter import parse_log_timestamp


_RECIPE_SCHEMA_VERSION = "framework-timeout-pack-recipe-v1"
_PACK_SCHEMA_VERSION = "framework-timeout-pack-v1"
_LIFECYCLE_SCHEMA_VERSION = "framework-timeout-lifecycle-v1"
_REPOSITORY = "platform/frameworks/base"
_HEX_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_HEX_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_TIMEOUT_KIND_RE = re.compile(r"\b(?:timeoutKind|timeout_kind|kind)\s*[:=]\s*(?P<value>[A-Z][A-Z0-9_]+)\b")
_COMPONENT_RE = re.compile(
    r"\bcomponent\s*[:=]\s*(?P<package>[A-Za-z][\w.]*)/(?P<class>[A-Za-z0-9_.$]+)",
    re.IGNORECASE,
)
_RECORD_KEY_RE = re.compile(r"\b(?:recordId|timeoutId|deliveryId|token)\s*[:=]\s*(?P<value>[A-Za-z0-9_.:@/-]+)", re.IGNORECASE)
_TRIGGER_RE = re.compile(r"\b(?:trigger(?:ed)?|timed\s*out|timeout)\b", re.IGNORECASE)
_CANCEL_RE = re.compile(r"\b(?:cancel(?:led)?|aborted|stopped)\b", re.IGNORECASE)
_RECOVERY_RE = re.compile(r"\b(?:recover(?:ed|y)?|finish(?:ed)?|complete(?:d)?)\b", re.IGNORECASE)


class FrameworkTimeoutPackError(ValueError):
    """Malformed, unavailable, or non-verifiable Framework timeout pack."""


def _canonical_url(commit: str, path: str) -> str:
    return f"https://android.googlesource.com/{_REPOSITORY}/+/{commit}/{path}"


def _recipe_sources() -> dict[str, dict[str, Any]]:
    """Return shared route definitions without release-specific provenance."""
    return {
        "timeoutRecord": {
            "path": "core/java/com/android/internal/os/TimeoutRecord.java",
            "symbols": ("TimeoutRecord", "TimeoutKind"),
        },
        "broadcastModern": {
            "path": "services/core/java/com/android/server/am/BroadcastQueueModernImpl.java",
            "symbols": ("TimeoutRecord.forBroadcastReceiver",),
        },
        "broadcastLegacy": {
            "path": "services/core/java/com/android/server/am/BroadcastQueueImpl.java",
            "symbols": ("TimeoutRecord.forBroadcastReceiver",),
        },
        "activeServices": {
            "path": "services/core/java/com/android/server/am/ActiveServices.java",
            "symbols": ("TimeoutRecord.forServiceExec", "serviceTimeout"),
        },
        "contentProviderHelper": {
            "path": "services/core/java/com/android/server/am/ContentProviderHelper.java",
            "symbols": ("appNotRespondingViaProvider", "TimeoutRecord.forContentProvider"),
        },
        "jobService": {
            "path": "apex/jobscheduler/service/java/com/android/server/job/JobServiceContext.java",
            "symbols": ("TimeoutRecord.forJobService",),
        },
        "activityManager": {
            "path": "services/core/java/com/android/server/am/ActivityManagerService.java",
            "symbols": ("TimeoutRecord.forAppStart", "appNotResponding"),
        },
        "iActivityManager": {
            "path": "core/java/android/app/IActivityManager.aidl",
            "symbols": ("appNotResponding",),
        },
    }


def _kind_definitions() -> dict[str, dict[str, Any]]:
    """Route-specific symbols are checked against each release's own bytes."""
    return {
        "BROADCAST_RECEIVER": {
            "timeoutRecordKind": "BROADCAST_RECEIVER",
            "sourceRefs": ("timeoutRecord",),
            "alternativeSourceRefs": (("broadcastModern",), ("broadcastLegacy",)),
            "requiredSymbols": {
                "timeoutRecord": ("BROADCAST_RECEIVER",),
                "broadcastModern": ("TimeoutRecord.forBroadcastReceiver",),
                "broadcastLegacy": ("TimeoutRecord.forBroadcastReceiver",),
            },
            "runtimeConditions": ("modern_or_legacy_broadcast_queue",),
        },
        "SERVICE_EXEC": {
            "timeoutRecordKind": "SERVICE_EXEC",
            "sourceRefs": ("timeoutRecord", "activeServices"),
            "requiredSymbols": {
                "timeoutRecord": ("SERVICE_EXEC",),
                "activeServices": ("TimeoutRecord.forServiceExec", "serviceTimeout"),
            },
            "runtimeConditions": ("service_timeout_handler",),
        },
        "SERVICE_START": {
            "timeoutRecordKind": "SERVICE_START",
            "sourceRefs": ("timeoutRecord", "activeServices"),
            "requiredSymbols": {
                "timeoutRecord": ("SERVICE_START",),
                "activeServices": ("TimeoutRecord.forServiceStartWithEndTime", "serviceForegroundTimeoutANR"),
            },
            "runtimeConditions": ("foreground_service_start_timeout",),
        },
        "CONTENT_PROVIDER": {
            "timeoutRecordKind": "CONTENT_PROVIDER",
            "sourceRefs": ("timeoutRecord", "contentProviderHelper"),
            "requiredSymbols": {
                "timeoutRecord": ("CONTENT_PROVIDER",),
                "contentProviderHelper": ("appNotRespondingViaProvider", "TimeoutRecord.forContentProvider"),
            },
            "runtimeConditions": ("provider_not_responding_path",),
        },
        "APP_REGISTERED": {
            "timeoutRecordKind": "APP_REGISTERED",
            "sourceRefs": ("timeoutRecord", "iActivityManager", "activityManager"),
            "requiredSymbols": {
                "timeoutRecord": ("APP_REGISTERED",),
                "iActivityManager": ("appNotResponding",),
                "activityManager": ("appNotResponding",),
            },
            "runtimeConditions": ("app_reported_not_responding",),
        },
        "SHORT_FGS_TIMEOUT": {
            "timeoutRecordKind": "SHORT_FGS_TIMEOUT",
            "sourceRefs": ("timeoutRecord", "activeServices"),
            "requiredSymbols": {
                "timeoutRecord": ("SHORT_FGS_TIMEOUT",),
                "activeServices": ("TimeoutRecord.forShortFgsTimeout",),
            },
            "runtimeConditions": ("short_or_time_limited_fgs",),
        },
        "JOB_SERVICE": {
            "timeoutRecordKind": "JOB_SERVICE",
            "sourceRefs": ("timeoutRecord", "jobService"),
            "requiredSymbols": {
                "timeoutRecord": ("JOB_SERVICE",),
                "jobService": ("TimeoutRecord.forJobService", "OP_TIMEOUT_MILLIS"),
            },
            "runtimeConditions": ("job_compat_and_target_sdk_gates",),
        },
        "APP_START": {
            "timeoutRecordKind": "APP_START",
            "sourceRefs": ("timeoutRecord", "activityManager"),
            "requiredSymbols": {
                "timeoutRecord": ("APP_START",),
                "activityManager": ("TimeoutRecord.forAppStart", "handleBindApplicationTimeoutHard"),
            },
            "runtimeConditions": ("bind_application_hard_timeout",),
        },
        # This is a deliberate negative pack entry.  Provider publish handling
        # may kill/clean up a process; it is not automatically an ANR path.
        "CONTENT_PROVIDER_PUBLISH_TIMEOUT": {
            "timeoutRecordKind": None,
            "sourceRefs": ("activityManager",),
            "requiredSymbols": {"activityManager": ("CONTENT_PROVIDER_PUBLISH_TIMEOUT",)},
            "runtimeConditions": ("provider_publish_timeout_cleanup",),
            "status": "non_anr_timeout",
            "mustNotPromoteToFrameworkAnr": True,
        },
    }


# The commits are release-specific peeled frameworks/base commits.  They must
# never be shared between recipes.  Blob hashes are intentionally absent: they
# are produced only after fetching a source from this exact release commit.
_RELEASES = {
    14: ("android-14.0.0_r1", "299fe6f5d6fc6f1af7c3411dcf4e5efdf7217368"),
    15: ("android-15.0.0_r1", "4e43ad12e1b211f15152e9c5b16b0fe88e6b93f3"),
    16: ("android-16.0.0_r1", "99b01a65cc4c104933788b3143285ab6bae65827"),
    17: ("android-17.0.0_r1", "94b4c163b7dfe5ce3607f7bb8456f9573f7de57d"),
}


def framework_timeout_pack_recipe(android_release: int) -> dict[str, Any]:
    """Get one release's unverified acquisition recipe.

    Recipes are intentionally not operational profiles.  They only pin a
    release tag/commit and enumerate routes to fetch.  ``verify_*`` below is
    required before an observation can be interpreted as a Framework timeout.
    """
    try:
        tag, commit = _RELEASES[android_release]
    except KeyError as exc:
        raise FrameworkTimeoutPackError(f"Unsupported Android release: {android_release}") from exc
    sources = {
        source_id: {
            "repository": _REPOSITORY,
            "revision": commit,
            "path": definition["path"],
            "url": _canonical_url(commit, definition["path"]),
            "symbols": list(definition["symbols"]),
        }
        for source_id, definition in _recipe_sources().items()
    }
    kinds = {
        kind: {
            "timeoutRecordKind": definition["timeoutRecordKind"],
            "sourceRefs": list(definition["sourceRefs"]),
            "alternativeSourceRefs": [list(route) for route in definition.get("alternativeSourceRefs", ())],
            "requiredSymbols": {source_id: list(symbols) for source_id, symbols in definition["requiredSymbols"].items()},
            "runtimeConditions": list(definition["runtimeConditions"]),
            "status": definition.get("status", "unverified"),
            "mustNotPromoteToFrameworkAnr": bool(definition.get("mustNotPromoteToFrameworkAnr", False)),
        }
        for kind, definition in _kind_definitions().items()
    }
    return {
        "schemaVersion": _RECIPE_SCHEMA_VERSION,
        "profileId": f"aosp-{tag}-framework-timeout-recipe",
        "androidRelease": android_release,
        "releaseTag": tag,
        "frameworksBaseCommit": commit,
        "provenanceStatus": "pending_source_acquisition",
        "sources": sources,
        "kinds": kinds,
        "finalJudgment": False,
        "notRootCauseYet": True,
        "requiresHumanConfirmation": True,
    }


def _source_input(source: Any) -> dict[str, Any] | None:
    """Accept only self-describing Gitiles source records, never bare text."""
    return source if isinstance(source, dict) else None


def _verified_source(recipe_source: dict[str, Any], supplied: Any) -> dict[str, Any]:
    value = _source_input(supplied)
    result = {
        "repository": recipe_source["repository"],
        "revision": recipe_source["revision"],
        "path": recipe_source["path"],
        "url": recipe_source["url"],
        "symbols": list(recipe_source["symbols"]),
        "status": "missing",
        "sha256": None,
        "gaps": [],
    }
    if value is None:
        result["gaps"].append("source_blob_missing")
        return result
    content = value.get("content")
    if not isinstance(content, str):
        result["status"] = "invalid"
        result["gaps"].append("source_content_missing")
        return result
    for field in ("repository", "revision", "path", "url"):
        if value.get(field) != recipe_source[field]:
            result["status"] = "invalid"
            result["gaps"].append(f"source_{field}_mismatch")
            return result
    computed = hashlib.sha256(content.encode("utf-8")).hexdigest()
    declared_hash = value.get("sha256")
    if not isinstance(declared_hash, str) or declared_hash != computed:
        result["status"] = "invalid"
        result["sha256"] = computed
        result["gaps"].append("source_sha256_mismatch")
        return result
    missing_symbols = [symbol for symbol in recipe_source["symbols"] if symbol not in content]
    result["sha256"] = computed
    if missing_symbols:
        result["status"] = "invalid"
        result["gaps"].append("required_symbol_missing")
        result["missingSymbols"] = missing_symbols
        return result
    result["status"] = "verified"
    return result


def verify_framework_timeout_pack(
    recipe: dict[str, Any],
    fetched_sources: dict[str, Any],
) -> dict[str, Any]:
    """Verify exact release bytes and symbols for one Framework timeout pack.

    ``fetched_sources`` is normally produced by
    :func:`retrieve_framework_timeout_sources`; accepting it explicitly also
    makes provenance tests deterministic and prevents hidden network I/O.
    """
    if recipe.get("schemaVersion") != _RECIPE_SCHEMA_VERSION:
        raise FrameworkTimeoutPackError("Unknown framework timeout recipe schema")
    release = recipe.get("androidRelease")
    if not isinstance(release, int):
        raise FrameworkTimeoutPackError("Recipe androidRelease is invalid")
    canonical = framework_timeout_pack_recipe(release)
    for field in ("profileId", "releaseTag", "frameworksBaseCommit"):
        if recipe.get(field) != canonical[field]:
            raise FrameworkTimeoutPackError(f"Recipe {field} conflicts with independently pinned release")
    if recipe.get("sources") != canonical["sources"] or recipe.get("kinds") != canonical["kinds"]:
        raise FrameworkTimeoutPackError("Recipe route declarations conflict with independently pinned release")

    sources = {
        source_id: _verified_source(source_recipe, fetched_sources.get(source_id))
        for source_id, source_recipe in canonical["sources"].items()
    }
    kinds: dict[str, dict[str, Any]] = {}
    for kind, definition in canonical["kinds"].items():
        source_refs = definition["sourceRefs"]
        source_statuses = {source_id: sources[source_id]["status"] for source_id in definition["requiredSymbols"]}
        missing_symbols: dict[str, list[str]] = {}
        for source_id, required_symbols in definition["requiredSymbols"].items():
            supplied = _source_input(fetched_sources.get(source_id))
            content = supplied.get("content") if supplied else None
            if not isinstance(content, str):
                missing_symbols[source_id] = list(required_symbols)
                continue
            missing = [symbol for symbol in required_symbols if symbol not in content]
            if missing:
                missing_symbols[source_id] = missing

        base_ok = all(
            source_statuses[source_id] == "verified" and source_id not in missing_symbols
            for source_id in source_refs
        )
        alternatives = definition.get("alternativeSourceRefs") or []
        verified_routes = [
            list(route) for route in alternatives
            if all(source_statuses[source_id] == "verified" and source_id not in missing_symbols for source_id in route)
        ]
        base_invalid = any(
            source_statuses[source_id] == "invalid" or source_id in missing_symbols
            for source_id in source_refs
        )
        alternatives_invalid = bool(alternatives) and all(
            any(source_statuses[source_id] == "invalid" or source_id in missing_symbols for source_id in route)
            for route in alternatives
        )
        if definition["status"] == "non_anr_timeout":
            status = "non_anr_timeout"
        elif base_invalid or alternatives_invalid:
            status = "invalid"
        elif base_ok and (not alternatives or verified_routes):
            status = "verified"
        else:
            status = "unverified"
        kinds[kind] = {**definition, "status": status}
        if verified_routes:
            kinds[kind]["verifiedRoutes"] = verified_routes
        if missing_symbols:
            kinds[kind]["missingSymbols"] = missing_symbols
    verified_count = sum(source["status"] == "verified" for source in sources.values())
    return {
        "schemaVersion": _PACK_SCHEMA_VERSION,
        "profileId": canonical["profileId"].replace("-recipe", ""),
        "androidRelease": release,
        "releaseTag": canonical["releaseTag"],
        "frameworksBaseCommit": canonical["frameworksBaseCommit"],
        "provenanceStatus": "verified" if verified_count == len(sources) else "partial" if verified_count else "unverified",
        "sources": sources,
        "kinds": kinds,
        "finalJudgment": False,
        "notRootCauseYet": True,
        "requiresHumanConfirmation": True,
    }



def _default_fetch(url: str) -> bytes:
    with urlopen(url, timeout=30) as response:  # noqa: S310 - canonical Google source only
        return response.read()


def retrieve_framework_timeout_sources(
    android_release: int,
    *,
    fetch: Callable[[str], bytes] | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Fetch canonical Gitiles bytes after rechecking tag -> peeled commit.

    This performs network I/O only when explicitly called.  It fails closed on
    a missing tag, tag/commit mismatch, decode failure, or missing source.
    """
    recipe = framework_timeout_pack_recipe(android_release)
    fetcher = fetch or _default_fetch
    tag_url = tag_ref_url(_REPOSITORY, recipe["releaseTag"])
    try:
        observed_tag_commit = revision_from_tag_json(fetcher(tag_url), recipe["releaseTag"])
    except (OSError, UnicodeError, TimeoutError, GitilesProvenanceError) as exc:
        raise FrameworkTimeoutPackError("Unable to verify upstream release tag") from exc
    if observed_tag_commit != recipe["frameworksBaseCommit"]:
        raise FrameworkTimeoutPackError("Upstream tag does not resolve to pinned release commit")

    fetched: dict[str, dict[str, Any]] = {}
    for source_id, source in recipe["sources"].items():
        endpoint = immutable_file_url(_REPOSITORY, recipe["frameworksBaseCommit"], source["path"]) + "?format=TEXT"
        try:
            content = b64decode(fetcher(endpoint), validate=True).decode("utf-8")
        except (OSError, UnicodeError, ValueError, TimeoutError) as exc:
            raise FrameworkTimeoutPackError(f"Unable to retrieve source bytes for {source_id}") from exc
        fetched[source_id] = {**source, "content": content, "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()}
    return recipe, fetched


def _append_gap(result: dict[str, Any], gap: str) -> None:
    if gap not in result["gaps"]:
        result["gaps"].append(gap)


def _target_package(anchor: dict[str, Any] | None) -> str | None:
    if not isinstance(anchor, dict):
        return None
    package = anchor.get("packageName") or anchor.get("package")
    return package.strip() if isinstance(package, str) and package.strip() else None


def _line_state(line: str) -> str:
    # Cancellation/recovery are checked first because their payload commonly
    # repeats "timeout" or "trigger" when referring to the same record.
    if _CANCEL_RE.search(line):
        return "cancelled"
    if _RECOVERY_RE.search(line):
        return "recovered"
    if _TRIGGER_RE.search(line):
        return "triggered"
    return "observed"


def _verified_pack_matches_recipe(pack: dict[str, Any], recipe: dict[str, Any]) -> bool:
    """Reject a shaped-but-mutated pack before using it for observations."""
    if (
        pack.get("schemaVersion") != _PACK_SCHEMA_VERSION
        or pack.get("profileId") != recipe["profileId"].replace("-recipe", "")
        or pack.get("provenanceStatus") != "verified"
    ):
        return False
    sources = pack.get("sources")
    kinds = pack.get("kinds")
    if not isinstance(sources, dict) or not isinstance(kinds, dict):
        return False
    for source_id, expected in recipe["sources"].items():
        actual = sources.get(source_id)
        if not isinstance(actual, dict):
            return False
        if actual.get("status") != "verified" or not isinstance(actual.get("sha256"), str) or not _HEX_SHA256_RE.fullmatch(actual["sha256"]):
            return False
        if any(actual.get(field) != expected[field] for field in ("repository", "revision", "path", "url", "symbols")):
            return False
    for kind, expected in recipe["kinds"].items():
        actual = kinds.get(kind)
        if not isinstance(actual, dict):
            return False
        for field in ("timeoutRecordKind", "sourceRefs", "alternativeSourceRefs", "requiredSymbols", "runtimeConditions", "mustNotPromoteToFrameworkAnr"):
            if actual.get(field) != expected.get(field):
                return False
        if expected["status"] == "non_anr_timeout":
            if actual.get("status") != "non_anr_timeout":
                return False
        elif actual.get("status") == "verified":
            routes = actual.get("verifiedRoutes", [])
            alternatives = expected.get("alternativeSourceRefs", [])
            if alternatives and not any(route in alternatives for route in routes):
                return False
        elif actual.get("status") not in {"invalid", "unverified"}:
            return False
    return True


def _empty_lifecycle_result(group_id: str, android_release: Any) -> dict[str, Any]:
    return {
        "schemaVersion": _LIFECYCLE_SCHEMA_VERSION,
        "groupId": group_id,
        "androidRelease": android_release,
        "profileId": None,
        "timeoutKind": None,
        "trigger": {"status": "unknown", "sourceEventIds": []},
        "component": None,
        "lifecycle": {"status": "unknown", "sourceEventIds": []},
        "edges": [],
        "gaps": [],
        "triggerIsRootCause": False,
        "eligibleForHighCausalConfidence": False,
        "finalJudgment": False,
        "notRootCauseYet": True,
        "requiresHumanConfirmation": True,
    }


def build_framework_timeout_lifecycle(
    *,
    group_id: str,
    android_release: int | None,
    verified_pack: dict[str, Any] | None,
    anchor: dict[str, Any] | None,
    source_lines: Iterable[str],
    timeout_kind: str | None = None,
) -> dict[str, Any]:
    """Derive a strict component lifecycle from an independently verified pack."""
    result = _empty_lifecycle_result(group_id, android_release)
    if not isinstance(android_release, int) or android_release not in _RELEASES:
        _append_gap(result, "unsupported_android_release")
        return result
    if not isinstance(verified_pack, dict):
        _append_gap(result, "framework_timeout_pack_unavailable")
        return result
    if verified_pack.get("schemaVersion") != _PACK_SCHEMA_VERSION:
        _append_gap(result, "framework_timeout_pack_unverified")
        return result
    canonical_recipe = framework_timeout_pack_recipe(android_release)
    if (
        verified_pack.get("androidRelease") != android_release
        or verified_pack.get("releaseTag") != canonical_recipe["releaseTag"]
        or verified_pack.get("frameworksBaseCommit") != canonical_recipe["frameworksBaseCommit"]
    ):
        _append_gap(result, "framework_timeout_pack_provenance_mismatch")
        return result
    if not _verified_pack_matches_recipe(verified_pack, canonical_recipe):
        _append_gap(result, "framework_timeout_pack_provenance_invalid")
        return result
    result["profileId"] = verified_pack.get("profileId")
    target_package = _target_package(anchor)
    if target_package is None:
        _append_gap(result, "selected_component_package_unknown")

    observations: list[dict[str, Any]] = []
    source_indexes: dict[str, int] = {}
    for item in source_lines:
        if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str) and isinstance(item[1], str):
            source_name, line = item
        elif isinstance(item, str):
            source_name, line = "source", item
        else:
            continue
        index = source_indexes.get(source_name, 0)
        source_indexes[source_name] = index + 1
        kind_match = _TIMEOUT_KIND_RE.search(line)
        if not kind_match:
            continue
        observed_kind = kind_match.group("value")
        if timeout_kind is not None and observed_kind != timeout_kind:
            _append_gap(result, "declared_timeout_kind_conflict")
            continue
        kind = verified_pack.get("kinds", {}).get(observed_kind)
        if not isinstance(kind, dict):
            _append_gap(result, "timeout_kind_unknown_to_pack")
            continue
        if kind.get("status") == "non_anr_timeout" or kind.get("mustNotPromoteToFrameworkAnr"):
            _append_gap(result, "non_anr_timeout_must_not_promote")
            continue
        if kind.get("status") != "verified":
            _append_gap(result, "timeout_kind_source_provenance_unverified")
            continue
        component_match = _COMPONENT_RE.search(line)
        if not component_match:
            _append_gap(result, "timeout_component_identity_unknown")
            continue
        component = {
            "kind": "component",
            "package": component_match.group("package"),
            "name": component_match.group("class"),
        }
        if target_package is None or component["package"] != target_package:
            _append_gap(result, "timeout_component_not_selected_target")
            continue
        key_match = _RECORD_KEY_RE.search(line)
        observations.append({
            "sourceEventId": f"{source_name}:{index}",
            "timestamp": parse_log_timestamp(line),
            "ordinal": index,
            "kind": observed_kind,
            "component": component,
            "recordKey": key_match.group("value") if key_match else None,
            "state": _line_state(line),
        })

    if not observations:
        _append_gap(result, "framework_timeout_trigger_unobserved")
        return result
    kinds = {item["kind"] for item in observations}
    if len(kinds) != 1:
        _append_gap(result, "multiple_framework_timeout_kinds_unresolved")
        return result
    result["timeoutKind"] = next(iter(kinds))
    # A lifecycle key is mandatory for a cancellation/recovery transition.  A
    # bare trigger remains a trigger observation but does not gain a state
    # transition or causal confidence.
    grouped: dict[tuple[str, str, str | None], list[dict[str, Any]]] = {}
    for observation in observations:
        component = observation["component"]
        key = (observation["kind"], component["package"] + "/" + component["name"], observation["recordKey"])
        grouped.setdefault(key, []).append(observation)
    if len(grouped) != 1:
        _append_gap(result, "multiple_framework_timeout_records_unresolved")
        return result
    ordered = sorted(next(iter(grouped.values())), key=lambda item: (item["timestamp"] is None, item["timestamp"] or datetime.max, item["ordinal"]))
    trigger = next((item for item in ordered if item["state"] == "triggered"), None)
    if trigger is None:
        _append_gap(result, "framework_timeout_trigger_unobserved")
        return result
    result["trigger"] = {
        "status": "observed",
        "sourceEventIds": [trigger["sourceEventId"]],
        "recordKey": trigger["recordKey"],
    }
    result["component"] = trigger["component"]
    terminal = next((item for item in ordered if item["ordinal"] > trigger["ordinal"] and item["state"] in {"cancelled", "recovered"}), None)
    if trigger["recordKey"] is None and terminal is not None:
        terminal = None
        _append_gap(result, "timeout_lifecycle_correlation_key_unknown")
    lifecycle_status = terminal["state"] if terminal else "triggered_at_anchor"
    result["lifecycle"] = {
        "status": lifecycle_status,
        "sourceEventIds": [item["sourceEventId"] for item in ordered],
        "recordKey": trigger["recordKey"],
    }
    result["edges"].append({
        "kind": "framework_timeout_trigger_observation",
        "timeoutKind": result["timeoutKind"],
        "component": result["component"],
        "lifecycleStatus": lifecycle_status,
        "evidenceEventIds": result["lifecycle"]["sourceEventIds"],
        "causalStatus": "trigger_only",
    })
    _append_gap(result, "trigger_not_root_cause")
    return result
