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
from .redaction import REDACTION_RULESET_VERSION


# V2 remains the frozen first output-label contract.  V3 adds the
# fail-closed real-corpus admission metadata without redefining V2 semantics.
FORMAL_CASE_SCHEMA_VERSION = "anr-formal-eval-case-v3"
FORMAL_CORPUS_SCHEMA_VERSION = "anr-formal-eval-corpus-v3"
FORMAL_V2_CASE_SCHEMA_VERSION = "anr-formal-eval-case-v2"
FORMAL_V2_CORPUS_SCHEMA_VERSION = "anr-formal-eval-corpus-v2"
REAL_CORPUS_ADMISSION_SCHEMA_VERSION = "anr-formal-real-corpus-admission-v1"
FORMAL_ACCEPTANCE_CRITERIA_SCHEMA_VERSION = "anr-formal-acceptance-criteria-v1"

# These are the deterministic evaluator outputs that v2 can measure.  Other
# analysis outputs (for example final root cause, ranking and remediation) are
# deliberately declared outside the fully-labelled scope rather than inferred
# from a passing hint regression.
FORMAL_OUTPUT_DOMAINS = (
    "trigger",
    "blocker",
    "evidence_status",
    "citations",
    "contradictions",
    "high_confidence_violations",
)
_ALLOWED_LABEL_COVERAGE = frozenset({"complete", "partial", "unsupported"})
_PRODUCTION_SOURCE_CLASS = "licensed_deidentified"
_ALLOWED_SOURCE_CLASSES = frozenset({"synthetic", _PRODUCTION_SOURCE_CLASS})
_ALLOWED_DEIDENTIFICATION = frozenset({"not_applicable", "verified"})
_ALLOWED_CORPUS_SPLITS = frozenset({"development", "evaluation"})
_ALLOWED_MANUAL_LABEL_STATUSES = frozenset({"complete", "partial", "unsupported", "not_applicable"})
_ACCEPTANCE_MEASUREMENTS = frozenset({
    "citation_integrity", "high_confidence_violations", "adversarial_trigger_false_positives",
})

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
_BLOCKER_MECHANISM_IDS = frozenset({
    "main_thread_binder_reply_wait",
    "main_thread_queued_work_wait",
    "main_thread_synchronous_database",
    "main_thread_render_fence_wait",
    "thread_deadlock_cycle",
    "thread_deadlock_self",
    "lock_owner_blocked",
    "lock_owner_sleeping",
})


@dataclass(frozen=True)
class FormalEvalDimensionResult:
    """One labelled evaluator output domain, never an implied E2E accuracy."""

    coverage: str
    actual_ids: list[str]
    required_ids: list[str]
    forbidden_ids: list[str]
    allowed_ids: list[str]
    missing_required_ids: list[str]
    forbidden_observed_ids: list[str]
    unlabeled_observed_ids: list[str]
    unexpected_observed_ids: list[str]
    passed: bool | None
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FormalEvalCaseResult:
    case_id: str
    description: str
    passed: bool
    android_release: int | None
    source_class: str | None
    dataset_split: str | None
    incident_source_id: str | None
    device_source_id: str | None
    manual_label_status: str | None
    manual_label_missing_domains: list[str]
    manual_label_unsupported_domains: list[str]
    support_class_ids: list[str]
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
    output_dimensions: dict[str, FormalEvalDimensionResult]
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
    labeling_scope: dict[str, list[str]]
    output_dimension_metrics: dict[str, dict[str, int]]
    rule_coverage: dict[str, dict[str, int]]
    corpus_governance: dict[str, Any]
    acceptance_metrics: dict[str, Any]
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
            "labelingScope": self.labeling_scope,
            "outputDimensionMetrics": self.output_dimension_metrics,
            "ruleCoverage": self.rule_coverage,
            "corpusGovernance": self.corpus_governance,
            "acceptanceMetrics": self.acceptance_metrics,
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


def _validate_labeling_scope(data: Any, errors: list[str]) -> dict[str, list[str]]:
    if not isinstance(data, dict):
        errors.append("labelingScope must be an object")
        return {"fullyLabeledOutputDomains": [], "notFullyLabeledOutputDomains": []}
    complete = _string_list(
        data.get("fullyLabeledOutputDomains"),
        "labelingScope.fullyLabeledOutputDomains",
        errors,
    )
    incomplete = _string_list(
        data.get("notFullyLabeledOutputDomains"),
        "labelingScope.notFullyLabeledOutputDomains",
        errors,
    )
    known = set(FORMAL_OUTPUT_DOMAINS)
    unknown_complete = sorted(set(complete) - known)
    if unknown_complete:
        errors.append(f"labelingScope.fullyLabeledOutputDomains has unknown domains: {unknown_complete}")
    if set(complete) & set(incomplete):
        errors.append("labelingScope output domains must not appear in both complete and incomplete lists")
    declared = set(complete) | set(incomplete)
    missing = sorted(set(FORMAL_OUTPUT_DOMAINS) - declared)
    if missing:
        errors.append(f"labelingScope must declare every formal output domain: {missing}")
    if not incomplete:
        errors.append("labelingScope.notFullyLabeledOutputDomains must explicitly declare excluded outputs")
    # Non-formal dimensions are declarations only.  They cannot silently turn
    # into scored evaluator domains merely by being omitted from `complete`.
    return {
        "fullyLabeledOutputDomains": sorted(set(complete)),
        "notFullyLabeledOutputDomains": sorted(set(incomplete)),
    }


def _validate_rule_coverage(data: Any, errors: list[str]) -> dict[str, dict[str, list[str]]]:
    if not isinstance(data, dict) or not data:
        errors.append("ruleCoverage must be a non-empty object")
        return {}
    normalized: dict[str, dict[str, list[str]]] = {}
    for rule_id, declaration in data.items():
        if not isinstance(rule_id, str) or not rule_id.strip():
            errors.append("ruleCoverage keys must be non-empty rule IDs")
            continue
        if not isinstance(declaration, dict):
            errors.append(f"ruleCoverage.{rule_id} must be an object")
            continue
        positive = _string_list(
            declaration.get("positiveCaseIds"), f"ruleCoverage.{rule_id}.positiveCaseIds", errors, nonempty=True,
        )
        negative = _string_list(
            declaration.get("negativeCaseIds"), f"ruleCoverage.{rule_id}.negativeCaseIds", errors, nonempty=True,
        )
        if set(positive) & set(negative):
            errors.append(f"ruleCoverage.{rule_id} cannot reuse a case as both positive and negative")
        normalized[rule_id.strip()] = {"positiveCaseIds": positive, "negativeCaseIds": negative}
    return normalized


def _validate_output_label_spec(value: Any, domain: str, errors: list[str]) -> dict[str, Any]:
    path = f"expected.outputLabels.{domain}"
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return {"coverage": "unsupported", "requiredIds": [], "forbiddenIds": [], "allowedIds": []}
    coverage = value.get("coverage")
    if coverage not in _ALLOWED_LABEL_COVERAGE:
        errors.append(f"{path}.coverage must be one of {sorted(_ALLOWED_LABEL_COVERAGE)}")
        coverage = "unsupported"
    required = _string_list(value.get("requiredIds", []), f"{path}.requiredIds", errors)
    forbidden = _string_list(value.get("forbiddenIds", []), f"{path}.forbiddenIds", errors)
    allowed = _string_list(value.get("allowedIds", []), f"{path}.allowedIds", errors)
    if set(required) & set(forbidden):
        errors.append(f"{path} requiredIds and forbiddenIds must not overlap")
    if coverage == "complete":
        if set(required) - set(allowed):
            errors.append(f"{path}.allowedIds must include every required ID for complete coverage")
        if set(forbidden) & set(allowed):
            errors.append(f"{path}.allowedIds must exclude forbidden IDs for complete coverage")
    elif coverage == "partial":
        if allowed:
            errors.append(f"{path}.allowedIds is only valid for complete coverage")
    elif required or forbidden or allowed:
        errors.append(f"{path} unsupported coverage must not claim output IDs")
    return {
        "coverage": coverage,
        "requiredIds": required,
        "forbiddenIds": forbidden,
        "allowedIds": allowed,
    }


def _evaluate_output_dimension(actual_ids: Iterable[str], spec: dict[str, Any]) -> FormalEvalDimensionResult:
    actual = sorted({item for item in actual_ids if isinstance(item, str) and item})
    coverage = spec.get("coverage") if isinstance(spec.get("coverage"), str) else "unsupported"
    required = list(spec.get("requiredIds") or [])
    forbidden = list(spec.get("forbiddenIds") or [])
    allowed = list(spec.get("allowedIds") or [])
    actual_set = set(actual)
    missing = [item for item in required if item not in actual_set]
    forbidden_observed = [item for item in forbidden if item in actual_set]
    unexpected = sorted(actual_set - set(allowed)) if coverage == "complete" else []
    unlabeled = sorted(actual_set - set(required) - set(forbidden)) if coverage == "partial" else []
    notes: list[str] = []
    if missing:
        notes.append(f"missing required outputs: {missing}")
    if forbidden_observed:
        notes.append(f"forbidden outputs observed: {forbidden_observed}")
    if unexpected:
        notes.append(f"unlabelled outputs violate complete coverage: {unexpected}")
    if unlabeled:
        notes.append(f"unlabelled outputs are not counted as correct: {unlabeled}")
    passed: bool | None
    if coverage == "unsupported":
        passed = None
    elif coverage == "partial":
        # Partial labels can reject known bad output, but can never establish
        # correctness for a value that the corpus did not label.
        passed = None
    else:
        passed = not (missing or forbidden_observed or unexpected)
    return FormalEvalDimensionResult(
        coverage=coverage,
        actual_ids=actual,
        required_ids=required,
        forbidden_ids=forbidden,
        allowed_ids=allowed,
        missing_required_ids=missing,
        forbidden_observed_ids=forbidden_observed,
        unlabeled_observed_ids=unlabeled,
        unexpected_observed_ids=unexpected,
        passed=passed,
        notes=notes,
    )


def _dimension_errors(dimensions: dict[str, FormalEvalDimensionResult]) -> list[str]:
    errors: list[str] = []
    for name, dimension in dimensions.items():
        if dimension.coverage == "unsupported":
            continue
        if dimension.missing_required_ids:
            errors.append(f"{name}: missing required outputs {dimension.missing_required_ids}")
        if dimension.forbidden_observed_ids:
            errors.append(f"{name}: forbidden outputs observed {dimension.forbidden_observed_ids}")
        if dimension.unexpected_observed_ids:
            errors.append(f"{name}: outputs outside complete label set {dimension.unexpected_observed_ids}")
    return errors


def _output_dimension_metrics(cases: Iterable[FormalEvalCaseResult]) -> dict[str, dict[str, int]]:
    metrics: dict[str, dict[str, int]] = {
        domain: {
            "cases": 0,
            "completeLabelCases": 0,
            "partialLabelCases": 0,
            "unsupportedCases": 0,
            "completePassCases": 0,
            "completeFailCases": 0,
            "actualOutputs": 0,
            "missingRequiredOutputs": 0,
            "forbiddenObservedOutputs": 0,
            "unlabeledObservedOutputs": 0,
            "unexpectedObservedOutputs": 0,
        }
        for domain in FORMAL_OUTPUT_DOMAINS
    }
    for case in cases:
        for domain in FORMAL_OUTPUT_DOMAINS:
            dimension = case.output_dimensions.get(domain)
            if dimension is None:
                continue
            entry = metrics[domain]
            entry["cases"] += 1
            if dimension.coverage == "complete":
                entry["completeLabelCases"] += 1
                entry["completePassCases" if dimension.passed else "completeFailCases"] += 1
            elif dimension.coverage == "partial":
                entry["partialLabelCases"] += 1
            else:
                entry["unsupportedCases"] += 1
            entry["actualOutputs"] += len(dimension.actual_ids)
            entry["missingRequiredOutputs"] += len(dimension.missing_required_ids)
            entry["forbiddenObservedOutputs"] += len(dimension.forbidden_observed_ids)
            entry["unlabeledObservedOutputs"] += len(dimension.unlabeled_observed_ids)
            entry["unexpectedObservedOutputs"] += len(dimension.unexpected_observed_ids)
    return metrics


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


def _sha256(value: Any, path: str, errors: list[str]) -> str | None:
    if (not isinstance(value, str) or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)):
        errors.append(f"{path} must be a lowercase SHA-256")
        return None
    return value


def _validate_real_corpus_admission(data: Any, errors: list[str]) -> dict[str, Any]:
    """Validate the non-self-authenticating governance commitments for v3.

    These fields identify the external licence/redaction/manual-review records
    that a release reviewer must inspect.  Their presence does not certify a
    private log, authorize outbound transfer, or replace organisational review.
    """
    default = {
        "developmentSplits": [],
        "evaluationSplits": [],
        "isolationKeys": [],
        "requiredManualLabelDomains": [],
        "minimumSupportByClass": {},
        "statisticalThresholdsEnabled": False,
        "statisticalThresholds": {},
    }
    if not isinstance(data, dict):
        errors.append("realCorpusAdmission must be an object")
        return default
    if data.get("schemaVersion") != REAL_CORPUS_ADMISSION_SCHEMA_VERSION:
        errors.append(f"realCorpusAdmission.schemaVersion must equal {REAL_CORPUS_ADMISSION_SCHEMA_VERSION}")
    split_policy = data.get("splitIsolation")
    if not isinstance(split_policy, dict):
        errors.append("realCorpusAdmission.splitIsolation must be an object")
        split_policy = {}
    development = _string_list(
        split_policy.get("developmentSplits"),
        "realCorpusAdmission.splitIsolation.developmentSplits",
        errors,
        nonempty=True,
    )
    evaluation = _string_list(
        split_policy.get("evaluationSplits"),
        "realCorpusAdmission.splitIsolation.evaluationSplits",
        errors,
        nonempty=True,
    )
    for split in development + evaluation:
        if split not in _ALLOWED_CORPUS_SPLITS:
            errors.append(f"realCorpusAdmission split is unsupported: {split}")
    if set(development) & set(evaluation):
        errors.append("realCorpusAdmission development and evaluation splits must not overlap")
    isolation_keys = _string_list(
        split_policy.get("identityKeys"),
        "realCorpusAdmission.splitIsolation.identityKeys",
        errors,
        nonempty=True,
    )
    required_keys = {"incidentSourceId", "deviceSourceId"}
    if set(isolation_keys) != required_keys:
        errors.append("realCorpusAdmission.splitIsolation.identityKeys must be incidentSourceId and deviceSourceId")
    manual_domains = _string_list(
        data.get("requiredManualLabelDomains"),
        "realCorpusAdmission.requiredManualLabelDomains",
        errors,
        nonempty=True,
    )
    if set(manual_domains) != set(FORMAL_OUTPUT_DOMAINS):
        errors.append("realCorpusAdmission.requiredManualLabelDomains must include exactly every formal output domain")
    support = data.get("minimumSupportByClass")
    normalized_support: dict[str, int] = {}
    if not isinstance(support, dict) or not support:
        errors.append("realCorpusAdmission.minimumSupportByClass must be a non-empty object")
    else:
        for class_id, minimum in support.items():
            if not isinstance(class_id, str) or not class_id.strip():
                errors.append("realCorpusAdmission.minimumSupportByClass keys must be non-empty")
                continue
            normalized_minimum = _strict_int(
                minimum,
                f"realCorpusAdmission.minimumSupportByClass.{class_id}",
                errors,
                minimum=1,
            )
            if normalized_minimum is not None:
                normalized_support[class_id.strip()] = normalized_minimum
    enabled = data.get("statisticalThresholdsEnabled")
    if type(enabled) is not bool:
        errors.append("realCorpusAdmission.statisticalThresholdsEnabled must be a boolean")
        enabled = False
    thresholds = data.get("statisticalThresholds")
    normalized_thresholds: dict[str, float] = {}
    if not isinstance(thresholds, dict):
        errors.append("realCorpusAdmission.statisticalThresholds must be an object")
    else:
        for metric in ("macroF1", "top3Accuracy"):
            value = thresholds.get(metric)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0.0 <= float(value) <= 1.0:
                errors.append(f"realCorpusAdmission.statisticalThresholds.{metric} must be between 0 and 1")
            else:
                normalized_thresholds[metric] = float(value)
    return {
        "developmentSplits": sorted(set(development)),
        "evaluationSplits": sorted(set(evaluation)),
        "isolationKeys": sorted(set(isolation_keys)),
        "requiredManualLabelDomains": sorted(set(manual_domains)),
        "minimumSupportByClass": dict(sorted(normalized_support.items())),
        "statisticalThresholdsEnabled": enabled,
        "statisticalThresholds": normalized_thresholds,
    }


def _validate_acceptance_criteria(data: Any, errors: list[str]) -> dict[str, Any]:
    """Validate measurable v3 acceptance objectives without inventing model scores."""
    default = {
        "requiredMeasurements": [],
        "minimumCitationIntegrity": 1.0,
        "maximumHighConfidenceViolations": 0,
        "adversarialTriggerRuleIds": [],
    }
    if not isinstance(data, dict):
        errors.append("acceptanceCriteria must be an object")
        return default
    if data.get("schemaVersion") != FORMAL_ACCEPTANCE_CRITERIA_SCHEMA_VERSION:
        errors.append(
            f"acceptanceCriteria.schemaVersion must equal {FORMAL_ACCEPTANCE_CRITERIA_SCHEMA_VERSION}"
        )
    required = _string_list(
        data.get("requiredMeasurements"), "acceptanceCriteria.requiredMeasurements", errors,
    )
    unknown = sorted(set(required) - _ACCEPTANCE_MEASUREMENTS)
    if unknown:
        errors.append(f"acceptanceCriteria.requiredMeasurements has unsupported values: {unknown}")
    citation_target = data.get("minimumCitationIntegrity")
    if (not isinstance(citation_target, (int, float)) or isinstance(citation_target, bool)
            or float(citation_target) != 1.0):
        errors.append("acceptanceCriteria.minimumCitationIntegrity must be exactly 1.0")
        citation_target = 1.0
    high_confidence_limit = _strict_int(
        data.get("maximumHighConfidenceViolations"),
        "acceptanceCriteria.maximumHighConfidenceViolations",
        errors,
        minimum=0,
    )
    if high_confidence_limit not in (0, None):
        errors.append("acceptanceCriteria.maximumHighConfidenceViolations must be exactly 0")
        high_confidence_limit = 0
    trigger_rules = _string_list(
        data.get("adversarialTriggerRuleIds"),
        "acceptanceCriteria.adversarialTriggerRuleIds",
        errors,
    )
    invalid_trigger_rules = sorted(rule for rule in trigger_rules if not rule.startswith("trigger:"))
    if invalid_trigger_rules:
        errors.append(f"acceptanceCriteria.adversarialTriggerRuleIds must use trigger:* rules: {invalid_trigger_rules}")
    return {
        "requiredMeasurements": sorted(set(required)),
        "minimumCitationIntegrity": float(citation_target),
        "maximumHighConfidenceViolations": high_confidence_limit if high_confidence_limit is not None else 0,
        "adversarialTriggerRuleIds": sorted(set(trigger_rules)),
    }


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
    labeling_scope = _validate_labeling_scope(data.get("labelingScope"), errors)
    rule_coverage = _validate_rule_coverage(data.get("ruleCoverage"), errors)
    real_corpus_admission = _validate_real_corpus_admission(data.get("realCorpusAdmission"), errors)
    acceptance_criteria = _validate_acceptance_criteria(data.get("acceptanceCriteria"), errors)
    unknown_adversarial_rules = sorted(
        set(acceptance_criteria["adversarialTriggerRuleIds"]) - set(rule_coverage)
    )
    if unknown_adversarial_rules:
        errors.append(f"acceptanceCriteria references undeclared trigger rules: {unknown_adversarial_rules}")
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
        "labelingScope": labeling_scope,
        "ruleCoverage": rule_coverage,
        "realCorpusAdmission": real_corpus_admission,
        "acceptanceCriteria": acceptance_criteria,
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
    split = _nonempty_string(corpus.get("split"), "corpus.split", errors)
    admission = manifest.get("realCorpusAdmission") if isinstance(manifest.get("realCorpusAdmission"), dict) else {}
    allowed_splits = set(admission.get("developmentSplits", [])) | set(admission.get("evaluationSplits", []))
    if split is not None and (split not in _ALLOWED_CORPUS_SPLITS or split not in allowed_splits):
        errors.append("corpus.split is not admitted by realCorpusAdmission.splitIsolation")
    source_class = _nonempty_string(corpus.get("sourceClass"), "corpus.sourceClass", errors)
    if source_class is not None and source_class not in _ALLOWED_SOURCE_CLASSES:
        errors.append("corpus.sourceClass is unsupported; raw/private samples are not accepted by formal evaluation")
    _nonempty_string(corpus.get("sourceLicense"), "corpus.sourceLicense", errors)
    deidentification = _nonempty_string(corpus.get("deidentificationStatus"), "corpus.deidentificationStatus", errors)
    if deidentification is not None and deidentification not in _ALLOWED_DEIDENTIFICATION:
        errors.append("corpus.deidentificationStatus is unsupported")
    release = _strict_int(corpus.get("androidRelease"), "corpus.androidRelease", errors, minimum=1)
    device = corpus.get("device")
    if not isinstance(device, dict):
        errors.append("corpus.device must be an object")
    else:
        _nonempty_string(device.get("model"), "corpus.device.model", errors)
        _nonempty_string(device.get("buildFingerprint"), "corpus.device.buildFingerprint", errors)
    sample_identity = corpus.get("sampleIdentity")
    incident_source_id: str | None = None
    device_source_id: str | None = None
    if not isinstance(sample_identity, dict):
        errors.append("corpus.sampleIdentity must be an object with opaque source aliases")
    else:
        incident_source_id = _nonempty_string(
            sample_identity.get("incidentSourceId"), "corpus.sampleIdentity.incidentSourceId", errors,
        )
        device_source_id = _nonempty_string(
            sample_identity.get("deviceSourceId"), "corpus.sampleIdentity.deviceSourceId", errors,
        )
    support_class_ids = _string_list(
        corpus.get("supportClassIds"), "corpus.supportClassIds", errors, nonempty=True,
    )
    manual = corpus.get("manualAnnotation")
    manual_status: str | None = None
    manual_missing: list[str] = []
    manual_unsupported: list[str] = []
    if not isinstance(manual, dict):
        errors.append("corpus.manualAnnotation must be an object")
    else:
        manual_status = _nonempty_string(manual.get("status"), "corpus.manualAnnotation.status", errors)
        if manual_status is not None and manual_status not in _ALLOWED_MANUAL_LABEL_STATUSES:
            errors.append("corpus.manualAnnotation.status is unsupported")
        manual_missing = _string_list(
            manual.get("missingOutputDomains", []), "corpus.manualAnnotation.missingOutputDomains", errors,
        )
        manual_unsupported = _string_list(
            manual.get("unsupportedOutputDomains", []), "corpus.manualAnnotation.unsupportedOutputDomains", errors,
        )
        labeled_domains = _string_list(
            manual.get("labeledOutputDomains", []), "corpus.manualAnnotation.labeledOutputDomains", errors,
        )
        if source_class == _PRODUCTION_SOURCE_CLASS:
            if manual_status != "complete":
                errors.append("licensed_deidentified cases require manualAnnotation.status=complete")
            _nonempty_string(manual.get("labelSetRevision"), "corpus.manualAnnotation.labelSetRevision", errors)
            _string_list(manual.get("annotatorIds"), "corpus.manualAnnotation.annotatorIds", errors, nonempty=True)
            _string_list(manual.get("reviewerIds"), "corpus.manualAnnotation.reviewerIds", errors, nonempty=True)
            required_domains = set(admission.get("requiredManualLabelDomains", []))
            missing_domains = sorted(required_domains - set(labeled_domains))
            if missing_domains:
                errors.append(f"licensed_deidentified manual labels missing required domains: {missing_domains}")
            forbidden_incomplete = sorted(required_domains & (set(manual_missing) | set(manual_unsupported)))
            if forbidden_incomplete:
                errors.append(f"licensed_deidentified manual labels mark required domains incomplete: {forbidden_incomplete}")
        elif source_class == "synthetic":
            if deidentification != "not_applicable":
                errors.append("synthetic cases require deidentificationStatus=not_applicable")
            if manual_status != "not_applicable":
                errors.append("synthetic cases require manualAnnotation.status=not_applicable")
        else:
            # Avoid treating an invalid class as a possibly safe form of private
            # input.  The source-class error above stops evaluator execution.
            pass
    if source_class == _PRODUCTION_SOURCE_CLASS:
        if deidentification != "verified":
            errors.append("licensed_deidentified cases require deidentificationStatus=verified")
        licence_record = corpus.get("sourceLicenseRecord")
        if not isinstance(licence_record, dict):
            errors.append("licensed_deidentified cases require corpus.sourceLicenseRecord")
        else:
            _nonempty_string(licence_record.get("recordId"), "corpus.sourceLicenseRecord.recordId", errors)
            _nonempty_string(licence_record.get("approvedBy"), "corpus.sourceLicenseRecord.approvedBy", errors)
            _sha256(licence_record.get("evidenceSha256"), "corpus.sourceLicenseRecord.evidenceSha256", errors)
        deidentification_record = corpus.get("deidentificationValidation")
        if not isinstance(deidentification_record, dict):
            errors.append("licensed_deidentified cases require corpus.deidentificationValidation")
        else:
            if deidentification_record.get("rulesetVersion") != REDACTION_RULESET_VERSION:
                errors.append(f"corpus.deidentificationValidation.rulesetVersion must equal {REDACTION_RULESET_VERSION}")
            _nonempty_string(deidentification_record.get("validationId"), "corpus.deidentificationValidation.validationId", errors)
            _nonempty_string(deidentification_record.get("validatorId"), "corpus.deidentificationValidation.validatorId", errors)
            _sha256(deidentification_record.get("viewCommitment"), "corpus.deidentificationValidation.viewCommitment", errors)
    package = case.get("package")
    if not isinstance(package, dict):
        errors.append("package must be an object")
    elif release is not None and package.get("android_release") != release:
        errors.append("package.android_release must equal corpus.androidRelease")
    return {
        "androidRelease": release,
        "sourceClass": source_class,
        "datasetSplit": split,
        "incidentSourceId": incident_source_id,
        "deviceSourceId": device_source_id,
        "manualLabelStatus": manual_status,
        "manualLabelMissingDomains": sorted(set(manual_missing)),
        "manualLabelUnsupportedDomains": sorted(set(manual_unsupported)),
        "supportClassIds": sorted(set(support_class_ids)),
    }, errors


def _expected_formal_fields(
    case: dict[str, Any],
    manifest: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    expected = case.get("expected")
    errors: list[str] = []
    if not isinstance(expected, dict):
        return {}, ["expected must be an object"]
    mechanisms = _string_list(expected.get("requiredMechanismIds"), "expected.requiredMechanismIds", errors, nonempty=True)
    forbidden = _string_list(expected.get("forbiddenMechanismIds", []), "expected.forbiddenMechanismIds", errors)
    sources = _string_list(expected.get("requiredCitationSourceKinds"), "expected.requiredCitationSourceKinds", errors, nonempty=True)
    minimum = _strict_int(expected.get("minVerifiedEvidenceRefs"), "expected.minVerifiedEvidenceRefs", errors, minimum=1)
    ontology = expected.get("timeoutOntology")
    canonical_trigger: str | None = None
    evidence_status: str | None = None
    if not isinstance(ontology, dict):
        errors.append("expected.timeoutOntology must be an object")
        ontology = {}
    else:
        canonical_trigger = _nonempty_string(ontology.get("canonicalTrigger"), "expected.timeoutOntology.canonicalTrigger", errors)
        evidence_status = _nonempty_string(ontology.get("evidenceStatus"), "expected.timeoutOntology.evidenceStatus", errors)
        _string_list(ontology.get("requiredProvenanceSymbols"), "expected.timeoutOntology.requiredProvenanceSymbols", errors, nonempty=True)
    output_labels = expected.get("outputLabels")
    if not isinstance(output_labels, dict):
        errors.append("expected.outputLabels must be an object")
        output_labels = {}
    unknown_domains = sorted(set(output_labels) - set(FORMAL_OUTPUT_DOMAINS))
    if unknown_domains:
        errors.append(f"expected.outputLabels has unknown domains: {unknown_domains}")
    labels = {
        domain: _validate_output_label_spec(output_labels.get(domain), domain, errors)
        for domain in FORMAL_OUTPUT_DOMAINS
    }
    manifest_scope = manifest.get("labelingScope") if isinstance(manifest.get("labelingScope"), dict) else {}
    complete_domains = set(manifest_scope.get("fullyLabeledOutputDomains") or [])
    for domain, label in labels.items():
        if domain in complete_domains and label["coverage"] != "complete":
            errors.append(f"expected.outputLabels.{domain}.coverage must be complete under the frozen labeling scope")
    trigger_label = labels["trigger"]
    if isinstance(ontology, dict) and canonical_trigger is not None and canonical_trigger not in trigger_label["requiredIds"]:
        errors.append("expected.outputLabels.trigger.requiredIds must include timeoutOntology.canonicalTrigger")
    status_label = labels["evidence_status"]
    if isinstance(ontology, dict) and evidence_status is not None and evidence_status not in status_label["requiredIds"]:
        errors.append("expected.outputLabels.evidence_status.requiredIds must include timeoutOntology.evidenceStatus")
    citation_label = labels["citations"]
    if set(sources) - set(citation_label["requiredIds"]):
        errors.append("expected.outputLabels.citations.requiredIds must include requiredCitationSourceKinds")
    return {
        "requiredMechanismIds": mechanisms,
        "forbiddenMechanismIds": forbidden,
        "requiredCitationSourceKinds": sources,
        "minVerifiedEvidenceRefs": minimum,
        "timeoutOntology": ontology,
        "outputLabels": labels,
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


def _collect_blocker_ids(detected_mechanisms: Iterable[str]) -> list[str]:
    return sorted(set(detected_mechanisms) & _BLOCKER_MECHANISM_IDS)


def _collect_contradiction_ids(groups: Iterable[dict[str, Any]]) -> list[str]:
    contradictions: set[str] = set()
    for group in groups:
        lifecycle = group.get("inputLifecycle")
        if not isinstance(lifecycle, dict):
            continue
        gaps = lifecycle.get("gaps")
        if isinstance(gaps, list):
            if "conflicting_original_target" in gaps:
                contradictions.add("input.conflicting_original_target")
            if "conflicting_wms_blame_target" in gaps:
                contradictions.add("input.conflicting_wms_blame_target")
        for event in lifecycle.get("events", []) if isinstance(lifecycle.get("events"), list) else []:
            if isinstance(event, dict) and event.get("associationStatus") == "wrong_identity":
                contradictions.add("input.wrong_identity_event")
    return sorted(contradictions)


def _collect_high_confidence_violation_ids(
    groups: Iterable[dict[str, Any]],
    timeout_ontology: dict[str, Any],
) -> list[str]:
    """Report deterministic conservative-contract violations, not model accuracy."""
    violations: set[str] = set()
    for group in groups:
        for key, label in (("inputLifecycle", "input_lifecycle"), ("dumpQuality", "dump_quality")):
            payload = group.get(key)
            if not isinstance(payload, dict):
                continue
            if payload.get("eligibleForHighCausalConfidence") is True:
                violations.add(f"{label}.unexpected_high_confidence_eligibility")
            if (payload.get("finalJudgment") is not False
                    or payload.get("notRootCauseYet") is not True
                    or payload.get("requiresHumanConfirmation") is not True):
                violations.add(f"{label}.conservative_field_violation")
    if timeout_ontology:
        if (timeout_ontology.get("finalJudgment") is not False
                or timeout_ontology.get("notRootCauseYet") is not True
                or timeout_ontology.get("requiresHumanConfirmation") is not True):
            violations.add("timeout_ontology.conservative_field_violation")
    return sorted(violations)


def _rule_coverage_results(
    declarations: dict[str, dict[str, list[str]]],
    cases: Iterable[FormalEvalCaseResult],
) -> tuple[dict[str, dict[str, int]], list[str]]:
    case_by_id = {case.case_id: case for case in cases}
    results: dict[str, dict[str, int]] = {}
    failures: list[str] = []
    for rule_id, declaration in sorted(declarations.items()):
        positive_ids = declaration.get("positiveCaseIds", [])
        negative_ids = declaration.get("negativeCaseIds", [])
        positive_observed = positive_clean = negative_clean = 0
        for case_id in positive_ids:
            case = case_by_id.get(case_id)
            if case is None:
                failures.append(f"rule_coverage_unknown_positive_case:{rule_id}:{case_id}")
                continue
            observed = rule_id in case.detected_mechanism_ids
            positive_observed += int(observed)
            positive_clean += int(observed and case.passed)
            if not observed or not case.passed:
                failures.append(f"rule_coverage_positive_miss:{rule_id}:{case_id}")
        for case_id in negative_ids:
            case = case_by_id.get(case_id)
            if case is None:
                failures.append(f"rule_coverage_unknown_negative_case:{rule_id}:{case_id}")
                continue
            clean = rule_id not in case.detected_mechanism_ids and case.passed
            negative_clean += int(clean)
            if not clean:
                failures.append(f"rule_coverage_negative_failure:{rule_id}:{case_id}")
        results[rule_id] = {
            "positiveCases": len(positive_ids),
            "positiveObserved": positive_observed,
            "positivePassed": positive_clean,
            "negativeCases": len(negative_ids),
            "negativePassed": negative_clean,
        }
    return results, failures


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
            dataset_split=None,
            incident_source_id=None,
            device_source_id=None,
            manual_label_status=None,
            manual_label_missing_domains=[],
            manual_label_unsupported_domains=[],
            support_class_ids=[],
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
            output_dimensions={},
        )
    if not isinstance(case, dict):
        case = {}
    metadata, fixture_errors = _validate_case_metadata(case, corpus_manifest)
    expected, expected_errors = _expected_formal_fields(case, corpus_manifest)
    fixture_errors.extend(expected_errors)
    case_id = case.get("id") if isinstance(case.get("id"), str) else path.stem
    description = case.get("description") if isinstance(case.get("description"), str) else ""
    package = case.get("package") if isinstance(case.get("package"), dict) else {}
    release = metadata.get("androidRelease")
    source_class = metadata.get("sourceClass")
    dataset_split = metadata.get("datasetSplit")
    incident_source_id = metadata.get("incidentSourceId")
    device_source_id = metadata.get("deviceSourceId")
    manual_label_status = metadata.get("manualLabelStatus")
    manual_label_missing_domains = metadata.get("manualLabelMissingDomains", [])
    manual_label_unsupported_domains = metadata.get("manualLabelUnsupportedDomains", [])
    support_class_ids = metadata.get("supportClassIds", [])
    budget = corpus_manifest.get("executionBudget") or {}
    started = perf_counter()
    detected_hints: list[str] = []
    detected_mechanisms: list[str] = []
    groups: list[dict[str, Any]] = []
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
            groups = [group for group in result.groups if isinstance(group, dict)]
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
    actual_outputs = {
        "trigger": [ontology["canonicalTrigger"]] if isinstance(ontology.get("canonicalTrigger"), str) else [],
        "blocker": _collect_blocker_ids(detected_mechanisms),
        "evidence_status": [ontology["evidenceStatus"]] if isinstance(ontology.get("evidenceStatus"), str) else [],
        "citations": sorted(citation_counts),
        "contradictions": _collect_contradiction_ids(groups),
        "high_confidence_violations": _collect_high_confidence_violation_ids(groups, ontology),
    }
    label_specs = expected.get("outputLabels") if isinstance(expected.get("outputLabels"), dict) else {}
    output_dimensions = {
        domain: _evaluate_output_dimension(actual_outputs[domain], label_specs.get(domain, {}))
        for domain in FORMAL_OUTPUT_DOMAINS
    }
    dimension_errors = _dimension_errors(output_dimensions)
    if not legacy_passed:
        semantic_notes.append("legacy hint expectations failed")
    if missing_mechanisms:
        semantic_notes.append(f"missing mechanisms: {missing_mechanisms}")
    if triggered_forbidden:
        semantic_notes.append(f"forbidden mechanisms observed: {triggered_forbidden}")
    semantic_notes.extend(ontology_errors)
    semantic_notes.extend(citation_errors)
    semantic_notes.extend(dimension_errors)
    for domain, dimension in output_dimensions.items():
        if dimension.coverage == "partial" and dimension.unlabeled_observed_ids:
            semantic_notes.append(f"{domain}: unlabelled outputs not counted as correct {dimension.unlabeled_observed_ids}")
    passed = not (
        fixture_errors
        or not legacy_passed
        or missing_mechanisms
        or triggered_forbidden
        or ontology_errors
        or citation_errors
        or dimension_errors
        or budget_errors
    )
    return FormalEvalCaseResult(
        case_id=case_id,
        description=description,
        passed=passed,
        android_release=release if type(release) is int else None,
        source_class=source_class if isinstance(source_class, str) else None,
        dataset_split=dataset_split if isinstance(dataset_split, str) else None,
        incident_source_id=incident_source_id if isinstance(incident_source_id, str) else None,
        device_source_id=device_source_id if isinstance(device_source_id, str) else None,
        manual_label_status=manual_label_status if isinstance(manual_label_status, str) else None,
        manual_label_missing_domains=list(manual_label_missing_domains),
        manual_label_unsupported_domains=list(manual_label_unsupported_domains),
        support_class_ids=list(support_class_ids),
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
        output_dimensions=output_dimensions,
        model_calls=model_calls,
        notes=semantic_notes,
    )


def _build_corpus_governance_report(
    manifest: dict[str, Any],
    cases: Iterable[FormalEvalCaseResult],
) -> tuple[dict[str, Any], list[str]]:
    """Report and gate corpus governance without exposing raw sample content."""
    admission = manifest.get("realCorpusAdmission") if isinstance(manifest.get("realCorpusAdmission"), dict) else {}
    development_splits = set(admission.get("developmentSplits", []))
    evaluation_splits = set(admission.get("evaluationSplits", []))
    minimum_support = admission.get("minimumSupportByClass") if isinstance(admission.get("minimumSupportByClass"), dict) else {}
    split_counts: dict[str, int] = {}
    source_counts_by_split: dict[str, dict[str, int]] = {}
    identities: dict[str, dict[str, set[str]]] = {
        "incidentSourceId": {"development": set(), "evaluation": set()},
        "deviceSourceId": {"development": set(), "evaluation": set()},
    }
    support_counts: dict[str, int] = {}
    missing_labels: list[dict[str, Any]] = []
    unsupported_labels: list[dict[str, Any]] = []
    unsupported_output_domains: list[dict[str, str]] = []
    case_list = list(cases)
    for case in case_list:
        split = case.dataset_split or "unknown"
        split_counts[split] = split_counts.get(split, 0) + 1
        source = case.source_class or "unknown"
        split_source_counts = source_counts_by_split.setdefault(split, {})
        split_source_counts[source] = split_source_counts.get(source, 0) + 1
        if split in development_splits | evaluation_splits:
            bucket = "development" if split in development_splits else "evaluation"
            if case.incident_source_id:
                identities["incidentSourceId"][bucket].add(case.incident_source_id)
            if case.device_source_id:
                identities["deviceSourceId"][bucket].add(case.device_source_id)
        if split in evaluation_splits:
            for class_id in case.support_class_ids:
                support_counts[class_id] = support_counts.get(class_id, 0) + 1
        if case.manual_label_missing_domains:
            missing_labels.append({"caseId": case.case_id, "domains": case.manual_label_missing_domains})
        if case.manual_label_unsupported_domains:
            unsupported_labels.append({"caseId": case.case_id, "domains": case.manual_label_unsupported_domains})
        for domain, dimension in case.output_dimensions.items():
            if dimension.coverage == "unsupported":
                unsupported_output_domains.append({"caseId": case.case_id, "domain": domain})
    failures: list[str] = []
    split_leakage: dict[str, list[str]] = {}
    for key, buckets in identities.items():
        overlap = sorted(buckets["development"] & buckets["evaluation"])
        split_leakage[key] = overlap
        for identity in overlap:
            failures.append(f"development_evaluation_leakage:{key}:{identity}")
    support_report: dict[str, dict[str, int]] = {}
    for class_id, minimum in sorted(minimum_support.items()):
        observed = support_counts.get(class_id, 0)
        support_report[class_id] = {"observed": observed, "minimum": minimum}
        if observed < minimum:
            failures.append(f"insufficient_class_support:{class_id}:{observed}<{minimum}")
    incomplete_manual_labels = bool(missing_labels or unsupported_labels)
    if incomplete_manual_labels:
        failures.append("manual_label_coverage_incomplete")
    prerequisites_ready = not failures
    thresholds_enabled = bool(admission.get("statisticalThresholdsEnabled"))
    if thresholds_enabled:
        # The current evaluator deliberately has no model-result scoring engine
        # for macro-F1/top-3.  Expose enabled intent but fail closed rather than
        # treating deterministic fixture passing as those statistics.
        failures.append("statistical_threshold_metrics_unsupported")
        threshold_status = "blocked_metric_execution_not_implemented" if prerequisites_ready else "blocked_prerequisites"
    else:
        threshold_status = "disabled_by_manifest"
    report = {
        "schemaVersion": REAL_CORPUS_ADMISSION_SCHEMA_VERSION,
        "sampleCountsBySplit": dict(sorted(split_counts.items())),
        "sampleCountsBySourceClassAndSplit": {
            split: dict(sorted(counts.items())) for split, counts in sorted(source_counts_by_split.items())
        },
        "supportByClass": support_report,
        "splitLeakage": split_leakage,
        "missingManualLabels": missing_labels,
        "unsupportedManualLabels": unsupported_labels,
        "unsupportedOutputDomains": unsupported_output_domains,
        "statisticalThresholds": {
            "configuredEnabled": thresholds_enabled,
            "status": threshold_status,
            "thresholds": admission.get("statisticalThresholds", {}),
        },
    }
    return report, failures


def _build_acceptance_metrics(
    manifest: dict[str, Any],
    evaluation_cases: Iterable[FormalEvalCaseResult],
) -> tuple[dict[str, Any], list[str]]:
    """Compute only deterministic acceptance measurements over evaluation data.

    ``N/A`` is deliberately distinct from zero or passing.  Model macro-F1 and
    top-3 remain goals until a frozen, independently labelled corpus and a
    ranking metric executor exist; this deterministic evaluator never infers
    them from hint cases.
    """
    criteria = manifest.get("acceptanceCriteria") if isinstance(manifest.get("acceptanceCriteria"), dict) else {}
    required = set(criteria.get("requiredMeasurements", []))
    cases = list(evaluation_cases)
    failures: list[str] = []

    citation_cases = [
        case for case in cases
        if case.required_citation_source_kinds
        and case.output_dimensions.get("citations") is not None
        and case.output_dimensions["citations"].coverage == "complete"
    ]
    citation_complete = [
        case for case in citation_cases
        if not case.missing_citation_sources
        and case.verified_evidence_ref_count > 0
        and case.output_dimensions["citations"].passed is True
    ]
    if citation_cases:
        citation_rate = len(citation_complete) / len(citation_cases)
        citation_status = "measured"
    else:
        citation_rate = None
        citation_status = "N/A"
    citation_metric = {
        "status": citation_status,
        "supportedCases": len(citation_cases),
        "completeCases": len(citation_complete),
        "integrityRate": citation_rate,
        "targetMinimum": criteria.get("minimumCitationIntegrity", 1.0),
    }

    high_confidence_cases = [
        case for case in cases
        if case.output_dimensions.get("high_confidence_violations") is not None
        and case.output_dimensions["high_confidence_violations"].coverage == "complete"
    ]
    high_confidence_violations = [
        violation
        for case in high_confidence_cases
        for violation in case.output_dimensions["high_confidence_violations"].actual_ids
    ]
    high_confidence_metric = {
        "status": "measured" if high_confidence_cases else "N/A",
        "supportedCases": len(high_confidence_cases),
        "violationCount": len(high_confidence_violations),
        "violationIds": sorted(set(high_confidence_violations)),
        "targetMaximum": criteria.get("maximumHighConfidenceViolations", 0),
    }

    cases_by_id = {case.case_id: case for case in cases}
    declarations = manifest.get("ruleCoverage") if isinstance(manifest.get("ruleCoverage"), dict) else {}
    adversarial_rules: dict[str, dict[str, Any]] = {}
    total_negative_cases = total_false_positives = 0
    for rule_id in criteria.get("adversarialTriggerRuleIds", []):
        declaration = declarations.get(rule_id) if isinstance(declarations.get(rule_id), dict) else {}
        negative_ids = declaration.get("negativeCaseIds") if isinstance(declaration.get("negativeCaseIds"), list) else []
        negative_cases = [cases_by_id[case_id] for case_id in negative_ids if case_id in cases_by_id]
        false_positive_ids = [
            case.case_id for case in negative_cases if rule_id in case.detected_mechanism_ids
        ]
        adversarial_rules[rule_id] = {
            "status": "measured" if negative_cases else "N/A",
            "negativeCases": len(negative_cases),
            "falsePositiveCases": len(false_positive_ids),
            "falsePositiveCaseIds": false_positive_ids,
        }
        total_negative_cases += len(negative_cases)
        total_false_positives += len(false_positive_ids)
    adversarial_metric = {
        "status": "measured" if total_negative_cases else "N/A",
        "negativeCases": total_negative_cases,
        "falsePositiveCases": total_false_positives,
        "rules": adversarial_rules,
        "targetMaximum": 0,
    }

    metrics = {
        "citationIntegrity": citation_metric,
        "highConfidenceViolations": high_confidence_metric,
        "adversarialTriggerFalsePositives": adversarial_metric,
        "modelQualityGoals": {
            "macroF1": {
                "status": "N/A",
                "targetMinimum": (manifest.get("realCorpusAdmission") or {}).get("statisticalThresholds", {}).get("macroF1"),
                "reason": "frozen_licensed_corpus_and_model_metric_executor_required",
            },
            "top3Accuracy": {
                "status": "N/A",
                "targetMinimum": (manifest.get("realCorpusAdmission") or {}).get("statisticalThresholds", {}).get("top3Accuracy"),
                "reason": "frozen_licensed_corpus_and_model_metric_executor_required",
            },
        },
    }

    for category, metric in (
        ("citation_integrity", citation_metric),
        ("high_confidence_violations", high_confidence_metric),
        ("adversarial_trigger_false_positives", adversarial_metric),
    ):
        if metric["status"] == "N/A":
            if category in required:
                failures.append(f"acceptance_measurement_not_available:{category}")
            continue
        if category == "citation_integrity" and metric["integrityRate"] < metric["targetMinimum"]:
            failures.append(
                f"citation_integrity_below_target:{metric['integrityRate']:.6f}<{metric['targetMinimum']:.6f}"
            )
        elif category == "high_confidence_violations" and metric["violationCount"] > metric["targetMaximum"]:
            failures.append(
                f"high_confidence_violations_above_max:{metric['violationCount']}>{metric['targetMaximum']}"
            )
        elif category == "adversarial_trigger_false_positives" and metric["falsePositiveCases"] > metric["targetMaximum"]:
            failures.append(
                f"adversarial_trigger_false_positives:{metric['falsePositiveCases']}>{metric['targetMaximum']}"
            )
    return metrics, failures


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
    evaluation_splits = set((manifest.get("realCorpusAdmission") or {}).get("evaluationSplits", []))
    evaluation_cases = [case for case in cases if case.dataset_split in evaluation_splits]
    coverage = {str(release): 0 for release in manifest.get("requiredAndroidReleases", [])}
    source_counts: dict[str, int] = {}
    for case in evaluation_cases:
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
    for case in evaluation_cases:
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
    corpus_governance, governance_failures = _build_corpus_governance_report(manifest, cases)
    gate_failures.extend(governance_failures)
    acceptance_metrics, acceptance_failures = _build_acceptance_metrics(manifest, evaluation_cases)
    gate_failures.extend(acceptance_failures)
    if not evaluation_cases:
        gate_failures.append("formal_corpus_has_no_evaluation_cases")
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
    labeling_scope = manifest.get("labelingScope") if isinstance(manifest.get("labelingScope"), dict) else {
        "fullyLabeledOutputDomains": [], "notFullyLabeledOutputDomains": [],
    }
    output_dimension_metrics = _output_dimension_metrics(evaluation_cases)
    for domain in labeling_scope.get("fullyLabeledOutputDomains", []):
        metrics = output_dimension_metrics.get(domain)
        if metrics is None:
            continue
        if metrics["completeLabelCases"] != len(evaluation_cases):
            gate_failures.append(
                f"incomplete_label_coverage:{domain}:{metrics['completeLabelCases']}<{len(evaluation_cases)}"
            )
        if metrics["completeFailCases"]:
            gate_failures.append(f"labelled_output_expectations_failed:{domain}")
    for domain in labeling_scope.get("notFullyLabeledOutputDomains", []):
        if domain in FORMAL_OUTPUT_DOMAINS:
            gate_failures.append(f"output_domain_not_fully_labeled:{domain}")
    rule_coverage, rule_failures = _rule_coverage_results(
        manifest.get("ruleCoverage") if isinstance(manifest.get("ruleCoverage"), dict) else {},
        cases,
    )
    gate_failures.extend(rule_failures)
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
        labeling_scope={
            "fullyLabeledOutputDomains": list(labeling_scope.get("fullyLabeledOutputDomains", [])),
            "notFullyLabeledOutputDomains": list(labeling_scope.get("notFullyLabeledOutputDomains", [])),
        },
        output_dimension_metrics=output_dimension_metrics,
        rule_coverage=rule_coverage,
        corpus_governance=corpus_governance,
        acceptance_metrics=acceptance_metrics,
        cases=cases,
    )


__all__ = [
    "FORMAL_CASE_SCHEMA_VERSION",
    "FORMAL_CORPUS_SCHEMA_VERSION",
    "FORMAL_V2_CASE_SCHEMA_VERSION",
    "FORMAL_V2_CORPUS_SCHEMA_VERSION",
    "REAL_CORPUS_ADMISSION_SCHEMA_VERSION",
    "FORMAL_ACCEPTANCE_CRITERIA_SCHEMA_VERSION",
    "FormalEvalAggregate",
    "FormalEvalCaseResult",
    "FormalEvalDimensionResult",
    "FORMAL_OUTPUT_DOMAINS",
    "collect_mechanism_ids",
    "run_formal_eval_case",
    "run_formal_eval_directory",
]
