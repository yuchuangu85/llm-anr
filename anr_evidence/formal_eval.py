"""Fail-closed E1/Q1 formal evaluation gates.

The historical hint eval remains a useful regression suite, but it cannot
establish that a corpus is frozen, cross-version, permitted for evaluation,
or that a claimed mechanism carries immutable evidence references.  This
module adds a separate, deterministic formal-eval surface:

* every case declares corpus revision, source class/licence/deidentification,
  Android release, and device/build identity;
* expected mechanisms are checked independently of hint IDs;
* selected evidence is exported through the EvidenceStore and freshly rebound
  from the generated ESS + sidecar before being counted as a citation;
* ontology observation/provenance expectations are verified from the pinned
  AOSP contract; and
* aggregate gates reject incomplete release coverage, synthetic/unlicensed
  formal corpora, missing frozen metadata, or resource-budget overruns.

A synthetic fixture may exercise evaluator rules, but it can never qualify a
production formal corpus.  The evaluator contains no model invocation; its
model-cost budget is deliberately fixed at zero.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path
import tempfile
from time import perf_counter
from typing import Any, Iterable

from .ai_context import AiContextOptions, build_ai_context, build_ai_context_artifacts
from .aosp_contract import resolve_timeout
from .evidence_slice import read_ess_jsonl
from .evidence_store import EvidenceStore, EvidenceStoreError
from .eval import collect_all_hint_ids, run_eval_case
from .loaders import load_package_from_directory


FORMAL_CASE_SCHEMA_VERSION = "anr-formal-eval-case-v1"
FORMAL_CORPUS_SCHEMA_VERSION = "anr-formal-eval-corpus-v1"
_PRODUCTION_SOURCE_CLASS = "licensed_deidentified"
_ALLOWED_SOURCE_CLASSES = frozenset({"synthetic", _PRODUCTION_SOURCE_CLASS})
_ALLOWED_DEIDENTIFICATION = frozenset({"not_applicable", "verified"})

# Mechanisms intentionally name an observed class rather than a final cause.
# Keep the mapping local and explicit; unknown future hint IDs do not become
# mechanism evidence until a reviewer adds a semantic mapping and fixtures.
_HINT_MECHANISMS = {
    "MAIN_BINDER_WAIT_REPLY": "main_thread_binder_reply_wait",
    "MAIN_SP_APPLY_WAIT": "main_thread_queued_work_wait",
    "MAIN_DB_BLOCKED": "main_thread_synchronous_database",
    "MAIN_RENDER_WAIT_FENCE": "main_thread_render_fence_wait",
    "NATIVE_POLL_BUT_BUSY": "main_thread_busy_looper",
    "NATIVE_POLL_IDLE_LIKELY": "main_thread_idle_snapshot",
    "DEADLOCK_CYCLE": "thread_deadlock_cycle",
    "DEADLOCK_SELF": "thread_deadlock_self",
    "LOCK_OWNER_BLOCKED": "lock_owner_blocked",
    "LOCK_OWNER_SLEEPING": "lock_owner_sleeping",
    "SYSTEM_CPU_SATURATED": "system_cpu_pressure",
    "SYSTEM_IO_PRESSURE": "system_io_pressure",
}


@dataclass(frozen=True)
class FormalEvalCaseResult:
    case_id: str
    description: str
    passed: bool
    android_release: int | None
    source_class: str | None
    detected_hint_ids: list[str]
    detected_mechanism_ids: list[str]
    required_mechanism_ids: list[str]
    forbidden_mechanism_ids: list[str]
    missing_mechanisms: list[str]
    triggered_forbidden_mechanisms: list[str]
    required_citation_source_kinds: list[str]
    verified_citation_source_counts: dict[str, int]
    verified_evidence_ref_count: int
    missing_citation_sources: list[str]
    ontology: dict[str, Any]
    ontology_errors: list[str]
    fixture_errors: list[str]
    budget_errors: list[str]
    wall_seconds: float
    input_bytes: int
    artifact_bytes: int
    model_calls: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FormalEvalAggregate:
    corpus_revision: str | None
    total: int
    passed: int
    failed: int
    qualified: bool
    qualification_status: str
    corpus_errors: list[str]
    gate_failures: list[str]
    release_coverage: dict[str, int]
    per_release_metrics: dict[str, dict[str, float | int]]
    source_class_counts: dict[str, int]
    resource_totals: dict[str, float | int]
    cases: list[FormalEvalCaseResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "corpusRevision": self.corpus_revision,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "qualified": self.qualified,
            "qualificationStatus": self.qualification_status,
            "corpusErrors": self.corpus_errors,
            "gateFailures": self.gate_failures,
            "releaseCoverage": self.release_coverage,
            "perReleaseMetrics": self.per_release_metrics,
            "sourceClassCounts": self.source_class_counts,
            "resourceTotals": self.resource_totals,
            "cases": [case.to_dict() for case in self.cases],
        }


def _nonempty_string(value: Any, path: str, errors: list[str]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path} must be a non-empty string")
        return None
    return value.strip()


def _strict_int(value: Any, path: str, errors: list[str], *, minimum: int = 0) -> int | None:
    if type(value) is not int or value < minimum:
        errors.append(f"{path} must be an integer >= {minimum}")
        return None
    return value


def _string_list(value: Any, path: str, errors: list[str], *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        errors.append(f"{path} must be an array of non-empty strings")
        return []
    result = [item.strip() for item in value]
    if len(set(result)) != len(result):
        errors.append(f"{path} must not contain duplicates")
    if nonempty and not result:
        errors.append(f"{path} must not be empty")
    return result


def _count_tree_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _safe_relative_path(value: Any, path: str, errors: list[str]) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path} must be a non-empty relative path")
        return None
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        errors.append(f"{path} must stay below the synthetic input root")
        return None
    return candidate


def _validate_corpus_manifest(data: Any) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return {}, ["corpus manifest must be an object"]
    if data.get("schemaVersion") != FORMAL_CORPUS_SCHEMA_VERSION:
        errors.append(f"schemaVersion must equal {FORMAL_CORPUS_SCHEMA_VERSION}")
    revision = _nonempty_string(data.get("corpusRevision"), "corpusRevision", errors)
    if data.get("frozen") is not True:
        errors.append("frozen must be true")
    releases = data.get("requiredAndroidReleases")
    if not isinstance(releases, list) or any(type(item) is not int or item < 1 for item in releases):
        errors.append("requiredAndroidReleases must be an array of positive integers")
        releases = []
    elif len(releases) < 2 or len(set(releases)) != len(releases):
        errors.append("requiredAndroidReleases must contain at least two unique releases")
    minimum = _strict_int(data.get("minimumCasesPerRelease"), "minimumCasesPerRelease", errors, minimum=1)
    source_class = _nonempty_string(data.get("requiredSourceClass"), "requiredSourceClass", errors)
    if source_class is not None and source_class != _PRODUCTION_SOURCE_CLASS:
        errors.append(f"requiredSourceClass must be {_PRODUCTION_SOURCE_CLASS}")
    case_sha256 = data.get("caseSha256")
    if not isinstance(case_sha256, dict) or not case_sha256:
        errors.append("caseSha256 must be a non-empty object mapping case filenames to SHA-256")
        case_sha256 = {}
    else:
        for filename, digest in case_sha256.items():
            if (not isinstance(filename, str) or Path(filename).name != filename
                    or not filename.endswith(".json")):
                errors.append("caseSha256 keys must be plain .json filenames")
            if (not isinstance(digest, str) or len(digest) != 64
                    or any(char not in "0123456789abcdef" for char in digest)):
                errors.append(f"caseSha256.{filename} must be a lowercase SHA-256")
    budget = data.get("executionBudget")
    if not isinstance(budget, dict):
        errors.append("executionBudget must be an object")
        budget = {}
    wall = budget.get("maxWallSecondsPerCase")
    if not isinstance(wall, (int, float)) or isinstance(wall, bool) or wall <= 0:
        errors.append("executionBudget.maxWallSecondsPerCase must be > 0")
    for field_name in ("maxInputBytesPerCase", "maxArtifactBytesPerCase"):
        if _strict_int(budget.get(field_name), f"executionBudget.{field_name}", errors) is None:
            pass
    if budget.get("maxModelCalls") != 0:
        errors.append("executionBudget.maxModelCalls must be exactly 0 for deterministic formal eval")
    normalized = {
        "corpusRevision": revision,
        "requiredAndroidReleases": list(releases),
        "minimumCasesPerRelease": minimum,
        "requiredSourceClass": source_class,
        "caseSha256": dict(case_sha256),
        "executionBudget": budget,
    }
    return normalized, errors


def _validate_case_metadata(case: dict[str, Any], manifest: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    if case.get("schemaVersion") != FORMAL_CASE_SCHEMA_VERSION:
        errors.append(f"schemaVersion must equal {FORMAL_CASE_SCHEMA_VERSION}")
    _nonempty_string(case.get("id"), "id", errors)
    _nonempty_string(case.get("description"), "description", errors)
    corpus = case.get("corpus")
    if not isinstance(corpus, dict):
        return {}, errors + ["corpus must be an object"]
    revision = _nonempty_string(corpus.get("corpusRevision"), "corpus.corpusRevision", errors)
    if revision is not None and revision != manifest.get("corpusRevision"):
        errors.append("corpus.corpusRevision must equal the frozen corpus manifest revision")
    if corpus.get("split") != "evaluation":
        errors.append("corpus.split must equal evaluation")
    source_class = _nonempty_string(corpus.get("sourceClass"), "corpus.sourceClass", errors)
    if source_class is not None and source_class not in _ALLOWED_SOURCE_CLASSES:
        errors.append("corpus.sourceClass is unsupported")
    _nonempty_string(corpus.get("sourceLicense"), "corpus.sourceLicense", errors)
    deidentification = _nonempty_string(corpus.get("deidentificationStatus"), "corpus.deidentificationStatus", errors)
    if deidentification is not None and deidentification not in _ALLOWED_DEIDENTIFICATION:
        errors.append("corpus.deidentificationStatus is unsupported")
    if source_class == _PRODUCTION_SOURCE_CLASS and deidentification != "verified":
        errors.append("licensed_deidentified cases require deidentificationStatus=verified")
    if source_class == "synthetic" and deidentification != "not_applicable":
        errors.append("synthetic cases require deidentificationStatus=not_applicable")
    release = _strict_int(corpus.get("androidRelease"), "corpus.androidRelease", errors, minimum=1)
    device = corpus.get("device")
    if not isinstance(device, dict):
        errors.append("corpus.device must be an object")
    else:
        _nonempty_string(device.get("model"), "corpus.device.model", errors)
        _nonempty_string(device.get("buildFingerprint"), "corpus.device.buildFingerprint", errors)
    package = case.get("package")
    if not isinstance(package, dict):
        errors.append("package must be an object")
    elif release is not None and package.get("android_release") != release:
        errors.append("package.android_release must equal corpus.androidRelease")
    return {"androidRelease": release, "sourceClass": source_class}, errors


def _expected_formal_fields(case: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    expected = case.get("expected")
    errors: list[str] = []
    if not isinstance(expected, dict):
        return {}, ["expected must be an object"]
    mechanisms = _string_list(expected.get("requiredMechanismIds"), "expected.requiredMechanismIds", errors, nonempty=True)
    forbidden = _string_list(expected.get("forbiddenMechanismIds", []), "expected.forbiddenMechanismIds", errors)
    sources = _string_list(expected.get("requiredCitationSourceKinds"), "expected.requiredCitationSourceKinds", errors, nonempty=True)
    minimum = _strict_int(expected.get("minVerifiedEvidenceRefs"), "expected.minVerifiedEvidenceRefs", errors, minimum=1)
    ontology = expected.get("timeoutOntology")
    if not isinstance(ontology, dict):
        errors.append("expected.timeoutOntology must be an object")
        ontology = {}
    else:
        _nonempty_string(ontology.get("canonicalTrigger"), "expected.timeoutOntology.canonicalTrigger", errors)
        _nonempty_string(ontology.get("evidenceStatus"), "expected.timeoutOntology.evidenceStatus", errors)
        _string_list(ontology.get("requiredProvenanceSymbols"), "expected.timeoutOntology.requiredProvenanceSymbols", errors, nonempty=True)
    return {
        "requiredMechanismIds": mechanisms,
        "forbiddenMechanismIds": forbidden,
        "requiredCitationSourceKinds": sources,
        "minVerifiedEvidenceRefs": minimum,
        "timeoutOntology": ontology,
    }, errors


def collect_mechanism_ids(ai_context_result: Any, timeout_ontology: dict[str, Any]) -> list[str]:
    """Return explicit observed mechanism IDs without upgrading causality."""
    mechanisms: set[str] = set()
    trigger = timeout_ontology.get("canonicalTrigger")
    if isinstance(trigger, str) and trigger:
        mechanisms.add(f"trigger:{trigger}")
    for hint_id in collect_all_hint_ids(ai_context_result, {}):
        mechanism = _HINT_MECHANISMS.get(hint_id)
        if mechanism:
            mechanisms.add(mechanism)
    return sorted(mechanisms)


def _materialize_captured_package(
    package: dict[str, Any], root: Path, errors: list[str], *, package_name: str | None
) -> tuple[dict[str, Any] | None, int]:
    sources = package.get("sources")
    if not isinstance(sources, dict):
        errors.append("package.sources must be an object")
        return None, 0
    input_root = root / "input"
    input_root.mkdir()
    total_bytes = 0
    for source_kind, source in sources.items():
        if not isinstance(source, dict):
            errors.append(f"package.sources.{source_kind} must be an object")
            continue
        relative = _safe_relative_path(source.get("path"), f"package.sources.{source_kind}.path", errors)
        content = source.get("content")
        if not isinstance(content, str):
            errors.append(f"package.sources.{source_kind}.content must be a string")
            continue
        if relative is None:
            continue
        target = input_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = content.encode("utf-8")
        target.write_bytes(raw)
        total_bytes += len(raw)
    if errors:
        return None, total_bytes
    package_name = package.get("package_id")
    captured = load_package_from_directory(input_root, package_name=package_name, capture_originals=True)
    for key, value in package.items():
        if key != "sources":
            captured[key] = deepcopy(value)
    return captured, total_bytes


def _verified_citations(package: dict[str, Any], *, package_name: str | None, root: Path, errors: list[str]) -> tuple[dict[str, int], int, int]:
    captured, input_bytes = _materialize_captured_package(package, root, errors, package_name=package_name)
    if captured is None:
        return {}, 0, input_bytes
    output_root = root / "output"
    try:
        summary = build_ai_context_artifacts(
            captured,
            AiContextOptions(
                out_dir=output_root,
                package_name=package_name,
                build_evidence_store=True,
                build_ess=True,
            ),
        )
        store_path = Path(summary["artifactPaths"]["evidenceStore"])
        counts: dict[str, int] = {}
        verified = 0
        with EvidenceStore.open_read_only(store_path) as store:
            for group in summary.get("groups", []):
                paths = group.get("artifactPaths") or {}
                refs_path = Path(paths["evidenceRefs"])
                slices_path = Path(paths["evidenceSlices"])
                sidecar = json.loads(refs_path.read_text(encoding="utf-8"))
                group_id = sidecar.get("groupId")
                if not isinstance(group_id, str):
                    errors.append("evidence sidecar groupId is missing")
                    continue
                slices = read_ess_jsonl(
                    slices_path,
                    store=store,
                    references={group_id: sidecar.get("references", [])},
                )
                for slice_ in slices:
                    if slice_.evidence_ref is None or slice_.evidence_ref_error is not None:
                        continue
                    verified += 1
                    counts[slice_.source] = counts.get(slice_.source, 0) + 1
        return counts, verified, input_bytes
    except (EvidenceStoreError, KeyError, OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        errors.append(f"immutable evidence export failed: {type(error).__name__}: {error}")
        return {}, 0, input_bytes


def _ontology_errors(actual: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in ("canonicalTrigger", "evidenceStatus"):
        value = expected.get(field)
        if isinstance(value, str) and actual.get(field) != value:
            errors.append(f"timeoutOntology.{field} expected {value!r}, got {actual.get(field)!r}")
    required_sources = expected.get("requiredProvenanceSymbols", [])
    actual_sources = {
        item.get("symbol") for item in actual.get("provenanceRefs", [])
        if isinstance(item, dict) and isinstance(item.get("symbol"), str)
    }
    # Source keys (base/native) are not carried in the emitted refs, so use
    # their manifest symbols as immutable provenance identities in fixtures.
    for source_name in required_sources:
        if source_name not in actual_sources:
            errors.append(f"timeoutOntology provenance source not observed: {source_name}")
    if not actual.get("observationEvidenceRefs"):
        errors.append("timeoutOntology requires at least one observed reason reference")
    return errors


def run_formal_eval_case(case_path: str | Path, *, corpus_manifest: dict[str, Any]) -> FormalEvalCaseResult:
    """Run one formal case, including immutable evidence-store readback."""
    path = Path(case_path)
    try:
        case = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return FormalEvalCaseResult(
            case_id=path.stem,
            description="",
            passed=False,
            android_release=None,
            source_class=None,
            detected_hint_ids=[],
            detected_mechanism_ids=[],
            required_mechanism_ids=[],
            forbidden_mechanism_ids=[],
            missing_mechanisms=[],
            triggered_forbidden_mechanisms=[],
            required_citation_source_kinds=[],
            verified_citation_source_counts={},
            verified_evidence_ref_count=0,
            missing_citation_sources=[],
            ontology={},
            ontology_errors=[],
            fixture_errors=[f"fixture unreadable: {type(error).__name__}: {error}"],
            budget_errors=[],
            wall_seconds=0.0,
            input_bytes=0,
            artifact_bytes=0,
        )
    if not isinstance(case, dict):
        case = {}
    metadata, fixture_errors = _validate_case_metadata(case, corpus_manifest)
    expected, expected_errors = _expected_formal_fields(case)
    fixture_errors.extend(expected_errors)
    case_id = case.get("id") if isinstance(case.get("id"), str) else path.stem
    description = case.get("description") if isinstance(case.get("description"), str) else ""
    package = case.get("package") if isinstance(case.get("package"), dict) else {}
    release = metadata.get("androidRelease")
    source_class = metadata.get("sourceClass")
    budget = corpus_manifest.get("executionBudget") or {}
    started = perf_counter()
    detected_hints: list[str] = []
    detected_mechanisms: list[str] = []
    ontology: dict[str, Any] = {}
    citation_counts: dict[str, int] = {}
    verified_refs = 0
    input_bytes = 0
    artifact_bytes = 0
    semantic_notes: list[str] = []
    ontology_errors: list[str] = []
    citation_errors: list[str] = []
    legacy_passed = False
    if not fixture_errors:
        try:
            legacy = run_eval_case(path)
            legacy_passed = legacy.passed
            semantic_notes.extend(legacy.notes)
            result = build_ai_context(
                package,
                AiContextOptions(package_name=case.get("expected", {}).get("packageNameFilter")),
            )
            detected_hints = collect_all_hint_ids(result, package)
            ontology = resolve_timeout(package)
            detected_mechanisms = collect_mechanism_ids(result, ontology)
            ontology_errors = _ontology_errors(ontology, expected["timeoutOntology"])
            with tempfile.TemporaryDirectory(prefix="anr-formal-eval-") as temporary:
                temp_root = Path(temporary)
                citation_counts, verified_refs, input_bytes = _verified_citations(
                    package,
                    package_name=case.get("expected", {}).get("packageNameFilter"),
                    root=temp_root,
                    errors=citation_errors,
                )
                artifact_bytes = _count_tree_bytes(temp_root / "output") if (temp_root / "output").exists() else 0
        except (KeyError, OSError, TypeError, ValueError) as error:
            fixture_errors.append(f"formal evaluation execution failed: {type(error).__name__}: {error}")
    wall_seconds = perf_counter() - started
    required_mechanisms = expected.get("requiredMechanismIds", [])
    forbidden_mechanisms = expected.get("forbiddenMechanismIds", [])
    detected_mechanism_set = set(detected_mechanisms)
    missing_mechanisms = [item for item in required_mechanisms if item not in detected_mechanism_set]
    triggered_forbidden = [item for item in forbidden_mechanisms if item in detected_mechanism_set]
    required_sources = expected.get("requiredCitationSourceKinds", [])
    missing_sources = [item for item in required_sources if citation_counts.get(item, 0) == 0]
    if verified_refs < (expected.get("minVerifiedEvidenceRefs") or 0):
        citation_errors.append(
            f"verified immutable evidence refs {verified_refs} below expected minimum {expected.get('minVerifiedEvidenceRefs')}"
        )
    if missing_sources:
        citation_errors.append(f"missing required immutable citation source kinds: {missing_sources}")
    budget_errors: list[str] = []
    wall_limit = budget.get("maxWallSecondsPerCase")
    if isinstance(wall_limit, (int, float)) and not isinstance(wall_limit, bool) and wall_seconds > wall_limit:
        budget_errors.append(f"wall_seconds {wall_seconds:.6f} exceeds {wall_limit}")
    for metric, value in (("maxInputBytesPerCase", input_bytes), ("maxArtifactBytesPerCase", artifact_bytes)):
        limit = budget.get(metric)
        if type(limit) is int and value > limit:
            budget_errors.append(f"{metric} exceeded: {value} > {limit}")
    if budget.get("maxModelCalls") == 0:
        model_calls = 0
    else:
        model_calls = 0
        budget_errors.append("invalid maxModelCalls budget prevented deterministic model-cost check")
    if not legacy_passed:
        semantic_notes.append("legacy hint expectations failed")
    if missing_mechanisms:
        semantic_notes.append(f"missing mechanisms: {missing_mechanisms}")
    if triggered_forbidden:
        semantic_notes.append(f"forbidden mechanisms observed: {triggered_forbidden}")
    semantic_notes.extend(ontology_errors)
    semantic_notes.extend(citation_errors)
    passed = not (
        fixture_errors
        or not legacy_passed
        or missing_mechanisms
        or triggered_forbidden
        or ontology_errors
        or citation_errors
        or budget_errors
    )
    return FormalEvalCaseResult(
        case_id=case_id,
        description=description,
        passed=passed,
        android_release=release if type(release) is int else None,
        source_class=source_class if isinstance(source_class, str) else None,
        detected_hint_ids=sorted(set(detected_hints)),
        detected_mechanism_ids=detected_mechanisms,
        required_mechanism_ids=required_mechanisms,
        forbidden_mechanism_ids=forbidden_mechanisms,
        missing_mechanisms=missing_mechanisms,
        triggered_forbidden_mechanisms=triggered_forbidden,
        required_citation_source_kinds=required_sources,
        verified_citation_source_counts=dict(sorted(citation_counts.items())),
        verified_evidence_ref_count=verified_refs,
        missing_citation_sources=missing_sources,
        ontology=ontology,
        ontology_errors=ontology_errors,
        fixture_errors=fixture_errors + citation_errors,
        budget_errors=budget_errors,
        wall_seconds=round(wall_seconds, 6),
        input_bytes=input_bytes,
        artifact_bytes=artifact_bytes,
        model_calls=model_calls,
        notes=semantic_notes,
    )


def run_formal_eval_directory(case_dir: str | Path, *, corpus_manifest_path: str | Path | None = None) -> FormalEvalAggregate:
    """Run a frozen formal corpus and return a qualification decision.

    This function deliberately returns a structured unqualified result instead
    of treating missing releases or non-production corpus provenance as a
    passing score.  Callers should use ``qualified`` as the release gate.
    """
    root = Path(case_dir)
    manifest_path = Path(corpus_manifest_path) if corpus_manifest_path else root / "corpus.json"
    try:
        raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raw_manifest = {}
        manifest_errors = [f"corpus manifest unreadable: {type(error).__name__}: {error}"]
    else:
        manifest_errors = []
    manifest, validation_errors = _validate_corpus_manifest(raw_manifest)
    manifest_errors.extend(validation_errors)
    default_manifest_path = root / "corpus.json"
    excluded_paths = {manifest_path.resolve(), default_manifest_path.resolve()}
    case_files = sorted(path for path in root.glob("*.json") if path.resolve() not in excluded_paths)
    if not case_files:
        manifest_errors.append("formal corpus contains no case fixtures")
    expected_hashes = manifest.get("caseSha256") if isinstance(manifest.get("caseSha256"), dict) else {}
    actual_names = {path.name for path in case_files}
    if set(expected_hashes) != actual_names:
        manifest_errors.append("case_sha256_file_set_mismatch")
    for path in case_files:
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if expected_hashes.get(path.name) != actual_hash:
            manifest_errors.append(f"case_sha256_mismatch:{path.name}")
    cases = [run_formal_eval_case(path, corpus_manifest=manifest) for path in case_files]
    coverage = {str(release): 0 for release in manifest.get("requiredAndroidReleases", [])}
    source_counts: dict[str, int] = {}
    for case in cases:
        if case.android_release is not None:
            coverage[str(case.android_release)] = coverage.get(str(case.android_release), 0) + 1
        if case.source_class:
            source_counts[case.source_class] = source_counts.get(case.source_class, 0) + 1
    per_release_metrics: dict[str, dict[str, float | int]] = {
        release: {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "verifiedEvidenceRefs": 0,
            "wallSeconds": 0.0,
            "inputBytes": 0,
            "artifactBytes": 0,
            "modelCalls": 0,
        }
        for release in coverage
    }
    for case in cases:
        if case.android_release is None:
            continue
        release = str(case.android_release)
        metrics = per_release_metrics.setdefault(release, {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "verifiedEvidenceRefs": 0,
            "wallSeconds": 0.0,
            "inputBytes": 0,
            "artifactBytes": 0,
            "modelCalls": 0,
        })
        metrics["total"] += 1
        metrics["passed" if case.passed else "failed"] += 1
        metrics["verifiedEvidenceRefs"] += case.verified_evidence_ref_count
        metrics["wallSeconds"] = round(float(metrics["wallSeconds"]) + case.wall_seconds, 6)
        metrics["inputBytes"] += case.input_bytes
        metrics["artifactBytes"] += case.artifact_bytes
        metrics["modelCalls"] += case.model_calls
    gate_failures: list[str] = []
    required_per_release = manifest.get("minimumCasesPerRelease")
    if type(required_per_release) is int:
        for release in manifest.get("requiredAndroidReleases", []):
            count = coverage.get(str(release), 0)
            if count < required_per_release:
                gate_failures.append(f"insufficient_release_coverage:{release}:{count}<{required_per_release}")
    unexpected_sources = sorted(source for source in source_counts if source != _PRODUCTION_SOURCE_CLASS)
    if unexpected_sources:
        gate_failures.append(f"non_production_source_classes:{','.join(unexpected_sources)}")
    if any(not case.passed for case in cases):
        gate_failures.append("case_expectations_failed")
    if any(case.model_calls != 0 for case in cases):
        gate_failures.append("nonzero_model_calls")
    qualified = not manifest_errors and not gate_failures
    totals = {
        "wallSeconds": round(sum(case.wall_seconds for case in cases), 6),
        "inputBytes": sum(case.input_bytes for case in cases),
        "artifactBytes": sum(case.artifact_bytes for case in cases),
        "modelCalls": sum(case.model_calls for case in cases),
    }
    passed = sum(1 for case in cases if case.passed)
    return FormalEvalAggregate(
        corpus_revision=manifest.get("corpusRevision") if isinstance(manifest.get("corpusRevision"), str) else None,
        total=len(cases),
        passed=passed,
        failed=len(cases) - passed,
        qualified=qualified,
        qualification_status="qualified" if qualified else "blocked",
        corpus_errors=manifest_errors,
        gate_failures=gate_failures,
        release_coverage=coverage,
        per_release_metrics={key: per_release_metrics[key] for key in sorted(per_release_metrics, key=int)},
        source_class_counts=dict(sorted(source_counts.items())),
        resource_totals=totals,
        cases=cases,
    )


__all__ = [
    "FORMAL_CASE_SCHEMA_VERSION",
    "FORMAL_CORPUS_SCHEMA_VERSION",
    "FormalEvalAggregate",
    "FormalEvalCaseResult",
    "collect_mechanism_ids",
    "run_formal_eval_case",
    "run_formal_eval_directory",
]
