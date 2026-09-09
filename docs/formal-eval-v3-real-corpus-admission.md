# Formal evaluation v3 real-corpus admission contract

> Status: active formal corpus schema on 2026-09-09.  The v2 output-label
> semantics remain frozen; v3 adds corpus governance without changing their
> meaning.  See [the v2 output-label contract](formal-eval-v2.md).

V3 is a local, deterministic **admission and reporting contract** for a formal
corpus.  It rejects malformed or incomplete evidence before the deterministic
evaluator runs.  It does not independently authenticate a licence, certify
redaction, permit outbound transfer, or make an arbitrary private log safe to
load.  An external privacy/licence reviewer must inspect the records that the
manifest commits to.

The checked-in corpus remains synthetic and continues to return `blocked`; it
proves v3 rule behavior only.  It contains no real or private incident logs.

## 1. Frozen manifest governance

A v3 `corpus.json` uses `anr-formal-eval-corpus-v3`, remains `frozen: true`,
and has immutable SHA-256 commitments for every case.  In addition to the
existing release, source-class, resource, label-scope, and rule-pair fields, it
must contain:

```json
{
  "realCorpusAdmission": {
    "schemaVersion": "anr-formal-real-corpus-admission-v1",
    "splitIsolation": {
      "developmentSplits": ["development"],
      "evaluationSplits": ["evaluation"],
      "identityKeys": ["incidentSourceId", "deviceSourceId"]
    },
    "requiredManualLabelDomains": [
      "trigger", "blocker", "evidence_status", "citations",
      "contradictions", "high_confidence_violations"
    ],
    "minimumSupportByClass": {
      "trigger:input.no_focused_window": 1
    },
    "statisticalThresholdsEnabled": false,
    "statisticalThresholds": {
      "macroF1": 0.95,
      "top3Accuracy": 0.90
    }
  }
}
```

The revision, split policy, required manual-label domains, and per-class
minimum support are part of the frozen manifest.  They cannot be added after
examining scores to make a corpus appear sufficient.

- `developmentSplits` and `evaluationSplits` must be non-empty, use closed
  split names, and must not overlap.
- Both opaque identity aliases are mandatory.  They are used locally to reject
  incident or device-source overlap between development and evaluation.  They
  must not be raw customer/device identifiers.
- Every support class declares its own positive integer minimum.  Support,
  Android-release coverage, source-class eligibility, and output-label metrics
  are counted only from evaluation-split samples; a development sample cannot
  fill an evaluation quota or make a production corpus qualify.
- `macroF1` and `top3Accuracy` are declared quality targets, not scores
  produced by the current deterministic evaluator.

## 2. Case-level production admission

Every case contains the normal release/model/build fields plus opaque source
identity and support classes:

```json
{
  "corpus": {
    "split": "evaluation",
    "sampleIdentity": {
      "incidentSourceId": "opaque-incident-alias",
      "deviceSourceId": "opaque-device-alias"
    },
    "supportClassIds": ["trigger:input.no_focused_window"]
  }
}
```

Only two source classes are accepted: `synthetic` and
`licensed_deidentified`.  Any other class — including a raw/private source —
is rejected before evaluator execution.  A caller cannot reclassify a private
log as safe by adding metadata.

A `licensed_deidentified` case additionally requires all of the following:

1. A non-empty `sourceLicense` plus `sourceLicenseRecord` with an opaque record
   ID, approver identity, and SHA-256 commitment for the externally reviewed
   licence/permission evidence.
2. `deidentificationStatus: "verified"` plus
   `deidentificationValidation`: exact `anr-redaction-v1` ruleset version,
   validation ID, validator identity, and redacted-view commitment hash.
3. `manualAnnotation.status: "complete"`, a label-set revision, at least one
   annotator and reviewer, all six required label domains, and no missing or
   unsupported required domain.
4. Android release, device model, and build fingerprint consistent with the
   package input.

These record references make a missing approval, missing review, or wrong
redaction ruleset fail closed.  They are **not self-authenticating evidence**:
publication still requires the external review system to validate the committed
records and the organization’s retention/privacy obligations.

Synthetic cases instead require `deidentificationStatus: "not_applicable"` and
`manualAnnotation.status: "not_applicable"`.  They can exercise evaluator
rules, split accounting, and support-report code, but never satisfy the
production source-class gate.

## 3. Aggregate reporting and gates

`python3 scripts/run_eval.py --formal <corpus> --json` emits
`corpusGovernance` alongside the v2 output metrics.  It contains:

- `sampleCountsBySplit` and `sampleCountsBySourceClassAndSplit`;
- `supportByClass` with observed evaluation samples and frozen minimum;
- `splitLeakage` for incident and device aliases;
- `missingManualLabels` and `unsupportedManualLabels`;
- `unsupportedOutputDomains`; and
- declared statistical targets and their execution status.

The evaluator returns `blocked` for any of the following:

- development/evaluation incident or device-source leakage;
- support below the frozen minimum for any class;
- a production case lacking licence, deidentification, device, or manual-label
  metadata; or
- a declared enabled statistics gate when macro-F1/top-3 metric execution is
  not implemented.

The last condition is intentional.  V3 makes it possible to freeze the
prerequisites for statistical thresholds, but the present zero-model-call
engine does not calculate model macro-F1 or top-3 accuracy.  Setting
`statisticalThresholdsEnabled: true` therefore fails with
`statistical_threshold_metrics_unsupported`; it cannot convert passing
synthetic/deterministic fixtures into a statistics claim.

## 4. Non-goals and data boundary

V3 does not ingest arbitrary real private logs, bypass the default-deny remote
policy, or replace the `redacted_private` outbound admission contract.  A
formal corpus is an offline evaluation input.  Source licence/deidentification
records must be reviewed outside the corpus and only their opaque IDs and
commitments may be placed in a release evidence bundle.

V3 also does not establish final root-cause accuracy, candidate-chain ranking,
remediation quality, device compatibility, or cross-version compatibility.  A
licensed/deidentified corpus with sufficient support is necessary for those
future gates, not sufficient evidence that they have passed.

## 5. Deterministic acceptance measurements

The nested `acceptanceCriteria` contract is versioned separately as
`anr-formal-acceptance-criteria-v1` so the v3 admission boundary remains
stable.  A frozen manifest declares which deterministic measurements are
required:

```json
{
  "acceptanceCriteria": {
    "schemaVersion": "anr-formal-acceptance-criteria-v1",
    "requiredMeasurements": [
      "citation_integrity",
      "high_confidence_violations",
      "adversarial_trigger_false_positives"
    ],
    "minimumCitationIntegrity": 1.0,
    "maximumHighConfidenceViolations": 0,
    "adversarialTriggerRuleIds": [
      "trigger:input.no_focused_window"
    ]
  }
}
```

The evaluator reports this as top-level `acceptanceMetrics`, evaluated only
against the evaluation split:

| Measurement | Denominator / evidence | Pass condition | No sample behavior |
| --- | --- | --- | --- |
| `citationIntegrity` | cases with complete citation labels and required immutable citation kinds | every supported case has read-back evidence and complete citation output labels; rate is exactly `1.0` | `status: "N/A"`; a required measurement blocks qualification |
| `highConfidenceViolations` | cases with a complete `high_confidence_violations` label domain | deterministic conservative-contract violation count is exactly `0` | `status: "N/A"`; a required measurement blocks qualification |
| `adversarialTriggerFalsePositives` | evaluation-split negative cases for declared `trigger:*` rule pairs | observed trigger false-positive case count is exactly `0` | `status: "N/A"`; a required measurement blocks qualification |

This is not a replacement for the v2 per-output label contract: citation
integrity uses both immutable store/ESS readback and complete citation labels;
high-confidence is a structural conservative-contract check; trigger negatives
are the frozen formal adversarial cases from `ruleCoverage`.

`modelQualityGoals.macroF1` and `modelQualityGoals.top3Accuracy` are always
reported as `N/A` by the current zero-model-call evaluator.  Their frozen goals
remain `>= 0.95` and `>= 0.90`, respectively, but they cannot be claimed as
met until all of these exist together: a frozen licensed/deidentified corpus,
actual per-class support, independent model/ranking result records, and a
metric executor with an auditable denominator.  Passing synthetic hints or the
three deterministic measurements is not macro-F1/top-3 evidence.
