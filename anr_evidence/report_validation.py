"""Deterministic candidate-report contracts, not a root-cause truth oracle."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json
import math
import re
from typing import Any

POLICY_VERSION = "report-acceptance-v1"
DELIVERABLE_STATUSES = frozenset({"ready", "ready_with_limitations"})
CONSERVATIVE_FLAGS = {"finalJudgment": False, "notRootCauseYet": True, "requiresHumanConfirmation": True}
ANALYSIS_SLOT_IDS = ("trace", "eventlog", "logcat-anrmanager", "final-anr")
FINAL_REPORT_SECTIONS = (
    "综合分析结论", "时间线", "Trace 证据分析", "EventLog 证据分析", "Logcat 与 AnrManager 证据分析",
    "直接阻塞点", "候选根因链", "超时输入事件", "ANR 发生模型", "模拟操作步骤", "证据质量", "修复建议",
)
CANDIDATE_FIELDS = ("candidateChains", "candidateConclusions", "inputEventAssessments", "anrModel", "simulationSteps")
_MODEL_FIELDS = frozenset({"inputEventAssessments", "anrModel", "simulationSteps"})
_REF_FIELDS = ("evidenceRefs", "supportingEventIds", "evidence", "supportingEvidenceRefs")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_SLOT = re.compile(r"^\s*<!-- AI_ANALYSIS_SLOT:([\w-]+) -->\s*$")
_PLACEHOLDER = re.compile(r"<[^<>\n]{1,160}>|(?:^|\s)(?:TODO|TBD)(?:\s|$)|待 AI 分析填写|_请用|_列出|_评估")


def content_digest(value: Any) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def strict_json_loads(text: str, *, max_bytes: int = 4 * 1024 * 1024) -> Any:
    """Reject duplicate keys, non-finite values, trailing data and oversized input."""
    if not isinstance(text, str) or len(text.encode("utf-8")) > max_bytes:
        raise ValueError(f"JSON must be UTF-8 text no larger than {max_bytes} bytes.")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def number(raw):
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError("Non-finite JSON number.")
        return value

    def reject(raw):
        raise ValueError(f"Invalid JSON constant: {raw}")

    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_float=number, parse_constant=reject)
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        return value
    except (RecursionError, OverflowError) as exc:
        raise ValueError("JSON nesting/number exceeds supported limits.") from exc


def markdown_records(text: str) -> tuple[list[dict], list[dict], bool]:
    """Return non-fenced records and complete blocks with original offsets."""
    records, blocks = [], []
    fence, language, body, offset, block_start = "", "", [], 0, 0
    for raw in text.splitlines(keepends=True):
        line = raw.rstrip("\r\n")
        match = _FENCE.match(line)
        if fence:
            if match and match[1][0] == fence[0] and len(match[1]) >= len(fence) and not match[2].strip():
                blocks.append({"language": language, "text": "".join(body), "start": block_start, "end": offset + len(raw)})
                fence, body = "", []
            else:
                body.append(raw)
        elif match:
            fence, language, block_start = match[1], match[2].strip().lower(), offset
        else:
            records.append({"text": line, "start": offset, "end": offset + len(raw)})
        offset += len(raw)
    return records, blocks, bool(fence)


def extract_analysis_slots(text: str) -> tuple[dict[str, str], list[dict]]:
    records, _, unterminated = markdown_records(text)
    markers = [(record, match[1]) for record in records if (match := _SLOT.fullmatch(record["text"]))]
    counts = Counter(slot for _, slot in markers)
    errors = []
    if unterminated:
        errors.append(issue("UNTERMINATED_FENCE", "存在未闭合代码围栏。", "analysis"))
    slots = {}
    boundaries = {"trace": "### EventLog 事件日志", "eventlog": "### Logcat 系统日志", "logcat-anrmanager": "### 完整性检查"}
    for slot in ANALYSIS_SLOT_IDS:
        if counts[slot] != 1:
            errors.append(issue("INVALID_ANALYSIS_SLOT", "每个分析槽位必须且只能出现一次。", f"slots.{slot}"))
            continue
        record = next(record for record, name in markers if name == slot)
        end = next((candidate["start"] for candidate in records if candidate["start"] > record["start"] and candidate["text"] == boundaries.get(slot)), len(text))
        next_marker = min((candidate["start"] for candidate, _ in markers if candidate["start"] > record["start"]), default=len(text))
        end = min(end, next_marker)
        slots[slot] = text[record["end"]:end].strip()
    for slot in counts.keys() - set(ANALYSIS_SLOT_IDS):
        errors.append(issue("UNKNOWN_ANALYSIS_SLOT", "出现未知分析槽位。", f"slots.{slot}"))
    return slots, errors


def analysis_digest(analyses: dict[str, str]) -> str:
    canonical = {}
    for slot, text in sorted(analyses.items()):
        records, blocks, _ = markdown_records(text)
        pieces = [(record["start"], record["text"].strip()) for record in records if record["text"].strip()]
        for block in blocks:
            body = block["text"]
            if block["language"] == "json":
                try:
                    body = json.dumps(strict_json_loads(body), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                except ValueError:
                    pass
            pieces.append((block["start"], block["language"] + "\n" + body.rstrip()))
        canonical[slot] = [value for _, value in sorted(pieces)]
    return content_digest(canonical)


def phase_evidence_binding(package: dict) -> dict:
    catalog = [{key: deepcopy(record.get(key)) for key in ("recordId", "sourceKind", "sourcePath", "rawSnippet")} for record in package.get("normalizedRecords", [])]
    identity = {"packageId": package.get("metadata", {}).get("packageId"), "anchor": deepcopy(package.get("anchors", {}).get("primaryAnchor"))}
    return {"evidenceCatalog": catalog, "evidenceIdentity": identity, "evidenceRevision": content_digest([identity, catalog])}


def refreshed_analysis_revision(report: dict, slots: dict[str, str]) -> dict:
    """Only a changed author revision explicitly acknowledging current evidence can rebind."""
    revision = deepcopy(report.get("analysisRevision", {}))
    current = analysis_digest(slots)
    if revision.get("status") in {"stale", "unknown"}:
        _, blocks, _ = markdown_records(slots.get("final-anr", ""))
        json_blocks = [block for block in blocks if block["language"] == "json"]
        try:
            payload = strict_json_loads(json_blocks[0]["text"]) if len(json_blocks) == 1 else None
        except ValueError:
            payload = None
        if (isinstance(payload, dict) and revision.get("analysisDigest")
                and revision["analysisDigest"] != current
                and payload.get("evidenceRevision") == report.get("evidenceRevision")
                and report.get("evidenceRevision")):
            return {"evidenceRevision": report["evidenceRevision"], "analysisDigest": current, "status": "unverified"}
    elif revision:
        revision["analysisDigest"] = current
        if slots and any(slot.strip() for slot in slots.values()):
            revision["status"] = "unverified"
    return revision


def issue(code: str, message: str, path: str) -> dict:
    return {"code": code, "message": message, "path": path, "issueId": content_digest([code, path])[:20]}


def new_result(profile: str, group_id: str | None = None) -> dict:
    return {
        "policyVersion": POLICY_VERSION, "profile": profile, "groupId": group_id,
        "status": "blocked", "blockers": [], "reviewItems": [], "warnings": [], "checks": {},
        "rootCauseConfirmed": False, "repairVerified": False,
        **CONSERVATIVE_FLAGS,
    }


def finish_result(result: dict) -> dict:
    result["status"] = (
        "blocked" if result["blockers"] else "needs_review" if result["reviewItems"]
        else "ready_with_limitations" if result["warnings"] else "ready"
    )
    return result


def _add(result, kind, code, message, path):
    item = issue(code, message, path)
    if not any(existing["issueId"] == item["issueId"] for existing in result[kind]):
        result[kind].append(item)


def _check_flags(value: dict, result: dict, path: str, *, require=False):
    for name, expected in CONSERVATIVE_FLAGS.items():
        if (require or name in value) and value.get(name) is not expected:
            _add(result, "blockers", "UNSAFE_CONSERVATIVE_FLAG", "候选报告必须保持保守标志。", f"{path}.{name}")
    for name in ("finalAdvice", "rootCauseConfirmed", "repairVerified"):
        if name in value and value[name] is not False:
            _add(result, "blockers", "UNSAFE_CONSERVATIVE_FLAG", "报告不能自行声明最终定责或修复验证。", f"{path}.{name}")
    if "tentative" in value and value["tentative"] is not True:
        _add(result, "blockers", "UNSAFE_CONCLUSION", "候选结论必须保持 tentative=true。", path + ".tentative")
    if value.get("executed") not in (None, False) or ("executed" in value and value["executed"] is not False):
        _add(result, "blockers", "SIMULATION_CLAIMED_EXECUTED", "候选模拟不能声明已执行。", path + ".executed")


def _nested_plan(value: Any, result: dict, path: str, events: list[dict] | None = None):
    if isinstance(value, dict):
        _check_flags(value, result, path)
        if "groupId" in value and value["groupId"] != result.get("groupId"):
            _add(result, "blockers", "GROUP_IDENTITY_MISMATCH", "嵌套候选不能跨组。", path + ".groupId")
        if events is not None:
            _check_refs(value, result, path, events)
            _check_timing(value, result, path, events)
        for name in ("phases", "actions", "preconditions", "captureChecks", "missingParameters", "expectedObservations", "uncertainties"):
            if name in value:
                _list(value[name], result, path + "." + name, strings=name not in {"phases", "actions"})
        for name in ("summary", "action", "expectedObservation"):
            if name in value:
                _text(value[name], result, path + "." + name)
        if "validation" in value and value["validation"] != "unverified":
            _add(result, "blockers", "UNSAFE_INCIDENT_CANDIDATE", "模型和模拟计划不能自报已验证。", path + ".validation")
        for key, child in value.items():
            _nested_plan(child, result, path + "." + key, events)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _nested_plan(child, result, f"{path}[{index}]", events)


def _check_timing(value: dict, result: dict, path: str, events: list[dict]):
    measured = [key for key in ("durationMs", "durationSeconds", "deltaTSeconds") if value.get(key) is not None]
    if not measured:
        return
    refs = [ref for field in _REF_FIELDS for ref in value.get(field, []) if isinstance(ref, str)] if all(isinstance(value.get(field, []), list) for field in _REF_FIELDS) else []
    selected = [event for event in events if event.get("eventId") in refs or event.get("rawLine") in refs]
    domains = {event.get("clockDomain") for event in selected if isinstance(event.get("clockDomain"), str)}
    known = selected and len(domains) == 1 and not any(domain.startswith(("unknown", "monotonic:unmapped")) for domain in domains) and all(event.get("timestamp") for event in selected)
    for field in measured:
        number = value[field]
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
            _add(result, "blockers", "INVALID_MEASURED_TIME", "时长或相对时间必须为有限数字或 null。", path + "." + field)
        elif not known or (field.startswith("duration") and (len(selected) < 2 or any(event.get("eventType") == "trace_snapshot" for event in selected))):
            _add(result, "blockers", "UNSUPPORTED_MEASURED_TIME", "未知/跨时钟或单一采样不能证明所声明的时间区间。", path + "." + field)
        elif field.startswith("duration"):
            _add(result, "reviewItems", "DURATION_REQUIRES_REVIEW", "可比较的两个时间点仍不能单独证明连续阻塞，需核对具体断言。", path + "." + field)


def _check_inventory(events: Any, result: dict, *, group_id=None) -> list[dict]:
    values = _list(events, result, "events")
    valid, seen = [], set()
    for index, event in enumerate(values):
        if not isinstance(event, dict):
            _add(result, "blockers", "INVALID_EVIDENCE_INVENTORY", "证据条目必须是对象。", f"events[{index}]")
            continue
        if group_id is not None and "groupId" in event and event["groupId"] != group_id:
            _add(result, "blockers", "GROUP_IDENTITY_MISMATCH", "观测条目不属于当前组。", f"events[{index}].groupId")
        for name in ("eventId", "entryId"):
            if name in event:
                value = event[name]
                if not isinstance(value, str) or not value or value in seen:
                    _add(result, "blockers", "AMBIGUOUS_EVIDENCE_ID", "观测身份为空、类型错误或重复。", f"events[{index}].{name}")
                else:
                    seen.add(value)
        if "recordRefs" in event:
            _list(event["recordRefs"], result, f"events[{index}].recordRefs", strings=True)
        valid.append(event)
    return valid


def _list(value: Any, result: dict, path: str, *, strings=False) -> list:
    if not isinstance(value, list):
        _add(result, "blockers", "INVALID_FIELD_TYPE", "字段必须是数组。", path)
        return []
    if strings and any(not isinstance(item, str) or not item.strip() for item in value):
        _add(result, "blockers", "INVALID_FIELD_TYPE", "字段必须是非空字符串数组。", path)
    return value


def _text(value: Any, result: dict, path: str, *, optional=False):
    if optional and value is None:
        return
    if not isinstance(value, str) or not value.strip() or value.strip() in {"...", "…"}:
        _add(result, "blockers", "MISSING_REPORT_CONTENT", "缺少正文或明确缺口。", path)
    elif _PLACEHOLDER.search(value):
        _add(result, "blockers", "TEMPLATE_CONTENT", "报告仍包含模板占位内容。", path)


def _evidence_index(events: list[dict]) -> tuple[set[str], Counter]:
    ids, raw = set(), Counter()
    for event in events:
        if not isinstance(event, dict):
            continue
        ids.update(value for key in ("eventId", "entryId", "recordRef", "recordId") if isinstance((value := event.get(key)), str))
        refs = event.get("recordRefs", [])
        if isinstance(refs, list):
            ids.update(value for value in refs if isinstance(value, str))
        if isinstance(event.get("rawLine"), str) and event["rawLine"]:
            raw[event["rawLine"]] += 1
    return ids, raw


def _check_refs(value: dict, result: dict, path: str, events: list[dict]):
    ids, raw = _evidence_index(events)
    for field in _REF_FIELDS:
        if field not in value:
            continue
        refs = _list(value[field], result, f"{path}.{field}", strings=True)
        for index, ref in enumerate(refs):
            if not isinstance(ref, str) or (ref not in ids and raw[ref] != 1):
                _add(result, "blockers", "INVALID_EVIDENCE_REF", "引用必须命中当前事故的真实 ID 或唯一完整原文。", f"{path}.{field}[{index}]")


def _check_candidates(payload: dict, result: dict, events: list[dict], *, require_models: bool):
    for field in CANDIDATE_FIELDS:
        if field not in payload:
            if require_models and field in {"anrModel", "simulationSteps", "inputEventAssessments", "candidateChains"}:
                _add(result, "blockers", "MISSING_REPORT_FIELD", "缺少必需候选字段。", field)
            continue
        values = _list(payload[field], result, field)
        if require_models and field in {"anrModel", "simulationSteps"} and not values:
            _add(result, "blockers", "EMPTY_INCIDENT_MODEL", "所有类型须有候选模型/计划或具体缺口与补证方案。", field)
        for index, item in enumerate(values):
            path = f"{field}[{index}]"
            if not isinstance(item, dict):
                _add(result, "blockers", "INVALID_FIELD_TYPE", "候选条目必须是对象。", path)
                continue
            text = next((item[key] for key in ("summary", "statement", "title", "rationale") if isinstance(item.get(key), str) and item[key].strip()), None)
            _text(text, result, path + ".summary")
            if "groupId" in item and item["groupId"] != result.get("groupId"):
                _add(result, "blockers", "GROUP_IDENTITY_MISMATCH", "候选条目不能跨 ANR 分组。", path + ".groupId")
            _check_flags(item, result, path)
            _check_refs(item, result, path, events)
            _check_timing(item, result, path, events)
            for key in ("limitations", "uncertainties", "evidenceGaps"):
                if key in item:
                    notes = _list(item[key], result, path + "." + key, strings=True)
                    if notes:
                        _add(result, "warnings", "DECLARED_CANDIDATE_LIMITATION", "候选已声明证据限制，交付必须保留。", path + "." + key)
            if field in _MODEL_FIELDS:
                _nested_plan(item, result, path, events)
                for name, expected in (("certainty", "inferred"), ("validation", "unverified"), ("executed", False), ("requiresHumanConfirmation", True)):
                    if item.get(name) != expected or (isinstance(expected, bool) and item.get(name) is not expected):
                        _add(result, "blockers", "UNSAFE_INCIDENT_CANDIDATE", "模型、事件评估和模拟步骤须保持未验证、未执行候选。", path + "." + name)
                _list(item.get("uncertainties"), result, path + ".uncertainties", strings=True)
                if not any(key in item for key in ("evidenceRefs", "supportingEventIds")):
                    _add(result, "blockers", "MISSING_EVIDENCE_FIELD", "需显式列出支撑引用；没有证据可使用空数组并说明缺口。", path)
                if not any(item.get(key) for key in _REF_FIELDS) and not item.get("uncertainties"):
                    _add(result, "blockers", "UNSUPPORTED_ASSERTION", "无支撑引用的候选必须说明未知与补证需求。", path)
            elif not any(item.get(key) for key in _REF_FIELDS):
                _add(result, "warnings", "UNSUPPORTED_CANDIDATE", "候选缺少支撑引用，不能作为已确认结论。", path)


def _common_payload(payload: dict, result: dict, events: list[dict], *, models=True):
    _check_flags(payload, result, "report", require=True)
    _check_candidates(payload, result, events, require_models=models)
    anr_type = payload.get("anrType")
    if not isinstance(anr_type, (str, type(None))):
        _add(result, "blockers", "ANR_TYPE_MISMATCH", "ANR 类型须为字符串。", "anrType")
    elif anr_type not in {None, "input_dispatching_timeout", "no_focus_window"} and payload.get("inputEventAssessments"):
        _add(result, "blockers", "INPUT_ASSESSMENT_TYPE_MISMATCH", "非输入类型和 unknown 不得编造输入事件评估。", "inputEventAssessments")
    _type_policy(payload, result, anr_type)
    for key in ("evidenceGaps", "globalLimitations"):
        if key in payload:
            values = _list(payload[key], result, key, strings=True)
            if values:
                _add(result, "warnings", "DECLARED_EVIDENCE_GAP", "存在已披露的证据缺口，报告仅作候选分析。", key)
    requests = payload.get("reviewRequests", [])
    for index, item in enumerate(_list(requests, result, "reviewRequests")):
        if not isinstance(item, dict) or not isinstance(item.get("summary"), str):
            _add(result, "blockers", "INVALID_REVIEW_REQUEST", "复核请求须指定具体断言和摘要。", f"reviewRequests[{index}]")
        else:
            _check_refs(item, result, f"reviewRequests[{index}]", events)
            _add(result, "reviewItems", "CLAIM_REQUIRES_REVIEW", item["summary"], f"reviewRequests[{index}]")
    for index, event in enumerate(_list(payload.get("events", []), result, "events")):
        if not isinstance(event, dict):
            _add(result, "blockers", "INVALID_FIELD_TYPE", "AI 事件候选须为对象。", f"events[{index}]")
        else:
            _check_flags(event, result, f"events[{index}]")
            _check_refs(event, result, f"events[{index}]", events)
            if event.get("certainty") == "observed":
                _add(result, "blockers", "AI_EVENT_CLAIMED_OBSERVED", "AI 不能创建新的观测事件。", f"events[{index}]")


def _type_policy(value: Any, result: dict, anr_type: str | None, path="report"):
    if isinstance(value, list):
        for index, child in enumerate(value):
            _type_policy(child, result, anr_type, f"{path}[{index}]")
    elif isinstance(value, dict):
        mechanism = value.get("mechanism")
        if anr_type == "no_focus_window" and mechanism in ("input_ack_timeout", "published_ack_wait"):
            _add(result, "blockers", "INPUT_BRANCH_MISMATCH", "无焦点窗口分支是等待投递，不是已发布事件的 ACK 等待。", path + ".mechanism")
        if value.get("nativeSeq") is not None and value.get("seqDomain", "native") != "native":
            _add(result, "blockers", "INPUT_SEQUENCE_DOMAIN_MISMATCH", "native seq 不能使用 Java 或日志序号域。", path + ".seqDomain")
        if anr_type == "job_scheduler_timeout" and value.get("completionEvidence") == "jobFinished" and value.get("provesCallbackReturned") is True:
            _add(result, "blockers", "JOB_COMPLETION_NOT_CALLBACK_RETURN", "jobFinished 不能证明 onStartJob/onStopJob 按时返回。", path + ".provesCallbackReturned")
        for key, child in value.items():
            _type_policy(child, result, anr_type, path + "." + key)


def validate_workspace_content(text: str, reconstruction: dict, *, expected_group_id: str | None = None) -> dict:
    result = new_result("workspace", expected_group_id or (reconstruction.get("groupId") if isinstance(reconstruction, dict) else None))
    if not isinstance(text, str) or not isinstance(reconstruction, dict) or not reconstruction:
        _add(result, "blockers", "INVALID_WORKSPACE_CONTENT", "分析须为文本，重建须为非空对象。", "workspace")
        return finish_result(result)
    slots, errors = extract_analysis_slots(text)
    result["blockers"].extend(errors)
    result["analysisDigest"] = analysis_digest(slots)
    if reconstruction.get("groupId") != result["groupId"]:
        _add(result, "blockers", "GROUP_IDENTITY_MISMATCH", "重建身份与当前目录不符。", "groupId")
    for slot in ANALYSIS_SLOT_IDS:
        body = slots.get(slot, "")
        _text(body, result, "slots." + slot)
    final = slots.get("final-anr", "")
    records, blocks, _ = markdown_records(final)
    headings = [(record, match[1]) for record in records if (match := re.match(r"^##\s+(.+?)\s*#*\s*$", record["text"]))]
    for title in FINAL_REPORT_SECTIONS:
        matches = [record for record, name in headings if name == title]
        if len(matches) != 1:
            _add(result, "blockers", "MISSING_REPORT_SECTION", "必需章节必须出现一次，围栏中的标题不计。", "sections." + title)
            continue
        record = matches[0]
        end = next((other["start"] for other, _ in headings if other["start"] > record["start"]), len(final))
        _text(final[record["end"]:end].strip(), result, "sections." + title)
    json_blocks = [block for block in blocks if block["language"] == "json"]
    if len(json_blocks) != 1:
        _add(result, "blockers", "INVALID_REPORT_JSON", "最终槽位须有且仅有一个结构化 JSON 尾部。", "finalJson")
        return finish_result(result)
    block = json_blocks[0]
    if final[block["end"]:].strip():
        _add(result, "blockers", "INVALID_REPORT_JSON", "结构化 JSON 必须位于最终槽位末尾。", "finalJson")
    try:
        payload = strict_json_loads(block["text"])
    except ValueError as exc:
        _add(result, "blockers", "INVALID_REPORT_JSON", str(exc), "finalJson")
        return finish_result(result)
    if not isinstance(payload, dict) or not payload:
        _add(result, "blockers", "EMPTY_REPORT", "报告 JSON 不能为空对象。", "finalJson")
        return finish_result(result)
    if payload.get("groupId", result["groupId"]) != result["groupId"]:
        _add(result, "blockers", "GROUP_IDENTITY_MISMATCH", "AI 报告不能引用另一个事故分组。", "groupId")
    from .anr_strategy import ANR_TYPE_STRATEGIES

    if not isinstance(payload.get("anrType"), str) or payload["anrType"] not in ANR_TYPE_STRATEGIES or payload["anrType"] != reconstruction.get("anrType"):
        _add(result, "blockers", "ANR_TYPE_MISMATCH", "报告类型须与当前组的有效类型一致。", "anrType")
    _text(payload.get("primaryRootCauseDescription"), result, "primaryRootCauseDescription")
    if "primaryRootCauseHintId" not in payload:
        _add(result, "blockers", "MISSING_REPORT_FIELD", "缺少主提示字段，未知可为 null。", "primaryRootCauseHintId")
    hints = _list(payload.get("supportingHintIds"), result, "supportingHintIds", strings=True)
    hint_inventory = _list(reconstruction.get("rootCausePatternHints", []), result, "rootCausePatternHints", strings=True)
    source_hints = _list(reconstruction.get("sourceHintIds", []), result, "sourceHintIds", strings=True)
    allowed_hints = {hint for hint in [*hint_inventory, *source_hints] if isinstance(hint, str)}
    for hint in [payload.get("primaryRootCauseHintId"), *hints]:
        if hint is not None and (not isinstance(hint, str) or hint not in allowed_hints):
            _add(result, "blockers", "INVALID_HINT_REF", "提示必须来自当前组的已提取证据。", "supportingHintIds")
    for field in ("blockingThread", "ownerThread"):
        value = payload.get(field)
        if not isinstance(value, dict) or any(key not in value or (value[key] is not None and not isinstance(value[key], str)) for key in ("tid", "name", "frame")):
            _add(result, "blockers", "INVALID_FIELD_TYPE", "线程须包含 tid/name/frame，未知用 null。", field)
    sources = payload.get("sourceAnalyses")
    events = _check_inventory(reconstruction.get("events"), result, group_id=result["groupId"])
    if not isinstance(sources, dict):
        _add(result, "blockers", "MISSING_SOURCE_ANALYSES", "缺少三来源结构化分析。", "sourceAnalyses")
    else:
        for name in ("trace", "eventLog", "logcat"):
            source = sources.get(name)
            if not isinstance(source, dict):
                _add(result, "blockers", "MISSING_SOURCE_ANALYSES", "每个来源须有结论或明确缺口。", "sourceAnalyses." + name)
                continue
            _text(source.get("summary"), result, f"sourceAnalyses.{name}.summary")
            refs = _list(source.get("keyEvidence"), result, f"sourceAnalyses.{name}.keyEvidence", strings=True)
            gaps = _list(source.get("gaps"), result, f"sourceAnalyses.{name}.gaps", strings=True)
            _check_refs({"evidenceRefs": refs}, result, f"sourceAnalyses.{name}", events)
            if not refs and not gaps:
                _add(result, "blockers", "EMPTY_SOURCE_ANALYSIS", "没有证据时必须明确说明来源缺口。", "sourceAnalyses." + name)
    _list(payload.get("remediationSuggestions"), result, "remediationSuggestions", strings=True)
    _list(payload.get("evidenceGaps"), result, "evidenceGaps", strings=True)
    _common_payload(payload, result, events)
    completeness = reconstruction.get("evidenceCompleteness", {})
    if not isinstance(completeness, dict) or type(completeness.get("complete", False)) is not bool:
        _add(result, "blockers", "INVALID_EVIDENCE_COMPLETENESS", "证据完整性须包含布尔状态。", "evidenceCompleteness")
        completeness = {}
    if not completeness.get("complete", False):
        if payload.get("evidenceGaps"):
            _add(result, "warnings", "INCOMPLETE_EVIDENCE", "提取证据不完整，报告已声明限制。", "evidenceCompleteness")
        else:
            _add(result, "blockers", "UNDISCLOSED_EVIDENCE_GAP", "原始证据缺失须在报告中披露。", "evidenceGaps")
    result["checks"].update({name: "fail" if result["blockers"] else "pass" for name in ("structure", "identityAndRefs", "conservativePolicy")})
    return finish_result(result)


def validate_manager_report(report: dict, *, groups: list[dict], pending_probe: dict | None = None, stop_reason: str | None = None) -> dict:
    result = new_result("manager")
    if not isinstance(report, dict) or not report:
        _add(result, "blockers", "EMPTY_REPORT", "Manager 未提供非空报告对象。", "report")
        return finish_result(result)
    if not isinstance(groups, list) or not groups or any(not isinstance(group, dict) or not isinstance(group.get("id"), str) or not group["id"] or not isinstance(group.get("reconstruction"), dict) for group in groups):
        _add(result, "blockers", "INVALID_GROUP_INVENTORY", "须提供有唯一身份和重建的事故分组。", "groups")
        return finish_result(result)
    if len({group["id"] for group in groups}) != len(groups):
        _add(result, "blockers", "INVALID_GROUP_INVENTORY", "事故分组身份不能重复。", "groups")
        return finish_result(result)
    for group in groups:
        if group["reconstruction"].get("groupId", group["id"]) != group["id"]:
            _add(result, "blockers", "GROUP_IDENTITY_MISMATCH", "分组与重建身份不一致。", "groups." + group["id"])
    try:
        content_digest(report)
    except (ValueError, TypeError, RecursionError):
        _add(result, "blockers", "INVALID_REPORT_JSON", "报告包含非有限或不支持的 JSON 值。", "report")
        return finish_result(result)
    for field in ("candidateChains", "candidateConclusions", "remediationDrafts", "globalLimitations"):
        if field not in report:
            _add(result, "blockers", "MISSING_REPORT_FIELD", "缺少 Manager 报告必需字段。", field)
        else:
            _list(report[field], result, field, strings=field == "globalLimitations")
    if len(groups) > 1:
        scoped = report.get("groups")
        if not isinstance(scoped, list) or any(not isinstance(item, dict) or not isinstance(item.get("groupId"), str) for item in scoped) or {item["groupId"] for item in scoped} != {group["id"] for group in groups} or len(scoped) != len(groups):
            _add(result, "blockers", "UNSCOPED_MULTI_ANR_REPORT", "多 ANR 输出须为各组提供唯一显式 groupId 报告。", "groups")
        else:
            for group in groups:
                child = next(item for item in scoped if item["groupId"] == group["id"])
                child_result = validate_manager_report(child, groups=[group])
                for kind in ("blockers", "reviewItems", "warnings"):
                    result[kind].extend(child_result[kind])
        for field in (*CANDIDATE_FIELDS, "remediationDrafts"):
            for index, candidate in enumerate(report.get(field, []) if isinstance(report.get(field, []), list) else []):
                if not isinstance(candidate, dict) or not isinstance(candidate.get("groupId"), str) or candidate["groupId"] not in {group["id"] for group in groups}:
                    _add(result, "blockers", "UNSCOPED_MULTI_ANR_REPORT", "聚合候选也必须显式归属到单一事故。", f"{field}[{index}]")
                else:
                    target = next(group for group in groups if group["id"] == candidate["groupId"])
                    local = new_result("manager", target["id"])
                    _check_candidates({field: [candidate]}, local, target["reconstruction"].get("events", []), require_models=False)
                    for kind in ("blockers", "reviewItems", "warnings"):
                        result[kind].extend(local[kind])
        _check_flags(report, result, "report", require=True)
    else:
        group = groups[0] if groups else {}
        result["groupId"] = group.get("id")
        if report.get("groupId", group.get("id")) != group.get("id"):
            _add(result, "blockers", "GROUP_IDENTITY_MISMATCH", "Manager 报告不属于当前组。", "groupId")
        reconstruction = group.get("reconstruction", {})
        events = _check_inventory(reconstruction.get("events"), result, group_id=group["id"])
        if "anrType" in report and report["anrType"] != reconstruction.get("anrType"):
            _add(result, "blockers", "ANR_TYPE_MISMATCH", "Manager 报告类型与事故不符。", "anrType")
        effective = {**report, "anrType": reconstruction.get("anrType", report.get("anrType"))}
        _common_payload(effective, result, events)
        if not any(report.get(field) for field in ("candidateChains", "candidateConclusions", "globalLimitations")):
            _add(result, "blockers", "EMPTY_REPORT", "报告须有候选结论或具体证据不足说明。", "report")
    for index, item in enumerate(report.get("remediationDrafts", []) if isinstance(report.get("remediationDrafts"), list) else []):
        if not isinstance(item, dict) or item.get("requiresHumanConfirmation") is not True:
            _add(result, "blockers", "UNSAFE_REMEDIATION", "修复建议必须保持人工确认。", f"remediationDrafts[{index}]")
        elif len(groups) == 1:
            if "groupId" in item and item["groupId"] != groups[0]["id"]:
                _add(result, "blockers", "GROUP_IDENTITY_MISMATCH", "修复建议不能跨事故。", f"remediationDrafts[{index}].groupId")
            _check_refs(item, result, f"remediationDrafts[{index}]", groups[0]["reconstruction"].get("events", []))
    if pending_probe:
        _add(result, "reviewItems", "UNRESOLVED_REPROBE", "存在未执行补探请求；撤回依赖断言或复核后再交付。", "reProbeRequest")
    result["stopReason"] = stop_reason
    result["checks"] = {"structure": "fail" if result["blockers"] else "pass", "rootCauseTruth": "not_assessed"}
    return finish_result(result)


def validate_phase_report(package: dict) -> dict:
    """Thin Phase 7 adapter; carried snippets are provenance, not four AI slots."""
    result = new_result("phase7")
    if not isinstance(package, dict):
        _add(result, "blockers", "INVALID_PHASE_REPORT", "Phase 报告必须是对象。", "report")
        return finish_result(result)
    try:
        content_digest(package)
    except (ValueError, TypeError, RecursionError):
        _add(result, "blockers", "INVALID_PHASE_REPORT", "Phase 包包含非有限、循环或不支持的数据。", "report")
        return finish_result(result)
    for name, expected in (("metadata", dict), ("classification", dict), ("anchors", dict), ("candidateChains", list), ("candidateConclusions", list), ("remediationDrafts", list), ("globalLimitations", list), ("warnings", list)):
        if not isinstance(package.get(name), expected):
            _add(result, "blockers", "INVALID_PHASE_REPORT", "Phase 报告字段缺失或类型不符。", name)
    if result["blockers"]:
        return finish_result(result)
    metadata = package["metadata"]
    if metadata.get("phase") != "phase7-remediation-draft" or not isinstance(metadata.get("packageId"), str):
        _add(result, "blockers", "INVALID_PHASE_REPORT", "仅接受有包身份的 Phase 7 交付候选。", "metadata")
    if metadata.get("finalAdvice") is not False:
        _add(result, "blockers", "UNSAFE_CONSERVATIVE_FLAG", "Phase 交付必须保持 finalAdvice=false。", "metadata.finalAdvice")
    _check_flags(metadata, result, "metadata")
    trusted_carried = any(key in package for key in ("evidenceCatalog", "timeline", "normalizedRecords"))
    events = []
    for field in (("evidenceCatalog",) if "evidenceCatalog" in package else ("timeline", "normalizedRecords")):
        if field in package:
            for entry in _list(package[field], result, field):
                if isinstance(entry, dict):
                    events.append(entry)
    if "evidenceCatalog" in package:
        identity = {"packageId": metadata.get("packageId"), "anchor": deepcopy(package["anchors"].get("primaryAnchor"))}
        if package.get("evidenceIdentity") != identity or package.get("evidenceRevision") != content_digest([identity, package["evidenceCatalog"]]):
            _add(result, "blockers", "PHASE_EVIDENCE_CHANGED", "Phase 证据清单或事故身份与绑定摘要不一致。", "evidenceRevision")
    result["analysisDigest"] = content_digest({key: value for key, value in package.items() if key not in {"deliveryReadiness", "acceptance"}})
    result["evidenceRevision"] = package.get("evidenceRevision")
    top = package.get("topConclusion")
    if top is not None:
        if not isinstance(top, dict):
            _add(result, "blockers", "INVALID_PHASE_REPORT", "最高结论必须是对象或 null。", "topConclusion")
    for field in ("candidateChains", "candidateConclusions", "remediationDrafts"):
        for index, item in enumerate(package[field]):
            if not isinstance(item, dict):
                _add(result, "blockers", "INVALID_PHASE_REPORT", "候选条目必须是对象。", f"{field}[{index}]")
                continue
            snippets = item.get("evidenceSnippets", item.get("supportingSnippets", []))
            for snippet in _list(snippets, result, f"{field}[{index}].snippets"):
                if isinstance(snippet, dict):
                    if not trusted_carried:
                        events.append({"recordId": snippet.get("recordRef") or snippet.get("recordId"), "rawLine": snippet.get("rawLine") or snippet.get("rawSnippet") or ""})
    if not trusted_carried:
        _add(result, "warnings", "LEGACY_SNIPPET_PROVENANCE", "旧 Phase 包仅携带片段，无法独立回查全部原始记录。", "evidenceIndex")
    _check_candidates(package, result, events, require_models=False)
    if isinstance(top, dict):
        _check_flags(top, result, "topConclusion")
        _check_refs(top, result, "topConclusion", events)
        if top.get("tentative") is not True:
            _add(result, "blockers", "UNSAFE_CONCLUSION", "最高结论必须保持 tentative=true。", "topConclusion.tentative")
    for index, item in enumerate(package["remediationDrafts"]):
        if not isinstance(item, dict):
            continue
        _check_refs(item, result, f"remediationDrafts[{index}]", events)
        if item.get("requiresHumanConfirmation") is not True:
            _add(result, "blockers", "UNSAFE_REMEDIATION", "修复草案须人工确认。", f"remediationDrafts[{index}]")
    for index, conclusion in enumerate(package["candidateConclusions"]):
        if isinstance(conclusion, dict) and conclusion.get("tentative") is not True:
            _add(result, "blockers", "UNSAFE_CONCLUSION", "Phase 结论必须保持 tentative=true。", f"candidateConclusions[{index}]")
    if package["globalLimitations"] or metadata.get("status") != "complete" or package["warnings"]:
        _add(result, "warnings", "PHASE_REPORT_LIMITATIONS", "确定性候选报告仍有全局限制或来源缺口。", "globalLimitations")
    scenario = package.get("incidentScenario")
    if isinstance(scenario, dict) and scenario:
        _check_flags(scenario, result, "incidentScenario", require=True)
        if scenario.get("certainty") != "inferred" or scenario.get("validation") != "unverified" or scenario.get("executed") is not False:
            _add(result, "blockers", "UNSAFE_INCIDENT_CANDIDATE", "公共事故模型须保持未执行候选。", "incidentScenario")
        _nested_plan(scenario.get("simulationSteps", []), result, "incidentScenario.simulationSteps")
    result["checks"] = {"structure": "fail" if result["blockers"] else "pass", "rootCauseTruth": "not_assessed"}
    return finish_result(result)
