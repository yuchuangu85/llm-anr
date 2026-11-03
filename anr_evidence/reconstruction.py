"""Evidence-bound incident reconstruction, distinct from analysis-run replay."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from .am_anr import package_name_from_am_anr_line
from .log_filter import TIMESTAMP_RE, iter_text_lines, parse_log_timestamp
from .reconstruction_ai import attach_ai_analysis
from .time_norm import parse_record_timestamp as _record_time, record_clock_domain as _clock_domain
from .trace_preprocessor import extract_trace_threads

SCHEMA_VERSION = "anr-reconstruction-v1"
SOURCE_KEYS = (
    ("trace", "trace"), ("eventLog", "event_log"), ("logcat", "logcat"),
    ("anrManager", "logcat"), ("kernelLog", "kernel_log"), ("meminfo", "meminfo"),
)
_COMMON_GAPS = (
    "日志窗口及关键词过滤之外的事件无法恢复；来源非空不代表过程完整。",
    "时间先后不等于因果；同时间戳事件不假定执行顺序。",
    "尚未校准 wall-clock、uptime/boottime 或不同显式时区；不同 clockDomain 不计算先后或跨域 ΔT。",
    "未提供年份的 Android 时间戳沿用有年份的锚点，否则使用解析器默认年份；跨年或时钟跳变仍需设备映射。",
    "Trace 仅证明采样现场，不证明该状态覆盖此前整个等待区间。",
    "sourcePath 的行号基于加载后的逻辑来源；多个归档分片可能合并，不能当作某个物理分片的行号。",
    "同一采集窗口内可能有其他进程/显示屏的共现日志；没有身份关联的观察不能自动归属于目标 ANR。",
)
_EVENT_RULES = (
    ("anr_reported", r"\bam_anr\b", "记录 ANR 上报"),
    ("pre_anr_warning", r"notifyPreNoFocusedWindowAnr|notifyPreAppUnresponsive|pre[-_ ]ANR", "记录 pre-ANR 预警"),
    ("anr_reported", r"\bANR in\b", "记录 ANR 上报"),
    ("focus_wait_started", r"Waiting because no window has focus", "开始等待焦点窗口"),
    ("focus_wait_reset", r"resetNoFocusedWindowTimeoutLocked|Resetting ANR timeouts", "清除无焦点等待状态"),
    ("focused_application", r"setFocusedApplication|focusedApplication\s*[=:]|focused app(?:lication)? changed", "记录焦点应用身份"),
    ("focus_granted", r"native newFocus\s*=|Focus entering|FocusResolver.*(?:focus granted|new focus)", "记录实际输入焦点变化"),
    ("focus_request", r"Focus request(?:ed)?\b|requestFocus\b|setFocusedWindow\b", "请求窗口焦点（不代表已授予）"),
    ("window_ineligible", r"\bNOT_VISIBLE\b|\bNOT_FOCUSABLE\b|\bNO_WINDOW\b", "记录窗口焦点资格状态"),
    ("window_drawn", r"reportDrawFinished|finishDrawingWindow|\bfinishDrawing\b|firstWindowDrawn|\ballDrawn\b", "记录窗口绘制完成（不代表获焦）"),
    ("activity_resume", r"wm_on_resume_called|wm_set_resumed_activity|wm_on_top_resumed_gained_called", "记录 Activity resume 状态"),
    ("focus_change", r"\binput_focus\b|\bwm_focus\b", "记录窗口焦点日志"),
    ("process_start", r"\bam_proc_start\b|\bStart proc\b", "记录进程启动"),
    ("process_died", r"\bam_proc_died\b|\bam_kill\b|\bKilling \d+", "记录进程退出或被杀"),
    ("recents_finish", r"finishRecentsAnimation|finishRunningRecentsAnimation|cleanUpRecentsAnimation", "记录 Recents finish/cleanup 调用"),
    ("recents_start", r"startRecentsAnimation|onAnimationStart|onTasksAppeared", "记录 Recents 启动或回调"),
    ("input_ack_timeout", r"has not finished processing|connection.*not responding|waitQueueLength", "记录已分发输入等待完成的信号"),
    ("no_focus_signal", r"does not have a focused window|no focused window|no focus window", "记录无焦点窗口信号"),
    ("anr_dump", r"AnrManager|dumpStackTraces|startAnrDump|Completed ANR", "记录 ANR 诊断采样或处理日志"),
    ("binder_signal", r"\bbinder\b|BinderProxy|IPCThreadState", "记录 Binder 相关信号"),
    ("input_event", r"\bACTION_DOWN\b|\bACTION_UP\b|dispatchKey|\bKeyEvent\b|\binput_interaction\b", "记录输入事件信号"),
)
_ENTITY_PATTERNS = {
    "displayId": r"\bdisplay(?:Id)?\s*[=: ]\s*(\d+)",
    "applicationToken": r"\b(?:applicationToken|appToken)\s*[=:]\s*([^\s,;]+)",
    "windowToken": r"\b(?:windowToken|focusToken)\s*[=:]\s*([^\s,;]+)",
    "inputEventId": r"\beventId\s*[=:]\s*([^\s,;]+)",
    "gestureId": r"\b(?:gestureId|logId)\s*[=:]\s*([^\s,;]+)",
    "handler": r"\b(?:handler|h)\s*[=:]\s*([^\s,;]+)",
    "pid": r"\bpid\s*[=: ]\s*(\d+)",
    "artTid": r"\btid\s*[=: ]\s*(\d+)",
    "sysTid": r"\bsysTid\s*[=: ]\s*(\d+)",
}


def _event_entities(line: str) -> dict[str, str]:
    entities = {name: match.group(1) for name, pattern in _ENTITY_PATTERNS.items() if (match := re.search(pattern, line))}
    threadtime = re.search(r"\d{2}:\d{2}:\d{2}(?:\.\d+)?\s+(?:(\d+)\s+)?(\d+)\s+(\d+)\s+[VDIWEF]\b", line)
    if threadtime:
        entities.update({"emitterPid": threadtime.group(2), "emitterTid": threadtime.group(3)})
        if threadtime.group(1):
            entities["emitterUid"] = threadtime.group(1)
    process = package_name_from_am_anr_line(line)
    if process:
        entities["anrProcess"] = process
    return entities


def index_source_locations(package: dict[str, Any], groups: list[dict[str, Any]]) -> dict[tuple[str, str], list[int]]:
    """Index only retained lines, once per loaded source, without copying full logs."""
    wanted: dict[str, set[str]] = defaultdict(set)
    for group in groups:
        for key, kind in SOURCE_KEYS:
            wanted[kind].update(line.strip() for line in group.get(key, {}).get("lines", []) if line.strip())
    locations: dict[tuple[str, str], list[int]] = {}
    for kind, selected in wanted.items():
        for number, line in enumerate(iter_text_lines(package.get("sources", {}).get(kind, {}).get("content", "")), 1):
            text = line.strip()
            if text in selected:
                positions = locations.setdefault((kind, text), [])
                # Two matches suffice to establish ambiguity; do not retain an unbounded index.
                if len(positions) < 2:
                    positions.append(number)
    return locations


def _classify(line: str) -> tuple[str, str, dict[str, str] | None]:
    if re.search(r"native newFocus\s*=\s*(?:null|none|0x0)\b", line, re.IGNORECASE):
        return "focus_change", "记录输入焦点清空（不是焦点恢复）", {"nativeFocus": "cleared"}
    if re.search(r"\b(?:NOT_VISIBLE|NOT_FOCUSABLE|allDrawn|firstWindowDrawn)\s*[=:]\s*(?:false|0)\b", line, re.IGNORECASE):
        return "log_observation", "记录窗口布尔状态（不扩展为绘制完成或焦点资格结论）", None
    for kind, pattern, summary in _EVENT_RULES:
        if re.search(pattern, line, re.IGNORECASE):
            state = {
                "focus_wait_started": {"noFocusWait": "waiting"},
                "focus_wait_reset": {"noFocusWait": "reset"},
                "focus_request": {"focusRequest": "requested"},
                "focus_granted": {"nativeFocus": "changed"},
                "window_drawn": {"drawing": "reported_finished"},
                "anr_reported": {"anr": "reported"},
            }.get(kind)
            if kind == "window_ineligible":
                match = re.search(r"\b(NOT_VISIBLE|NOT_FOCUSABLE|NO_WINDOW)\b", line)
                state = {"focusEligibility": match.group(1)} if match else None
            return kind, summary, state
    return "log_observation", "保留的日志观察", None


def _artifact_locations(texts: dict[str, str]) -> dict[tuple[str, str, str], list[int]]:
    locations: dict[tuple[str, str, str], list[int]] = {}
    titles = {"Trace 堆栈": "trace", "EventLog 事件日志": "event_log", "Logcat 系统日志": "logcat", "Meminfo 目标/高负载跟进": "meminfo"}
    for name, text in texts.items():
        evidence = [("logcat", number, line) for number, line in enumerate(iter_text_lines(text), 1)] if name == "logcat.txt" else [
            (titles[title], number, line)
            for title, block in _workspace_blocks(text).items() for number, line in block
        ]
        for kind, number, line in evidence:
            positions = locations.setdefault((name, kind, line.strip()), [])
            if len(positions) < 2:
                positions.append(number)
    return locations


def _reference(kind: str, line: str, number: int, path: str, locations: dict, artifact_locations: dict) -> dict[str, Any]:
    positions = locations.get((kind, line.strip()), [])
    artifact = "logcat.txt" if kind == "logcat" else "anr_analysis.md"
    artifact_positions = artifact_locations.get((artifact, kind, line.strip()), [])
    return {
        "sourceKind": kind, "sourcePath": path,
        "lineNumber": positions[0] if len(positions) == 1 else None,
        "sourceLineCandidates": positions if len(positions) > 1 else [],
        "coordinateSystem": "loaded-source-content",
        "extractedLineNumber": number,
        "artifactPath": artifact if artifact_positions else None,
        "artifactLineNumber": artifact_positions[0] if len(artifact_positions) == 1 else None,
        "content": line,
    }


def build_reconstruction(
    group: dict[str, Any], *, source_paths: dict[str, str] | None = None,
    source_locations: dict | None = None, artifact_texts: dict[str, str] | None = None,
    ai_analyses: dict[str, str] | None = None, ai_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build observed events without inventing timestamps, causality or missing states."""
    paths, locations, artifacts = source_paths or {}, source_locations or {}, artifact_texts or {}
    artifact_locations = _artifact_locations(artifacts)
    anchor = deepcopy(group.get("anchor"))
    anchor_line = (anchor or {}).get("line") or (anchor or {}).get("timestamp", "")
    anchor_dt, anchor_time_line = _record_time(anchor_line)
    if anchor_dt is None:
        anchor_dt = parse_log_timestamp((anchor or {}).get("timestamp", ""))
        anchor_time_line = (anchor or {}).get("timestamp", "")
    anchor_domain = _clock_domain(anchor_time_line, anchor_dt)
    group_id = group["id"]
    events: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for key, kind in SOURCE_KEYS:
        section = group.get(key, {})
        lines = section.get("lines", [])
        if not lines:
            continue
        if kind == "trace":
            lines = [line for line in lines if "HINT[" not in line]
            observations = [(1, "\n".join(lines))] if lines else []
        else:
            observations = list(enumerate(lines, 1))
        occurrences: dict[str, int] = {}
        for number, line in observations:
            occurrence = occurrences.get(line, 0)
            occurrences[line] = occurrence + 1
            if not line.strip() or (kind, line, occurrence) in seen:
                continue
            seen.add((kind, line, occurrence))
            parsed, time_line = _record_time(line, year=anchor_dt.year if anchor_dt else None, trace=kind == "trace")
            domain = _clock_domain(time_line, parsed)
            comparable = parsed is not None and anchor_dt is not None and domain == anchor_domain
            delta = round((parsed - anchor_dt).total_seconds(), 6) if comparable else None
            event_type, summary, state = _classify(line)
            if kind == "trace":
                event_type, summary, state = "trace_snapshot", "Trace 采样快照（不是连续阻塞区间）", None
            entities = _event_entities(line)
            refs = [_reference(kind, raw, number + offset, paths.get(kind, kind), locations, artifact_locations) for offset, raw in enumerate(line.splitlines()) if raw.strip()]
            digest = sha256(f"{group_id}|{kind}|{paths.get(kind, kind)}|{line}|{occurrence}".encode()).hexdigest()[:16]
            timeout = re.search(r"Will wait for\s+(\d+)\s*ms", line, re.IGNORECASE)
            events.append({
                "eventId": f"evt-{digest}", "sourceKind": kind, "eventType": event_type,
                "timestamp": parsed.isoformat(timespec="microseconds") if parsed else None,
                "clockDomain": domain, "deltaTSeconds": delta,
                "timeBasis": "trace_snapshot" if kind == "trace" and parsed else "log_timestamp" if parsed else "unknown",
                "summary": summary, "rawLine": line, "entities": entities,
                "stateBefore": None, "stateAfter": state, "certainty": "observed",
                "configuredTimeoutMs": int(timeout.group(1)) if timeout else None,
                "evidenceRefs": refs,
            })
    events.sort(key=lambda event: (event["clockDomain"], event["timestamp"] is None, event["timestamp"] or "", event["eventId"]))
    relations = []
    previous: dict[str, dict[str, Any]] = {}
    for event in events:
        if event["timestamp"] is None:
            continue
        prior = previous.get(event["clockDomain"])
        if prior and prior["timestamp"] < event["timestamp"]:
            relations.append({"fromEventId": prior["eventId"], "toEventId": event["eventId"], "kind": "precedes", "certainty": "observed", "causal": False})
        previous[event["clockDomain"]] = event
    gaps = list(_COMMON_GAPS)
    if not anchor_dt:
        gaps.append("缺少可解析的 ANR 锚点，所有相对 ANR 时间均为未知。")
    if group.get("fallbackUsed"):
        gaps.append("使用 fallback 锚点；它不是已验证的实际触发时间。")
    if any(event["timestamp"] is None for event in events):
        gaps.append("存在无法定时的证据，独立列为时间未知，不回填 ANR 时刻。")
    if not events:
        gaps.append("没有可用于重建的保留日志；不能仅凭 AI 文字建立已观察事件。")
    mechanism_steps = []
    anr_type = group.get("strategy", {}).get("anrType", "unknown")
    if anr_type == "no_focus_window" or any(e["eventType"] in {"focus_wait_started", "no_focus_signal"} for e in events):
        for event_type, title in (
            ("focused_application", "焦点应用身份"), ("input_event", "输入事件信号（焦点目标查找需另证）"),
            ("focus_wait_started", "首次无焦点等待起点"),
            ("window_ineligible", "窗口焦点资格"), ("focus_request", "WMS 焦点请求"),
            ("anr_reported", "ANR 上报（到期复查条件需另证）"), ("focus_wait_reset", "等待状态 reset"),
            ("focus_granted", "实际输入焦点恢复"), ("trace_snapshot", "诊断采样"),
        ):
            matches = [e["eventId"] for e in events if e["eventType"] == event_type]
            mechanism_steps.append({"step": title, "certainty": "observed" if matches else "unknown", "supportingEventIds": matches})
        gaps.append("no-focus 检查项只是机制覆盖索引，不是一次事故必经的线性执行序列；ANR 日志不单独证明到期复查的每个条件。")
        if not any(e["eventType"] == "focus_wait_started" for e in events):
            gaps.append("未观察到无焦点等待起点，不能用 am_anr 减 5 秒补造。")
        if not any(e["configuredTimeoutMs"] is not None for e in events):
            gaps.append("未直接记录有效 dispatch timeout，不能把基础默认 5000 ms 当作本次事故实测阈值。")
    warnings = [deepcopy(warning) for key, _ in SOURCE_KEYS for warning in group.get(key, {}).get("warnings", [])]
    report = {
        "schemaVersion": SCHEMA_VERSION, "groupId": group_id, "anrType": anr_type,
        "anchor": anchor, "events": events, "relations": relations,
        "inputIdentity": deepcopy(group.get("inputIdentity")),
        "mechanismSteps": mechanism_steps, "evidenceGaps": gaps, "warnings": warnings,
        "evidenceCompleteness": deepcopy(group.get("completeness", {})),
        "processCompleteness": "partial" if events else "unknown",
        "finalJudgment": False, "notRootCauseYet": True, "requiresHumanConfirmation": True,
    }
    if artifacts:
        report["workspaceEvidenceDigest"] = _workspace_digest(artifacts.get("anr_analysis.md", ""), artifacts.get("logcat.txt", ""))
    return attach_ai_analysis(report, ai_analyses, ai_report)


def _code_block(text: str, language: str = "text") -> list[str]:
    longest = max((len(match.group()) for match in re.finditer(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return [fence + language, text, fence]


_TIMELINE_LIMIT = 24
_KEY_SIGNAL = re.compile(
    r"\bReason\s*:|timed?\s*out|timeout|not responding|unresponsive|"
    r"\bblocked\b|\bstalled\b|\bbacklog\b|\bdeadlock\b|waiting (?:for|on|to lock)|"
    r"\bFINISHED\b|\bfinishInputEvent\b|\bfinishDispatchCycle\w*\b|\bCompleted ANR\b",
    re.IGNORECASE,
)
_CRITICAL_EVENTS = {
    "anr_reported", "pre_anr_warning", "focus_wait_started", "focus_wait_reset",
    "focus_granted", "input_ack_timeout", "no_focus_signal",
}


def _brief(text: str, limit: int = 240) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "... [截断，见完整证据]"


def _key_timeline(report: dict[str, Any]) -> list[list[dict[str, Any]]]:
    cited = {event_id for claim in report.get("aiClaims", []) for event_id in claim.get("resolvedEventIds", [])}
    groups: list[list[dict[str, Any]]] = []
    previous_key = None
    for event in report.get("events", []):
        kind, raw = event["eventType"], event["rawLine"]
        if (
            kind in {"log_observation", "binder_signal", "anr_dump"}
            and not _KEY_SIGNAL.search(raw)
            and event.get("configuredTimeoutMs") is None
            and event["eventId"] not in cited
        ):
            previous_key = None
            continue
        match = TIMESTAMP_RE.match(raw.lstrip())
        # Only identical timed records can collapse, never different tokens or intervening states.
        key = (
            event["clockDomain"], event["sourceKind"], kind, raw.lstrip()[match.end():]
        ) if match and event.get("timestamp") is not None else None
        if key is not None and key == previous_key and kind not in {"trace_snapshot", "anr_reported"}:
            groups[-1].append(event)
        else:
            groups.append([event])
        previous_key = key
    if len(groups) <= _TIMELINE_LIMIT:
        return groups

    categories: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, group in enumerate(groups):
        categories[(group[0]["clockDomain"], group[0]["eventType"])].append(index)
    endpoints = {index for indices in categories.values() for index in (indices[0], indices[-1])}
    anchor_line = (report.get("anchor") or {}).get("line")

    def priority(index: int) -> tuple:
        group = groups[index]
        event = group[0]
        kind = event["eventType"]
        importance = (
            0 if any(item["rawLine"] == anchor_line for item in group)
            else 1 if kind in _CRITICAL_EVENTS or kind == "trace_snapshot" or _KEY_SIGNAL.search(event["rawLine"])
            else 2 if any(item["eventId"] in cited for item in group)
            else 3
        )
        delta = event.get("deltaTSeconds")
        return importance, index not in endpoints, abs(delta) if delta is not None else float("inf"), index

    selected = set(sorted(range(len(groups)), key=priority)[:_TIMELINE_LIMIT])
    return [group for index, group in enumerate(groups) if index in selected]


def _event_excerpt(event: dict[str, Any]) -> list[str]:
    raw = event["rawLine"]
    if event["eventType"] != "trace_snapshot":
        return [_brief(raw, 360)]
    threads = extract_trace_threads(raw.splitlines())
    main = next((thread for thread in threads if thread.get("isMainThread")), None)
    if main:
        candidates = [main["rawLine"], main.get("nativeTopFrame"), main.get("javaTopFrame")]
        candidates.extend(line for line in main["rawBlock"].splitlines() if "waiting to lock" in line or "held by thread" in line)
        return list(dict.fromkeys(line for line in candidates if line))[:4]
    return [line for line in raw.splitlines() if line.strip()][:3]


def _event_location(event: dict[str, Any], first_line: str) -> str:
    refs = event.get("evidenceRefs", [])
    ref = next((ref for ref in refs if ref["content"] == first_line), refs[0] if refs else None)
    if not ref:
        return "原始定位未知；按 eventId 查阅完整 JSON"
    coordinate = f"L{ref['lineNumber']}" if ref.get("lineNumber") else "原始行号未知/匹配不唯一"
    location = f"`{ref['sourcePath']}` · {coordinate}（加载后来源）；提取行 `{ref['extractedLineNumber']}`"
    if ref.get("artifactPath") and ref.get("artifactLineNumber"):
        path, number = ref["artifactPath"], ref["artifactLineNumber"]
        location += f"；[{path}:{number}]({path}#L{number})"
    return location


def _final_summary(report: dict[str, Any], section_titles: tuple[str, ...] = ()) -> list[str]:
    final = next((item["text"] for item in report.get("aiAnalyses", []) if item["slot"] == "final-anr"), "")
    summary: list[str] = []
    fence = ""
    active = not section_titles
    titles = {title.casefold() for title in section_titles}
    for line in final.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})(.*)$", line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = ""
            continue
        if marker:
            fence = marker[1]
            continue
        heading = re.match(r"^\s*#{1,6}\s+(.+?)\s*#*\s*$", line)
        if heading:
            if summary:
                break
            if titles:
                active = heading[1].casefold() in titles
            continue
        if active and line.strip():
            summary.append(_brief(line, 200))
            if len(summary) == 3:
                break
    return summary


def render_reconstruction_markdown(report: dict[str, Any]) -> str:
    """Render one compact incident view; keep the full evidence in the JSON sidecar."""
    events = report.get("events", [])
    timeline = _key_timeline(report)
    shown = sum(len(group) for group in timeline)
    lines = [
        "# ANR 发生过程重建", "",
        f"- ANR group：`{report['groupId']}`；类型：`{report.get('anrType', 'unknown')}`。",
        f"- 锚点：`{(report.get('anchor') or {}).get('timestamp') or '未知'}`；过程完整性：`{report.get('processCompleteness', 'unknown')}`。",
        f"- 关键节点 {len(timeline)} 项，覆盖 {shown}/{len(events)} 条观察；未展开 {len(events) - shown} 条，完整记录见 JSON。",
        "- observed = 日志记录；inferred = 未验证解释；unknown = 缺口。日志记录不保证操作成功或归属于目标 ANR。",
        "- 仅在同一时钟域内按时间排列；同时间戳不假定执行顺序，不跨域计算 ΔT；时间先后不等于因果（causal=false）。",
        "", "## 关键时间线", "",
    ]
    if not timeline:
        lines.append("_没有可展开的关键事件；不能据此补造执行过程，完整保留证据见 JSON。_")
    domain = None
    for group in timeline:
        first, last = group[0], group[-1]
        if first["clockDomain"] != domain:
            domain = first["clockDomain"]
            lines.extend([f"### 时钟域：{domain}", ""])
        delta = first.get("deltaTSeconds")
        relative = f"{delta:+.6f}s" if delta is not None else "未知/不可跨域比较"
        lines.extend([
            f"#### {first['timestamp'] or '时间未知'} · {first['summary']}",
            f"- observed · `{first['eventType']}` · ΔT `{relative}` · `{first['eventId']}`。",
        ])
        if len(group) > 1:
            lines.append(f"- 重复记录：{len(group)} 次；首末观测点：`{first['timestamp']}` / `{last['timestamp']}`（`{last['eventId']}`）；不表示连续状态或阻塞时长。")
        if first.get("configuredTimeoutMs") is not None:
            lines.append(f"- 配置阈值：`{first['configuredTimeoutMs']} ms`，不是实测等待时长。")
        excerpt = _event_excerpt(first)
        lines.extend([f"- 摘录首行定位：{_event_location(first, excerpt[0])}。", *_code_block("\n".join(_brief(line, 360) for line in excerpt)), ""])

    lines.extend(["## 候选解释摘要", "", "inferred / unverified；references_resolved 只说明引用存在，不证明解释或因果。", ""])
    summary = _final_summary(report)
    if summary:
        lines.extend(_code_block("\n".join(summary)))
    claims = report.get("aiClaims", [])
    for number, claim in enumerate(claims[:3], 1):
        text = next((claim[key] for key in ("summary", "statement", "title", "rationale") if isinstance(claim.get(key), str) and claim[key].strip()), "仅含结构化字段，详见 JSON。")
        rationale = claim.get("rationale")
        if text == claim.get("title") and isinstance(rationale, str) and rationale.strip():
            text += "；" + rationale
        lines.extend([f"- 候选 {number}（inferred / {claim.get('validation', 'unverified')}）：", *_code_block(_brief(text))])
        limitations = claim.get("limitations", [])
        if isinstance(limitations, str):
            limitations = [limitations]
        if isinstance(limitations, list):
            notes = [_brief(item, 160) for item in limitations if isinstance(item, str) and item.strip()][:2]
            if notes:
                lines.extend(["限制（inferred / unverified）：", *_code_block("\n".join(notes))])
    if len(claims) > 3:
        lines.append(f"- 其余 {len(claims) - 3} 条候选未展开，见完整 JSON。")
    if not summary and not claims:
        lines.append("- 尚无综合候选摘要；填写四个 AI 槽位后刷新。本文件不重复专项分析正文。")

    lines.extend([
        "", "## 关键缺口与限制", "",
        "- 过滤窗口外的过程未知；Trace 快照不证明持续阻塞；缺省年份、跨年或时钟跳变需另核验。",
    ])
    missing = [step["step"] for step in report.get("mechanismSteps", []) if step["certainty"] == "unknown"]
    if missing:
        lines.append("- unknown / 未观察到的机制检查项（非必经线性步骤）：" + "、".join(missing) + "。")
    lines.extend(f"- {_brief(gap)}" for gap in dict.fromkeys(report.get("evidenceGaps", [])) if gap not in _COMMON_GAPS)
    ai_gaps = _final_summary(report, ("证据质量", "证据缺口", "Evidence quality", "Evidence gaps"))
    if ai_gaps:
        lines.extend(["- AI 证据限制（inferred / unverified）：", *_code_block("\n".join(ai_gaps))])
    warnings: dict[str, str] = {}
    for warning in [*report.get("warnings", []), *report.get("aiWarnings", [])]:
        warnings.setdefault(warning.get("code", "warning"), warning.get("message", ""))
    lines.extend(f"- {code}：{_brief(message)}" for code, message in warnings.items())
    lines.extend([
        "", "完整分析：[anr_analysis.md](anr_analysis.md)；完整事件、证据引用、时序关系和 AI 原文：[anr_reconstruction.json](anr_reconstruction.json)。",
        "保守标志：`finalJudgment=false`；`notRootCauseYet=true`；`requiresHumanConfirmation=true`。不是设备执行重放或最终根因裁决。", "",
    ])
    return "\n".join(lines)


def write_reconstruction_artifacts(report: dict[str, Any], directory: str | Path) -> dict[str, str]:
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "anr_reconstruction.json"
    md_path = root / "anr_reconstruction.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_reconstruction_markdown(report), encoding="utf-8")
    return {"reconstruction": str(md_path), "reconstructionJson": str(json_path)}


def _workspace_blocks(text: str) -> dict[str, list[tuple[int, str]]]:
    from .ai_context import _analysis_slot_body_span

    titles = {"Trace 堆栈", "EventLog 事件日志", "Logcat 系统日志", "Meminfo 目标/高负载跟进"}
    spans = sorted(span for slot in ("trace", "eventlog", "logcat-anrmanager", "final-anr") if (span := _analysis_slot_body_span(text, slot)))
    result: dict[str, list[tuple[int, str]]] = {}
    section, fence, block = None, "", []
    offset = 0
    span_index = 0
    for number, raw in enumerate(text.splitlines(keepends=True), 1):
        line = raw.rstrip("\r\n")
        position, offset = offset, offset + len(raw)
        while span_index < len(spans) and position >= spans[span_index][1]:
            span_index += 1
        if span_index < len(spans) and spans[span_index][0] <= position < spans[span_index][1]:
            section, fence, block = None, "", []
            continue
        marker = re.match(r"^(`{3,})(.*)$", line)
        if fence:
            if marker and len(marker.group(1)) >= len(fence) and not marker.group(2).strip():
                if section is not None:
                    result[section] = block
                fence, block = "", []
            else:
                block.append((number, line))
            continue
        if line.startswith("### "):
            title = line[4:]
            section = title if title in titles and title not in result else None
        elif marker:
            if section is not None and marker.group(2).strip() == "text":
                fence, block = marker.group(1), []
            else:
                section = None
    return result


def _workspace_evidence(text: str, title: str) -> list[str]:
    return [line for _, line in _workspace_blocks(text).get(title, [])]


def _workspace_digest(text: str, logcat: str) -> str:
    evidence = {title: _workspace_evidence(text, title) for title in ("Trace 堆栈", "EventLog 事件日志", "Meminfo 目标/高负载跟进")}
    evidence["logcat"] = logcat.splitlines() if logcat else _workspace_evidence(text, "Logcat 系统日志")
    evidence["anchorMetadata"] = [match.group(0) for label in ("Timestamp", "Source", "Line", "ANR type strategy") if (match := re.search(rf"^- {label}:.*$", text, re.MULTILINE))]
    return sha256(json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _load_legacy_workspace(path: Path, text: str) -> dict[str, Any]:
    anchor_match = re.search(r"^- Timestamp: `([^`]+)`", text, re.MULTILINE)
    anchor_line = re.search(r"^- Line: `([^`]+)`", text, re.MULTILINE)
    type_match = re.search(r"^- ANR type strategy: `([^`]+)`", text, re.MULTILINE)
    logcat = path.parent / "logcat.txt"
    group = {
        "id": path.parent.name,
        "anchor": {"timestamp": anchor_match.group(1), "line": anchor_line.group(1) if anchor_line else "", "sourceKind": "event_log"} if anchor_match else None,
        "strategy": {"anrType": type_match.group(1) if type_match else "unknown"},
        "trace": {"lines": _workspace_evidence(text, "Trace 堆栈")},
        "eventLog": {"lines": _workspace_evidence(text, "EventLog 事件日志")},
        "logcat": {"lines": logcat.read_text(encoding="utf-8").splitlines() if logcat.exists() else _workspace_evidence(text, "Logcat 系统日志")},
    }
    sources = {kind: {"path": path.name, "content": text} for kind in ("trace", "event_log")}
    sources["logcat"] = {"path": logcat.name if logcat.exists() else path.name, "content": logcat.read_text(encoding="utf-8") if logcat.exists() else text}
    report = build_reconstruction(group, source_paths={kind: data["path"] for kind, data in sources.items()}, source_locations=index_source_locations({"sources": sources}, [group]), artifact_texts={path.name: text, logcat.name: logcat.read_text(encoding="utf-8") if logcat.exists() else ""})
    report["evidenceGaps"].append("此工作区由旧版 Markdown 迁移；只能定位当前产物，原始输入路径/分片位置及未导出的来源可能不可恢复。")
    return report


def refresh_reconstruction(input_path: str | Path, *, ai_report: dict[str, Any] | None = None) -> dict[str, Any]:
    """Refresh reconstruction documents without re-extracting or editing AI slots."""
    from .ai_context import _analysis_slot_statuses_from_text, _read_existing_analyses_from_text

    path = Path(input_path)
    if path.is_file() and path.name == "index.json":
        path = path.parent
    if path.is_file():
        if path.name != "anr_analysis.md":
            raise ValueError("Expected anr_analysis.md, an ANR group directory, or a context root.")
        directories = [path.parent]
        index_path = None
    elif path.is_dir() and (path / "anr_analysis.md").is_file():
        directories, index_path = [path], None
    elif path.is_dir() and (path / "index.json").is_file():
        index_path = path / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        directories = []
        for group in index.get("groups", []):
            name = group.get("id", "")
            if not name or Path(name).name != name or (path / name).resolve().parent != path.resolve():
                raise ValueError("Unsafe ANR group path in context index.")
            directories.append(path / name)
    else:
        raise ValueError("No generated ANR workspace found; run scripts/anr_to_ai.py first.")
    if not directories:
        raise ValueError("Context index contains no ANR groups.")
    if ai_report is not None and len(directories) > 1 and not isinstance(ai_report.get("groups"), list):
        raise ValueError("For multiple ANRs, --ai-report must contain groups with explicit groupId values.")
    if index_path is None:
        parent_index = directories[0].parent / "index.json"
        if parent_index.is_file():
            index_path = parent_index
            index = json.loads(index_path.read_text(encoding="utf-8"))
    outputs = []
    for directory in directories:
        analysis_path = directory / "anr_analysis.md"
        text = analysis_path.read_text(encoding="utf-8")
        json_path = directory / "anr_reconstruction.json"
        report = json.loads(json_path.read_text(encoding="utf-8")) if json_path.exists() else _load_legacy_workspace(analysis_path, text)
        if report.get("schemaVersion") != SCHEMA_VERSION or report.get("groupId") != directory.name:
            raise ValueError(f"Invalid reconstruction identity/schema in {json_path}")
        logcat_path = directory / "logcat.txt"
        artifacts = {"anr_analysis.md": text, "logcat.txt": logcat_path.read_text(encoding="utf-8") if logcat_path.exists() else ""}
        digest = _workspace_digest(text, artifacts["logcat.txt"])
        if report.get("workspaceEvidenceDigest") and report["workspaceEvidenceDigest"] != digest:
            raise ValueError(f"Extracted evidence changed in {directory}; re-run anr_to_ai.py to rebuild observed events before refreshing AI explanations.")
        report["workspaceEvidenceDigest"] = digest
        artifact_locations = _artifact_locations(artifacts)
        for event in report.get("events", []):
            for ref in event.get("evidenceRefs", []):
                artifact = "logcat.txt" if ref["sourceKind"] == "logcat" else "anr_analysis.md"
                positions = artifact_locations.get((artifact, ref["sourceKind"], ref["content"].strip()), [])
                ref["artifactPath"] = artifact if positions else None
                ref["artifactLineNumber"] = positions[0] if len(positions) == 1 else None
        current_ai = ai_report
        if ai_report is not None and isinstance(ai_report.get("groups"), list):
            matches = [item for item in ai_report["groups"] if isinstance(item, dict) and item.get("groupId") == report["groupId"]]
            if len(matches) > 1:
                raise ValueError(f"Duplicate AI report groupId: {report['groupId']}")
            current_ai = matches[0] if matches else None
        report = attach_ai_analysis(report, _read_existing_analyses_from_text(text), current_ai)
        outputs.append({"id": report["groupId"], "artifactPaths": write_reconstruction_artifacts(report, directory), "analysisSlots": _analysis_slot_statuses_from_text(text)})
    if index_path is not None:
        by_id = {item["id"]: item for item in outputs}
        for group in index.get("groups", []):
            if group["id"] not in by_id:
                continue
            update = by_id[group["id"]]
            group.setdefault("artifactPaths", {}).update(update["artifactPaths"])
            group["analysisSlots"] = update["analysisSlots"]
            group["analysisComplete"] = all(status == "filled" for status in update["analysisSlots"].values())
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"groupCount": len(outputs), "groups": outputs}
