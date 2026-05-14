"""Replay harness for repeatable ANR pipeline benchmarks."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
import time
from typing import Any

from .analyzer import analyze_normalized_package
from .delivery import render_final_delivery
from .extractor import extract_evidence_package, load_package_from_archive, load_package_from_directory, load_package_from_fixture
from .hypothesis import generate_causal_draft
from .normalizer import normalize_evidence_package
from .remediation import generate_remediation_drafts
from .reporter import render_analysis_report
from .root_cause import generate_root_cause_report

SUPPORTED_OPERATIONS = {"extract", "normalize", "analyze", "hypothesize", "root-cause", "remediate", "report", "deliver"}


class ReplayError(ValueError):
    """Raised when a replay manifest or case is invalid."""



def run_replay_manifest(manifest_path: str | Path, output_dir: str | Path | None = None) -> dict[str, Any]:
    manifest_file = Path(manifest_path)
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ReplayError("Replay manifest must contain a non-empty `cases` list.")

    artifact_root = Path(output_dir) if output_dir else manifest_file.parent / ".replay-out" / manifest_file.stem
    artifact_root.mkdir(parents=True, exist_ok=True)

    results = []
    for index, case in enumerate(cases, start=1):
        case_id = case.get("id") or f"case-{index}"
        input_ref = case.get("input")
        operation = case.get("operation", "deliver")
        if operation not in SUPPORTED_OPERATIONS:
            raise ReplayError(f"Replay case `{case_id}` uses unsupported operation `{operation}`.")
        if not input_ref:
            raise ReplayError(f"Replay case `{case_id}` is missing `input`.")

        resolved_input = manifest_file.parent / input_ref
        input_kind = _input_kind(resolved_input)
        input_bytes = _input_size_bytes(resolved_input)
        start = time.perf_counter()
        payload = _load_input(resolved_input)
        result = _run_operation(payload, operation)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 3)

        artifact_path = artifact_root / f"{case_id}.{'md' if isinstance(result, str) else 'json'}"
        if isinstance(result, str):
            artifact_path.write_text(result + ("" if result.endswith("\n") else "\n"), encoding="utf-8")
            phase = "markdown-delivery" if operation == "deliver" else "markdown-report"
        else:
            artifact_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            phase = result.get("metadata", {}).get("phase")

        artifact_bytes = artifact_path.stat().st_size
        source_metrics = _source_metrics(payload)
        results.append(
            {
                "id": case_id,
                "operation": operation,
                "input": input_ref,
                "inputKind": input_kind,
                "inputBytes": input_bytes,
                "phase": phase,
                "artifact": str(artifact_path),
                "artifactBytes": artifact_bytes,
                "elapsedMs": elapsed_ms,
                "expectedPhase": case.get("expectedPhase"),
                "phaseMatched": case.get("expectedPhase") in (None, phase),
                "sourceMetrics": source_metrics,
            }
        )

    return {
        "manifest": str(manifest_file),
        "artifactRoot": str(artifact_root),
        "caseCount": len(results),
        "totalElapsedMs": round(sum(item["elapsedMs"] for item in results), 3),
        "totalArtifactBytes": sum(item["artifactBytes"] for item in results),
        "results": results,
    }



def archive_replay_session(manifest_path: str | Path, out_root: str | Path, label: str | None = None) -> dict[str, Any]:
    manifest_file = Path(manifest_path)
    out_root_path = Path(out_root)
    out_root_path.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    slug = manifest_file.stem if label is None else f"{manifest_file.stem}-{label}"
    session_dir = out_root_path / f"{timestamp}-{slug}"
    artifacts_dir = session_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    summary = run_replay_manifest(manifest_file, artifacts_dir)
    manifest_copy = session_dir / "manifest.json"
    shutil.copy2(manifest_file, manifest_copy)
    summary_path = session_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    rule_coverage = build_rule_coverage_report(manifest_file, summary)
    rule_coverage_json_path = session_dir / "rule-coverage.json"
    rule_coverage_json_path.write_text(json.dumps(rule_coverage, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    rule_coverage_md_path = session_dir / "rule-coverage.md"
    rule_coverage_md_path.write_text(render_rule_coverage_markdown(rule_coverage), encoding="utf-8")

    metadata = {
        "manifest": str(manifest_file),
        "sessionDir": str(session_dir),
        "artifactsDir": str(artifacts_dir),
        "summaryPath": str(summary_path),
        "ruleCoverageJsonPath": str(rule_coverage_json_path),
        "ruleCoverageMarkdownPath": str(rule_coverage_md_path),
        "manifestCopy": str(manifest_copy),
        "caseCount": summary["caseCount"],
        "totalElapsedMs": summary["totalElapsedMs"],
        "totalArtifactBytes": summary["totalArtifactBytes"],
    }
    metadata_path = session_dir / "session.json"
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    index = build_replay_index(out_root_path)
    metadata["indexPath"] = str(Path(index["runsRoot"]) / "index.json")
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return metadata



def build_replay_index(runs_root: str | Path) -> dict[str, Any]:
    runs_root_path = Path(runs_root)
    runs_root_path.mkdir(parents=True, exist_ok=True)
    sessions = []
    aggregated_rule_totals = {key: 0 for key in RULE_KEYS}
    for session_dir in sorted((path for path in runs_root_path.iterdir() if path.is_dir()), reverse=True):
        session_json = session_dir / "session.json"
        summary_json = session_dir / "summary.json"
        coverage_json = session_dir / "rule-coverage.json"
        if not session_json.exists() or not summary_json.exists():
            continue
        session = json.loads(session_json.read_text(encoding="utf-8"))
        summary = json.loads(summary_json.read_text(encoding="utf-8"))
        coverage = json.loads(coverage_json.read_text(encoding="utf-8")) if coverage_json.exists() else {"ruleTotals": {}}
        for key in RULE_KEYS:
            aggregated_rule_totals[key] += int(coverage.get("ruleTotals", {}).get(key, 0))
        sessions.append(
            {
                "sessionDir": str(session_dir),
                "manifest": session.get("manifest"),
                "caseCount": session.get("caseCount", summary.get("caseCount")),
                "totalElapsedMs": session.get("totalElapsedMs", summary.get("totalElapsedMs", 0)),
                "totalArtifactBytes": session.get("totalArtifactBytes", summary.get("totalArtifactBytes", 0)),
                "resultPhases": sorted({result.get("phase") for result in summary.get("results", []) if result.get("phase")}),
                "ruleTotals": coverage.get("ruleTotals", {}),
            }
        )
    index = {
        "runsRoot": str(runs_root_path),
        "sessionCount": len(sessions),
        "totalCaseCount": sum(session["caseCount"] or 0 for session in sessions),
        "aggregatedRuleTotals": aggregated_rule_totals,
        "sessions": sessions,
    }
    index_path = runs_root_path / "index.json"
    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return index





def compare_replay_sessions(baseline_session: str | Path, candidate_session: str | Path) -> dict[str, Any]:
    baseline_dir = Path(baseline_session)
    candidate_dir = Path(candidate_session)
    baseline_summary = _load_session_summary(baseline_dir)
    candidate_summary = _load_session_summary(candidate_dir)
    baseline_coverage = _load_session_rule_coverage(baseline_dir)
    candidate_coverage = _load_session_rule_coverage(candidate_dir)

    baseline_by_id = {item["id"]: item for item in baseline_summary.get("results", [])}
    candidate_by_id = {item["id"]: item for item in candidate_summary.get("results", [])}
    baseline_rules_by_id = {item["id"]: item for item in baseline_coverage.get("cases", [])}
    candidate_rules_by_id = {item["id"]: item for item in candidate_coverage.get("cases", [])}
    all_ids = sorted(set(baseline_by_id) | set(candidate_by_id))
    case_diffs = []
    for case_id in all_ids:
        before = baseline_by_id.get(case_id)
        after = candidate_by_id.get(case_id)
        before_rules = (baseline_rules_by_id.get(case_id) or {}).get("signals", {})
        after_rules = (candidate_rules_by_id.get(case_id) or {}).get("signals", {})
        case_diffs.append(
            {
                "id": case_id,
                "status": _case_status(before, after),
                "baselinePhase": before.get("phase") if before else None,
                "candidatePhase": after.get("phase") if after else None,
                "phaseChanged": (before or {}).get("phase") != (after or {}).get("phase"),
                "elapsedMsDelta": _delta((before or {}).get("elapsedMs"), (after or {}).get("elapsedMs")),
                "artifactBytesDelta": _delta((before or {}).get("artifactBytes"), (after or {}).get("artifactBytes")),
                "inputKindChanged": (before or {}).get("inputKind") != (after or {}).get("inputKind"),
                "phaseMatchedBefore": (before or {}).get("phaseMatched"),
                "phaseMatchedAfter": (after or {}).get("phaseMatched"),
                "ruleSignalChanges": _rule_signal_changes(before_rules, after_rules),
            }
        )

    rule_totals_diff = {
        key: {
            "before": int(baseline_coverage.get("ruleTotals", {}).get(key, 0)),
            "after": int(candidate_coverage.get("ruleTotals", {}).get(key, 0)),
            "delta": int(candidate_coverage.get("ruleTotals", {}).get(key, 0)) - int(baseline_coverage.get("ruleTotals", {}).get(key, 0)),
        }
        for key in RULE_KEYS
    }

    return {
        "baselineSession": str(baseline_dir),
        "candidateSession": str(candidate_dir),
        "caseCountBefore": baseline_summary.get("caseCount", 0),
        "caseCountAfter": candidate_summary.get("caseCount", 0),
        "totalElapsedMsBefore": baseline_summary.get("totalElapsedMs", 0),
        "totalElapsedMsAfter": candidate_summary.get("totalElapsedMs", 0),
        "totalElapsedMsDelta": _delta(baseline_summary.get("totalElapsedMs"), candidate_summary.get("totalElapsedMs")),
        "totalArtifactBytesBefore": baseline_summary.get("totalArtifactBytes", 0),
        "totalArtifactBytesAfter": candidate_summary.get("totalArtifactBytes", 0),
        "totalArtifactBytesDelta": _delta(baseline_summary.get("totalArtifactBytes"), candidate_summary.get("totalArtifactBytes")),
        "ruleTotalsDiff": rule_totals_diff,
        "caseDiffs": case_diffs,
    }


def evaluate_replay_diff(
    diff: dict[str, Any],
    *,
    max_elapsed_ms_delta: float | None = None,
    max_artifact_bytes_delta: int | None = None,
    allow_phase_changes: bool = False,
    allow_case_count_changes: bool = False,
    allow_rule_regressions: bool = False,
) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []

    if not allow_case_count_changes and diff.get("caseCountBefore") != diff.get("caseCountAfter"):
        failures.append(
            {
                "kind": "case-count-changed",
                "message": f"Case count changed from {diff.get('caseCountBefore')} to {diff.get('caseCountAfter')}.",
            }
        )

    total_elapsed_delta = diff.get("totalElapsedMsDelta")
    if max_elapsed_ms_delta is not None and total_elapsed_delta is not None and total_elapsed_delta > max_elapsed_ms_delta:
        failures.append(
            {
                "kind": "total-elapsed-delta-exceeded",
                "message": f"Total elapsed delta {total_elapsed_delta}ms exceeded threshold {max_elapsed_ms_delta}ms.",
            }
        )

    total_artifact_delta = diff.get("totalArtifactBytesDelta")
    if max_artifact_bytes_delta is not None and total_artifact_delta is not None and total_artifact_delta > max_artifact_bytes_delta:
        failures.append(
            {
                "kind": "total-artifact-bytes-delta-exceeded",
                "message": f"Total artifact size delta {total_artifact_delta} bytes exceeded threshold {max_artifact_bytes_delta} bytes.",
            }
        )

    if not allow_rule_regressions:
        for rule_key, rule_delta in diff.get("ruleTotalsDiff", {}).items():
            if rule_delta.get("delta", 0) < 0:
                failures.append(
                    {
                        "kind": "rule-total-regressed",
                        "ruleKey": rule_key,
                        "message": f"Rule `{rule_key}` regressed from {rule_delta.get('before')} to {rule_delta.get('after')}.",
                    }
                )

    for case in diff.get("caseDiffs", []):
        if not allow_phase_changes and case.get("phaseChanged"):
            failures.append(
                {
                    "kind": "phase-changed",
                    "caseId": case.get("id"),
                    "message": f"Case `{case.get('id')}` changed phase from {case.get('baselinePhase')} to {case.get('candidatePhase')}.",
                }
            )
        if max_elapsed_ms_delta is not None:
            case_elapsed_delta = case.get("elapsedMsDelta")
            if case_elapsed_delta is not None and case_elapsed_delta > max_elapsed_ms_delta:
                failures.append(
                    {
                        "kind": "case-elapsed-delta-exceeded",
                        "caseId": case.get("id"),
                        "message": f"Case `{case.get('id')}` elapsed delta {case_elapsed_delta}ms exceeded threshold {max_elapsed_ms_delta}ms.",
                    }
                )
        if max_artifact_bytes_delta is not None:
            case_artifact_delta = case.get("artifactBytesDelta")
            if case_artifact_delta is not None and case_artifact_delta > max_artifact_bytes_delta:
                failures.append(
                    {
                        "kind": "case-artifact-bytes-delta-exceeded",
                        "caseId": case.get("id"),
                        "message": f"Case `{case.get('id')}` artifact delta {case_artifact_delta} bytes exceeded threshold {max_artifact_bytes_delta} bytes.",
                    }
                )
        if not allow_rule_regressions:
            for rule_key, change in case.get("ruleSignalChanges", {}).items():
                if change.get("before") is True and change.get("after") is False:
                    failures.append(
                        {
                            "kind": "case-rule-regressed",
                            "caseId": case.get("id"),
                            "ruleKey": rule_key,
                            "message": f"Case `{case.get('id')}` regressed rule `{rule_key}` from hit to miss.",
                        }
                    )

    return {
        "passed": not failures,
        "failureCount": len(failures),
        "failures": failures,
        "thresholds": {
            "maxElapsedMsDelta": max_elapsed_ms_delta,
            "maxArtifactBytesDelta": max_artifact_bytes_delta,
            "allowPhaseChanges": allow_phase_changes,
            "allowCaseCountChanges": allow_case_count_changes,
            "allowRuleRegressions": allow_rule_regressions,
        },
    }

def _load_input(path: Path) -> dict[str, Any]:
    if path.is_dir():
        return load_package_from_directory(path)
    if _is_archive_path(path):
        return load_package_from_archive(path)
    return load_package_from_fixture(path)



def _is_archive_path(path: Path) -> bool:
    suffixes = [suffix.lower() for suffix in path.suffixes]
    if not suffixes:
        return False
    if suffixes[-1] == ".zip":
        return True
    return any(suffix in {".tar", ".gz", ".tgz", ".bz2", ".xz"} for suffix in suffixes)



def _input_kind(path: Path) -> str:
    if path.is_dir():
        return "directory"
    if _is_archive_path(path):
        return "archive"
    return "fixture"



def _input_size_bytes(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(candidate.stat().st_size for candidate in path.rglob("*") if candidate.is_file())



def _source_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    sources = payload.get("sources", {})
    counts = {source_kind: len(source.get("content", "").splitlines()) for source_kind, source in sources.items()}
    bytes_by_source = {source_kind: len(source.get("content", "").encode("utf-8")) for source_kind, source in sources.items()}
    return {
        "sourceCount": len(sources),
        "lineCounts": counts,
        "bytesBySource": bytes_by_source,
    }



def _run_operation(payload: dict[str, Any], operation: str) -> dict[str, Any] | str:
    phase = _payload_phase(payload)
    if operation == "extract":
        return payload if phase == 1 else extract_evidence_package(payload)
    if operation == "normalize":
        if phase == 2:
            return payload
        if phase == 1:
            return normalize_evidence_package(payload)
        return normalize_evidence_package(extract_evidence_package(payload))
    if operation == "analyze":
        if phase == 3:
            return payload
        if phase == 2:
            return analyze_normalized_package(payload)
        if phase == 1:
            return analyze_normalized_package(normalize_evidence_package(payload))
        return analyze_normalized_package(normalize_evidence_package(extract_evidence_package(payload)))
    if operation == "hypothesize":
        if phase == 5:
            return payload
        if phase == 3:
            return generate_causal_draft(payload)
        return generate_causal_draft(_run_operation(payload, "analyze"))
    if operation == "root-cause":
        if phase == 6:
            return payload
        if phase == 5:
            return generate_root_cause_report(payload)
        return generate_root_cause_report(_run_operation(payload, "hypothesize"))
    if operation == "remediate":
        if phase == 7:
            return payload
        if phase == 6:
            return generate_remediation_drafts(payload)
        return generate_remediation_drafts(_run_operation(payload, "root-cause"))
    if operation == "report":
        return render_analysis_report(_run_operation(payload, "remediate"))
    if operation == "deliver":
        return render_final_delivery(_run_operation(payload, "remediate"))
    raise ReplayError(f"Unsupported replay operation `{operation}`.")



def _payload_phase(payload: dict[str, Any]) -> int:
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return 0
    return {
        "phase1-evidence-extraction-mvp": 1,
        "phase2-evidence-normalization": 2,
        "phase3-assisted-analysis": 3,
        "phase5-causal-draft": 5,
        "phase6-root-cause-report-v1": 6,
        "phase7-remediation-draft": 7,
    }.get(metadata.get("phase"), 0)


def _load_session_summary(session_dir: Path) -> dict[str, Any]:
    summary_path = session_dir / "summary.json"
    if not summary_path.exists():
        raise ReplayError(f"Replay session `{session_dir}` does not contain summary.json")
    return json.loads(summary_path.read_text(encoding="utf-8"))


def _load_session_rule_coverage(session_dir: Path) -> dict[str, Any]:
    coverage_path = session_dir / "rule-coverage.json"
    if not coverage_path.exists():
        raise ReplayError(f"Replay session `{session_dir}` does not contain rule-coverage.json")
    return json.loads(coverage_path.read_text(encoding="utf-8"))


RULE_KEYS = (
    "mainThreadCaptured",
    "lockContentionDetected",
    "binderWaitChainDetected",
    "renderWaitChainDetected",
    "stwPauseDetected",
    "vmWaitClusterDetected",
    "schedulerPressureDetected",
    "cpuBusyExecutionDetected",
    "inputWaitDetected",
    "noFocusedWindowDetected",
    "crossSourceInputConsistency",
)


def build_rule_coverage_report(manifest_path: str | Path, summary: dict[str, Any]) -> dict[str, Any]:
    manifest_file = Path(manifest_path)
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    cases = manifest.get("cases", [])
    cases_by_id = {case.get("id"): case for case in cases}

    per_case: list[dict[str, Any]] = []
    rule_totals = {key: 0 for key in RULE_KEYS}
    matched_cases = 0
    failed_cases = 0

    for result in summary.get("results", []):
        case = cases_by_id.get(result["id"], {})
        resolved_input = manifest_file.parent / case.get("input", result.get("input", ""))
        payload = _load_input(resolved_input)
        analysis = _run_operation(payload, "analyze")
        signals = _extract_rule_signals(analysis)
        expected_signals = dict(case.get("expectedSignals", {}))
        expectation_failures = []
        for key, expected in expected_signals.items():
            actual = bool(signals.get(key))
            if actual != bool(expected):
                expectation_failures.append(
                    {
                        "ruleKey": key,
                        "expected": bool(expected),
                        "actual": actual,
                    }
                )
        for key, value in signals.items():
            if value:
                rule_totals[key] += 1
        expectation_matched = not expectation_failures
        if expected_signals:
            if expectation_matched:
                matched_cases += 1
            else:
                failed_cases += 1
        per_case.append(
            {
                "id": result["id"],
                "input": result.get("input"),
                "inputKind": result.get("inputKind"),
                "phaseMatched": result.get("phaseMatched"),
                "elapsedMs": result.get("elapsedMs"),
                "signals": signals,
                "expectedSignals": expected_signals,
                "expectationMatched": expectation_matched,
                "expectationFailures": expectation_failures,
                "sourceCounts": analysis.get("signalSummary", {}).get("sourceCounts", {}),
                "inputInsights": analysis.get("signalSummary", {}).get("inputInsights", {}),
                "traceInsights": analysis.get("signalSummary", {}).get("traceInsights", {}),
            }
        )

    return {
        "manifest": str(manifest_file),
        "caseCount": len(per_case),
        "ruleTotals": rule_totals,
        "expectationSummary": {
            "casesWithExpectations": sum(1 for case in per_case if case.get("expectedSignals")),
            "matchedCases": matched_cases,
            "failedCases": failed_cases,
        },
        "cases": per_case,
    }


def render_rule_coverage_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Replay 规则命中报告")
    lines.append("")
    lines.append(f"- Manifest: `{report.get('manifest')}`")
    lines.append(f"- Case Count: `{report.get('caseCount')}`")
    expectation_summary = report.get("expectationSummary", {})
    lines.append(f"- Cases With Expectations: `{expectation_summary.get('casesWithExpectations', 0)}`")
    lines.append(f"- Matched Cases: `{expectation_summary.get('matchedCases', 0)}`")
    lines.append(f"- Failed Cases: `{expectation_summary.get('failedCases', 0)}`")
    lines.append("")
    lines.append("## 规则汇总")
    lines.append("")
    for key, count in report.get("ruleTotals", {}).items():
        lines.append(f"- `{key}`: {count}")
    lines.append("")
    lines.append("## Case 明细")
    lines.append("")
    lines.append("| Case | InputKind | Expect | main | lock | binder | render | stw | vmwait | sched | cpubusy | input | nofocus | cross-source |")
    lines.append("| --- | --- | ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for case in report.get("cases", []):
        signals = case.get("signals", {})
        lines.append(
            "| "
            f"{case.get('id')} | {case.get('inputKind')} | "
            f"{'OK' if case.get('expectationMatched', True) else 'FAIL'} | "
            f"{_flag(signals.get('mainThreadCaptured'))} | "
            f"{_flag(signals.get('lockContentionDetected'))} | "
            f"{_flag(signals.get('binderWaitChainDetected'))} | "
            f"{_flag(signals.get('renderWaitChainDetected'))} | "
            f"{_flag(signals.get('stwPauseDetected'))} | "
            f"{_flag(signals.get('vmWaitClusterDetected'))} | "
            f"{_flag(signals.get('schedulerPressureDetected'))} | "
            f"{_flag(signals.get('cpuBusyExecutionDetected'))} | "
            f"{_flag(signals.get('inputWaitDetected'))} | "
            f"{_flag(signals.get('noFocusedWindowDetected'))} | "
            f"{_flag(signals.get('crossSourceInputConsistency'))} |"
        )
        if case.get("expectationFailures"):
            for failure in case["expectationFailures"]:
                lines.append(
                    f"| ↳ expected `{failure['ruleKey']}`={failure['expected']} but actual={failure['actual']} |  |  |  |  |  |  |  |  |  |  |  |  |  |"
                )
    lines.append("")
    return "\n".join(lines) + "\n"


def _extract_rule_signals(analysis: dict[str, Any]) -> dict[str, bool]:
    trace = analysis.get("signalSummary", {}).get("traceInsights", {})
    main = trace.get("mainThread", {})
    binder = trace.get("binderSummary", {})
    render = trace.get("renderSummary", {})
    suspend = trace.get("suspendSummary", {})
    cpu = trace.get("cpuSummary", {})
    input_insights = analysis.get("signalSummary", {}).get("inputInsights", {})
    return {
        "mainThreadCaptured": bool(main.get("captured")),
        "lockContentionDetected": bool(main.get("lockContentionDetected")),
        "binderWaitChainDetected": bool(binder.get("binderWaitChainDetected")),
        "renderWaitChainDetected": bool(render.get("renderWaitChainDetected")),
        "stwPauseDetected": bool(suspend.get("stwPauseDetected")),
        "vmWaitClusterDetected": bool(suspend.get("vmWaitClusterDetected")),
        "schedulerPressureDetected": bool(cpu.get("schedulerPressureDetected")),
        "cpuBusyExecutionDetected": bool(cpu.get("cpuBusyExecutionDetected")),
        "inputWaitDetected": bool(input_insights.get("inputWaitDetected")),
        "noFocusedWindowDetected": bool(input_insights.get("noFocusedWindowDetected")),
        "crossSourceInputConsistency": bool(input_insights.get("crossSourceInputConsistency")),
    }


def _flag(value: bool | None) -> str:
    return "Y" if value else ""



def _case_status(before: dict[str, Any] | None, after: dict[str, Any] | None) -> str:
    if before and after:
        return "matched"
    if before and not after:
        return "removed"
    return "added"



def _delta(before: Any, after: Any) -> Any:
    if before is None or after is None:
        return None
    return round(after - before, 3) if isinstance(before, float) or isinstance(after, float) else after - before


def _rule_signal_changes(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, bool]]:
    return {
        key: {
            "before": bool(before.get(key)),
            "after": bool(after.get(key)),
        }
        for key in RULE_KEYS
    }
