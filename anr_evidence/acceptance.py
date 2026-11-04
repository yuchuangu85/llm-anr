"""Content-bound report acceptance and explicit workspace finalization."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import ast
import json
import os
from pathlib import Path
import tempfile
import re
from typing import Any

from .report_validation import (
    CONSERVATIVE_FLAGS, DELIVERABLE_STATUSES, POLICY_VERSION,
    finish_result, issue, markdown_records, new_result, strict_json_loads, validate_workspace_content,
)

_ARTIFACTS = ("anr_analysis.md", "logcat.txt", "anr_reconstruction.md", "anr_reconstruction.json")
_MAX_ARTIFACT_BYTES = 64 * 1024 * 1024


def _read(path: Path) -> bytes:
    if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
        raise ValueError(f"Expected a regular non-symlink artifact: {path}")
    if path.stat().st_size > _MAX_ARTIFACT_BYTES:
        raise ValueError(f"Artifact exceeds 64 MiB acceptance limit: {path}")
    return path.read_bytes()


def _json(path: Path) -> dict:
    value = strict_json_loads(_read(path).decode("utf-8"), max_bytes=_MAX_ARTIFACT_BYTES)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _targets(input_path: str | Path) -> tuple[list[Path], Path | None, dict]:
    path = Path(input_path)
    if path.is_symlink():
        raise ValueError("Symlink workspaces are not accepted.")
    if path.is_file():
        if path.name not in {"anr_analysis.md", "index.json"}:
            raise ValueError("Expected anr_analysis.md, index.json or a generated workspace directory.")
        path = path.parent
    if not path.is_dir():
        raise ValueError("No generated ANR workspace directory found.")
    if (path / "anr_analysis.md").is_file():
        index_path = path.parent / "index.json"
        return [path], index_path if index_path.is_file() else None, _json(index_path) if index_path.is_file() else {}
    index_path = path / "index.json"
    index = _json(index_path)
    entries = index.get("groups")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Context index must contain a nonempty groups array.")
    names, directories = set(), []
    for entry in entries:
        name = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(name, str) or name in {"", ".", ".."} or name in names or Path(name).name != name or "\\" in name:
            raise ValueError("Unsafe or duplicate ANR group identity in index.")
        directory = path / name
        if directory.is_symlink() or directory.resolve().parent != path.resolve():
            raise ValueError("Unsafe ANR group path in index.")
        names.add(name)
        directories.append(directory)
    return directories, index_path, index


def _snapshot(directory: Path) -> dict[str, str]:
    return {name: sha256(_read(directory / name)).hexdigest() for name in _ARTIFACTS}


def _assert_snapshot(directory: Path, expected: dict[str, str]) -> None:
    if _snapshot(directory) != expected:
        raise ValueError("Workspace dependencies changed while finalizing.")


def _atomic_json(path: Path, value: dict) -> None:
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError(f"Cannot replace a symlink artifact: {path}")
    data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _problem(result: dict, code: str, message: str, path: str):
    result["blockers"].append(issue(code, message, path))


def _check_completeness(text: str, report: dict, result: dict):
    records, _, _ = markdown_records(text)
    starts = [record["end"] for record in records if record["text"] == "<!-- AI_ANALYSIS_SLOT:logcat-anrmanager -->"]
    ends = [record["start"] for record in records if record["text"] == "<!-- AI_ANALYSIS_SLOT:final-anr -->"]
    if len(starts) != 1 or len(ends) != 1:
        return
    headers = [record for record in records if starts[0] < record["start"] < ends[0] and record["text"] == "### 完整性检查"]
    values = {}
    if len(headers) == 1:
        for record in records:
            match = re.fullmatch(r"- (完整|缺失来源|空过滤来源|保留行数): `(.+)`", record["text"])
            if match and headers[0]["end"] <= record["start"] < ends[0]:
                if match[1] in values:
                    values = {}
                    break
                values[match[1]] = match[2]
    expected = report.get("evidenceCompleteness", {})
    try:
        counts = ast.literal_eval(values["保留行数"])
        matches = (
            values["完整"] == str(expected["complete"])
            and values["缺失来源"] == (", ".join(expected["missingSources"]) or "none")
            and values["空过滤来源"] == (", ".join(expected["emptyFilteredSources"]) or "none")
            and isinstance(counts, dict) and counts == expected["retainedLineCounts"]
            and all(isinstance(key, str) and type(value) is int and value >= 0 for key, value in counts.items())
        )
    except (KeyError, ValueError, TypeError, SyntaxError, RecursionError):
        matches = False
    if not matches:
        _problem(result, "COMPLETENESS_MISMATCH", "静态提取完整性与重建 JSON 不一致，不能由 AI 自改。", "completeness")


def _review(result: dict, records: Any) -> None:
    if records is None:
        return
    entries = records.get("reviews", []) if isinstance(records, dict) else records
    if not isinstance(entries, list):
        _problem(result, "INVALID_REVIEW_RECORD", "复核记录须为 reviews 数组。", "reviews")
        return
    applied = []
    for record in entries:
        if not isinstance(record, dict) or record.get("groupId") != result["groupId"]:
            continue
        binding = (
            record.get("evidenceRevision") == result.get("evidenceRevision")
            and record.get("analysisDigest") == result.get("analysisDigest")
        )
        if not binding:
            _problem(result, "STALE_REVIEW_RECORD", "复核记录不属于当前证据与分析版本。", "reviews")
            continue
        if record.get("source") != "external-review" or not all(isinstance(record.get(key), str) and record[key].strip() for key in ("reviewer", "scope", "rationale")):
            _problem(result, "INVALID_REVIEW_RECORD", "复核须声明外部来源、复核者、具体范围与理由。", "reviews")
            continue
        ids = record.get("issueIds")
        if not isinstance(ids, list) or not ids or any(not isinstance(value, str) for value in ids):
            _problem(result, "INVALID_REVIEW_RECORD", "复核须绑定具体待复核项，不能笼统自报通过。", "reviews.issueIds")
            continue
        pending = {item["issueId"] for item in result["reviewItems"]}
        if not set(ids) <= pending:
            _problem(result, "UNKNOWN_REVIEW_ISSUE", "复核不能覆盖硬错误或不存在的问题。", "reviews.issueIds")
            continue
        if record.get("decision") not in {"accepted_with_limitations", "accepted"}:
            _problem(result, "UNRESOLVED_REVIEW", "复核尚未接受当前候选分析。", "reviews.decision")
            continue
        result["reviewItems"] = [item for item in result["reviewItems"] if item["issueId"] not in ids]
        applied.append(deepcopy(record))
        result["warnings"].append(issue("EXTERNAL_REVIEW_APPLIED", "外部复核仅接受报告交付边界，不证明根因。", "reviews"))
    result["reviews"] = applied


def _validate_group(directory: Path, index: dict, records=None) -> dict:
    from .reconstruction import _workspace_digest, render_reconstruction_markdown

    result = new_result("workspace", directory.name)
    result["receiptStatus"] = "absent"
    missing = [name for name in _ARTIFACTS if not (directory / name).is_file()]
    if missing:
        for name in missing:
            _problem(result, "MISSING_ARTIFACT", "缺少交付依赖文件。", name)
        return finish_result(result)
    try:
        hashes = _snapshot(directory)
        analysis = _read(directory / "anr_analysis.md").decode("utf-8")
        logcat = _read(directory / "logcat.txt").decode("utf-8")
        report = _json(directory / "anr_reconstruction.json")
        result = validate_workspace_content(analysis, report, expected_group_id=directory.name)
        result.update({"artifactDigests": hashes, "evidenceRevision": report.get("evidenceRevision"), "inputIdentity": deepcopy(report.get("inputIdentity")), "receiptStatus": "absent"})
        if report.get("schemaVersion") != "anr-reconstruction-v1":
            _problem(result, "INVALID_RECONSTRUCTION_SCHEMA", "未知重建数据版本。", "anr_reconstruction.json")
        if any(report.get(key) is not value for key, value in CONSERVATIVE_FLAGS.items()):
            _problem(result, "UNSAFE_CONSERVATIVE_FLAG", "交付的重建 JSON 也须保持保守标志。", "anr_reconstruction.json")
        _check_completeness(analysis, report, result)
        current = _workspace_digest(analysis, logcat)
        if not report.get("workspaceEvidenceDigest") or current != report["workspaceEvidenceDigest"]:
            _problem(result, "EVIDENCE_CHANGED", "证据摘要不一致，须重新提取，不能靠刷新或复核绕过。", "workspaceEvidenceDigest")
        stored_analysis = report.get("workspaceAnalysisDigest")
        if stored_analysis != result["analysisDigest"]:
            _problem(result, "RECONSTRUCTION_OUTDATED", "当前分析未同步到重建，先刷新后验收。", "workspaceAnalysisDigest")
        expected_md = render_reconstruction_markdown(report).encode("utf-8")
        if sha256(expected_md).hexdigest() != hashes["anr_reconstruction.md"]:
            _problem(result, "ARTIFACT_MISMATCH", "重建 Markdown 与 JSON 重新渲染结果不一致。", "anr_reconstruction.md")
        revision = report.get("analysisRevision", {})
        if revision.get("status") in {"stale", "unknown"}:
            _problem(result, "STALE_ANALYSIS", "旧分析未在当前证据版本上重审，须重写当前结论并明确证据版本。", "analysisRevision")
        entries = [item for item in index.get("groups", []) if isinstance(item, dict) and item.get("id") == directory.name]
        if index and len(entries) != 1:
            _problem(result, "GROUP_NOT_IN_INDEX", "现有父索引必须恰好包含当前组，不能用单组入口绕过。", "index.groups")
        elif entries:
            entry = entries[0]
            for key in ("inputIdentity", "evidenceRevision"):
                if entry.get(key) != report.get(key):
                    _problem(result, "GROUP_IDENTITY_MISMATCH", "索引与组内重建的事故身份/证据版本不一致。", key)
        if not report.get("inputIdentity") or not report.get("evidenceRevision"):
            _problem(result, "MISSING_EVIDENCE_VERSION", "旧工作区缺少事故/证据版本，须重新提取。", "evidenceRevision")
        receipt_path = directory / "acceptance.json"
        stored_reviews = None
        if receipt_path.exists():
            try:
                receipt = _json(receipt_path)
                matches = (
                    receipt.get("schemaVersion") == "anr-report-acceptance-v1"
                    and receipt.get("profile") == "workspace"
                    and receipt.get("status") in DELIVERABLE_STATUSES
                    and receipt.get("blockers") == [] and receipt.get("reviewItems") == []
                    and isinstance(receipt.get("checks"), dict)
                    and receipt.get("rootCauseConfirmed") is False and receipt.get("repairVerified") is False
                    and receipt.get("policyVersion") == POLICY_VERSION
                    and receipt.get("groupId") == directory.name
                    and receipt.get("inputIdentity") == result["inputIdentity"]
                    and receipt.get("evidenceRevision") == result["evidenceRevision"]
                    and receipt.get("analysisDigest") == result["analysisDigest"]
                    and receipt.get("artifactDigests") == hashes
                )
                result["receiptStatus"] = "current" if matches else "stale"
                if matches:
                    stored_reviews = receipt.get("reviews")
            except (OSError, ValueError, UnicodeError):
                result["receiptStatus"] = "invalid"
        _review(result, records if records is not None else stored_reviews)
        result["checks"]["freshness"] = "fail" if any(item["code"] in {"EVIDENCE_CHANGED", "STALE_ANALYSIS", "RECONSTRUCTION_OUTDATED"} for item in result["blockers"]) else "pass"
        result["checks"]["artifacts"] = "fail" if any(item["code"] == "ARTIFACT_MISMATCH" for item in result["blockers"]) else "pass"
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        _problem(result, "INVALID_WORKSPACE_ARTIFACT", str(exc), str(directory))
    return finish_result(result)


def _summary(outputs: list[dict]) -> dict:
    ready = [item["id"] for item in outputs if item["deliveryReadiness"]["status"] in DELIVERABLE_STATUSES]
    blocked = [item["id"] for item in outputs if item["deliveryReadiness"]["status"] == "blocked"]
    review = [item["id"] for item in outputs if item["deliveryReadiness"]["status"] == "needs_review"]
    finalized = [item["id"] for item in outputs if item["id"] in ready and item["deliveryReadiness"].get("receiptStatus") == "current"]
    return {
        "policyVersion": POLICY_VERSION, "groupCount": len(outputs), "groups": outputs,
        "deliverable": bool(outputs) and len(ready) == len(outputs),
        "readyGroupIds": ready, "blockedGroupIds": blocked, "reviewGroupIds": review,
        "finalized": bool(outputs) and len(finalized) == len(outputs), "finalizedGroupIds": finalized,
    }


def validate_workspace_reports(input_path: str | Path, *, review_records=None) -> dict:
    """Read-only current-content validation; saved readiness is never trusted."""
    directories, _, index = _targets(input_path)
    return _summary([{
        "id": directory.name,
        "artifactPaths": {"analysis": str(directory / "anr_analysis.md"), "reconstruction": str(directory / "anr_reconstruction.md"), "reconstructionJson": str(directory / "anr_reconstruction.json"), "acceptance": str(directory / "acceptance.json")},
        "deliveryReadiness": _validate_group(directory, index, review_records),
    } for directory in directories])


def finalize_workspace_reports(input_path: str | Path, *, review_records=None) -> dict:
    """Refresh drafts then publish a content-bound per-group acceptance receipt."""
    from .reconstruction import refresh_reconstruction

    directories, index_path, index = _targets(input_path)
    outputs = []
    for directory in directories:
        initial = _validate_group(directory, index, review_records)
        fatal = {item["code"] for item in initial["blockers"]} & {"EVIDENCE_CHANGED", "GROUP_IDENTITY_MISMATCH", "INVALID_WORKSPACE_ARTIFACT", "MISSING_EVIDENCE_VERSION"}
        if not fatal:
            try:
                before_analysis = _read(directory / "anr_analysis.md")
                before_logcat = _read(directory / "logcat.txt")
                refresh_reconstruction(directory)
                if _read(directory / "anr_analysis.md") != before_analysis or _read(directory / "logcat.txt") != before_logcat:
                    raise ValueError("Evidence or analysis changed during reconstruction refresh.")
                if index_path:
                    index = _json(index_path)
                current = _validate_group(directory, index, review_records)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                current = initial
                _problem(current, "FINALIZE_REFRESH_FAILED", str(exc), str(directory))
        else:
            current = initial
        finish_result(current)
        snapshot = current.get("artifactDigests")
        if snapshot:
            try:
                _assert_snapshot(directory, snapshot)
            except (OSError, ValueError) as exc:
                _problem(current, "WORKSPACE_CHANGED_DURING_FINALIZE", str(exc), str(directory))
                finish_result(current)
        # Diagnostic failures may be saved too, but are never counted as deliverable.
        receipt = deepcopy(current)
        receipt["schemaVersion"] = "anr-report-acceptance-v1"
        if directory.is_dir() and not directory.is_symlink():
            _atomic_json(directory / "acceptance.json", receipt)
            if snapshot and current["status"] in DELIVERABLE_STATUSES:
                try:
                    _assert_snapshot(directory, snapshot)
                except (OSError, ValueError) as exc:
                    _problem(current, "WORKSPACE_CHANGED_DURING_FINALIZE", str(exc), str(directory))
                    finish_result(current)
                    _atomic_json(directory / "acceptance.json", {**deepcopy(current), "schemaVersion": "anr-report-acceptance-v1"})
                else:
                    current["receiptStatus"] = "current"
        outputs.append({"id": directory.name, "artifactPaths": {"acceptance": str(directory / "acceptance.json"), "analysis": str(directory / "anr_analysis.md"), "reconstruction": str(directory / "anr_reconstruction.md"), "reconstructionJson": str(directory / "anr_reconstruction.json")}, "deliveryReadiness": current})
    if index_path:
        latest = _json(index_path)
        by_id = {item["id"]: item for item in outputs}
        for group in latest.get("groups", []):
            if group.get("id") in by_id:
                output = by_id[group["id"]]
                group["deliveryReadiness"] = {key: deepcopy(output["deliveryReadiness"].get(key)) for key in ("status", "policyVersion", "analysisDigest", "evidenceRevision", "blockers", "reviewItems", "warnings")}
                group.setdefault("artifactPaths", {})["acceptance"] = output["artifactPaths"]["acceptance"]
        _atomic_json(index_path, latest)
    return _summary(outputs)
