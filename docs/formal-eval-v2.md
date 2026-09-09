# Formal evaluation v2 contract

> Status: v2 output-label semantics frozen on 2026-09-09.
> Active schema: v3 adds real-corpus admission without changing v2 label semantics; see [v3 real-corpus admission](formal-eval-v3-real-corpus-admission.md).
> Implementation: `anr_evidence.formal_eval`; command: `python3 scripts/run_eval.py --formal <corpus> --json`.

This contract is an intentionally **deterministic, zero-model-call** evaluation
surface.  It reuses the existing hint evaluator to obtain stable detection
signals, but it does **not** turn the legacy hint suite into an end-to-end
accuracy claim.  In particular, a successful `python3 scripts/run_eval.py
tests/fixtures/eval` run (currently 13 hint cases) is only a regression result
for the listed hint rules.

The frozen v2 contract was recorded after the already-completed D1 local
slice.  It therefore does not retroactively prove that the historical D1 work
was developed against this schema.  It governs subsequent D1 maintenance and
all later formal-evaluation rule changes.

## 1. Corpus and case identity

The frozen v2 layer defined the output-label fields below.  The active v3
manifest (`anr-formal-eval-corpus-v3`) preserves them and adds the separate
real-corpus admission block described in [v3 real-corpus admission](formal-eval-v3-real-corpus-admission.md).  It must declare:

- `corpusRevision` and `frozen: true`;
- `caseSha256` for every listed case file;
- required Android releases and a minimum case count per release;
- `requiredSourceClass: "licensed_deidentified"` for a qualifying corpus;
- deterministic per-case wall-time, input-byte, artifact-byte, and
  `maxModelCalls: 0` budgets;
- the complete/non-complete labeling scope; and
- the positive and negative case sets for every declared formal rule.

Each case uses `anr-formal-eval-case-v2` and declares corpus identity, Android
release, device/build metadata, expected legacy hints and mechanisms, timeout
ontology observations/provenance, and immutable evidence citation
expectations.  Evaluation recreates a package, exports evidence through the
store/ESS path, and only counts a citation after the sidecar reference can be
read back from the store.

A mismatched case hash, malformed identity, budget breach, unmatched ontology
expectation, or unverified citation fails closed.  Case passing is not by
itself qualification: the aggregate must also satisfy corpus source and
release-coverage gates.

## 2. Fully evaluated output domains

V2 evaluates exactly these deterministic output domains:

| Domain | Actual output collected by v2 | What it establishes when completely labelled |
| --- | --- | --- |
| `trigger` | timeout ontology `canonicalTrigger` | The declared timeout trigger identifier only |
| `blocker` | explicit direct-blocker mechanism identifiers derived from existing hints | An observed blocker class, not a final cause |
| `evidence_status` | timeout ontology `evidenceStatus` | The ontology observation status |
| `citations` | source kinds of immutable ESS/store references that were read back | That these source kinds have verified evaluator references |
| `contradictions` | structured Input/WMS/identity conflicts | The listed deterministic conflict signals |
| `high_confidence_violations` | violations of current conservative V1/DQ1/ontology structural contracts | Contract violations only; not LLM confidence calibration |

The aggregate emits metrics independently for every domain: labelled case
count, complete/partial/unsupported counts, complete pass/fail counts, actual
output count, and missing/forbidden/unlabelled/unexpected outputs.  A domain
is never silently folded into a general hint pass rate.

## 3. Label coverage semantics

Every case has `expected.outputLabels.<domain>` with:

```json
{
  "coverage": "complete | partial | unsupported",
  "requiredIds": ["..."],
  "forbiddenIds": ["..."],
  "allowedIds": ["..."]
}
```

`requiredIds` and `forbiddenIds` are disjoint.  Their interpretation is:

| Coverage | Evaluation rule | Can it be counted as correct? |
| --- | --- | --- |
| `complete` | All required IDs must occur, forbidden IDs must not occur, and **every observed ID must be in `allowedIds`**.  `allowedIds` includes all required IDs and excludes every forbidden ID. | Yes, but only if all three checks pass. |
| `partial` | Known required and forbidden IDs are still checked.  Every other observed ID is recorded as `unlabeled_observed_ids`. | No. `passed` is `null`; unknown extra outputs are not evidence of correctness. |
| `unsupported` | No output IDs are claimed by this case. | No. `passed` is `null`. |

Therefore, an output being absent from `forbiddenIds` does **not** make it
correct.  With complete labels it must be explicitly allowed; with partial
labels it remains observed-but-unlabelled; with unsupported labels it is outside
the measured scope.  If any one of the six v2 domains is not declared fully
labelled in the manifest, the aggregate contains
`output_domain_not_fully_labeled:<domain>` and cannot be `qualified=true`.

The shipped synthetic corpus completely labels all six v2 domains.  It
explicitly does **not** fully label:

- `final_root_cause`;
- `ranked_candidate_chains`;
- `remediation_quality`;
- `device_profile_compatibility`; or
- `lifecycle_freshness`.

No output in that second list has an accuracy claim from v2.

## 4. Positive and negative rule pairs

`ruleCoverage` freezes the test obligations for each declared formal rule:

```json
{
  "main_thread_binder_reply_wait": {
    "positiveCaseIds": ["case-that-must-observe-the-rule"],
    "negativeCaseIds": ["case-that-must-not-observe-the-rule"]
  }
}
```

Both lists must be non-empty, refer to real case IDs, and must not overlap.
Every positive case must pass and observe the rule; every negative case must
pass and not observe it.  Missing, unknown, failing, or semantically inverted
pairs produce `rule_coverage_*` gate failures.  When a formal rule changes,
the rule declaration and **both** its positive and negative fixtures must be
updated in the same corpus revision and their SHA-256 commitments regenerated.

This pair requirement tests the declared deterministic rule behavior.  It does
not substitute for unlabelled model outputs, real-device behavior, or a
licensed corpus.

## 5. Legacy hint eval versus formal qualification

The legacy command remains useful and intentionally separate:

```bash
python3 scripts/run_eval.py tests/fixtures/eval
```

It reports per-hint precision/recall/F1 over the legacy fixture definitions.
It neither requires a frozen corpus nor evaluates immutable citations, Android
release coverage, source permission/deidentification, output-label coverage,
contradictions, high-confidence contract violations, or device compatibility.
It must never be described as end-to-end root-cause accuracy or as formal
qualification.

Formal evaluation is run separately:

```bash
python3 scripts/run_eval.py --formal tests/fixtures/formal_eval --json
```

`exit 0` requires `qualified=true`.  `exit 2` with
`qualificationStatus="blocked"` is an intentional fail-closed result, not a
pass.  On 2026-09-09 the checked-in v3 corpus has two Android 17 **synthetic**
cases.  It validates v2 output-label and v3 admission-rule behavior only; it
remains blocked because Android 14–16 coverage and a `licensed_deidentified`
corpus are absent.

## 6. Required interpretation limits

V2 does not prove any of the following unless a future schema/corpus explicitly
adds complete labels and its own acceptance evidence:

- final root cause or causal ranking;
- remediation quality;
- model response quality or LLM accuracy;
- source-byte/device/OEM compatibility outside separately verified profiles;
- privacy, source-license, or real private-log eligibility; or
- compatibility with a real device merely because a synthetic fixture passes.

Synthetic fixtures can prove deterministic evaluator behavior.  They cannot
prove production corpus quality, cross-version device compatibility, or
end-to-end diagnostic correctness.
