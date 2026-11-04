"""Evidence-bound ANR occurrence models and unexecuted verification plans."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from .am_anr import package_name_from_am_anr_line
from .log_filter import TIMESTAMP_RE


SCHEMA_VERSION = "anr-incident-scenario-v1"
_FLAGS = {
    "certainty": "inferred",
    "validation": "unverified",
    "executed": False,
    "finalJudgment": False,
    "notRootCauseYet": True,
    "requiresHumanConfirmation": True,
}


_TYPE_CONFIG: dict[str, dict[str, Any]] = {
    "broadcast_timeout": {
        "label": "Broadcast receiver 超时",
        "phases": (
            ("dispatch", "系统选择并投递 Broadcast/receiver；目标 intent、receiver 和投递时刻待核验。", ("broadcast", "receiver", "broadcast of intent")),
            ("callback", "receiver 执行 onReceive，或 goAsync 后由 PendingResult 持续承担完成责任。", ("onreceive", "goasync", "pendingresult")),
            ("completion", "receiver 调用 finish/finishReceiver 或完成异步结果；未观察到不等于没有调用。", ("finishreceiver", "finished", "pendingresult.finish")),
            ("notification", "记录 ANR 上报原文；到期检测、通知及实际 receiver 归因仍需另外举证。", ("am_anr", "anr in", "broadcast timeout")),
            ("recovery", "队列清理、结果回收和进程/用户界面恢复，当前通常需要补证。", ("recovery", "resumed", "completed anr")),
        ),
        "action": "在隔离环境用相同 receiver/intent 前置条件触发一次 Broadcast，并记录 onReceive/goAsync/finishReceiver 与 ANR 结果。",
        "missing": ("完整 intent/receiver 身份", "有效 broadcast timeout 与调度时刻", "goAsync/PendingResult 状态", "目标进程线程栈和系统负载"),
    },
    "service_timeout": {
        "label": "Service 生命周期超时",
        "phases": (
            ("dispatch", "系统启动、绑定或调度目标 Service；具体 start/bind 请求和目标组件待核验。", ("service", "startservice", "bindservice")),
            ("callback", "Service 执行 onCreate/onStartCommand/onBind 等生命周期回调。", ("oncreate", "onstartcommand", "onbind", "foreground service")),
            ("completion", "生命周期回调或前台服务启动完成；完成信号和返回边界需与线程证据对齐。", ("service done", "started", "startforeground")),
            ("notification", "记录 ANR 上报原文；具体生命周期超时、通知及进程归因仍需另外举证。", ("am_anr", "anr in", "timeout executing service", "service timeout")),
            ("recovery", "服务启动/绑定状态、进程和后续重启结果，当前通常未知。", ("service stopped", "service restart", "completed anr")),
        ),
        "action": "在隔离环境按已确认的 start/bind/foreground 前置条件触发 Service 生命周期，并采集组件与主线程证据。",
        "missing": ("Service component 和启动入口", "启动模式与有效 timeout", "回调参数/Intent", "进程主线程和系统负载"),
    },
    "content_provider_timeout": {
        "label": "ContentProvider 超时",
        "phases": (
            ("dispatch", "系统发布 Provider 或远端发起 query/CRUD；publish 与调用方身份需分开。", ("contentprovider", "content provider", "query", "crud")),
            ("callback", "Provider 在主线程、Binder 线程或发布流程中处理请求。", ("binder", "oncreate", "query", "insert", "update", "delete")),
            ("completion", "Provider 发布完成或返回调用结果；单个 Binder 返回不自动证明全链恢复。", ("published", "reply", "result")),
            ("notification", "记录 ANR 上报原文；publish 或调用无响应的超时检查及通知仍需另外举证。", ("am_anr", "anr in", "provider not responding", "publishing content providers")),
            ("recovery", "Provider/调用方连接和后续请求恢复，需同身份证据。", ("recovered", "resumed", "completed anr")),
        ),
        "action": "在隔离环境按已确认的 Provider publish 或 query/CRUD 入口执行一次请求，记录调用方、Provider 线程、Binder 往返及结果。",
        "missing": ("Provider 组件与调用方", "publish/query/CRUD 类型", "请求参数和有效 timeout", "Provider 线程/锁/Binder 对端"),
    },
    "job_scheduler_timeout": {
        "label": "JobService 回调超时",
        "phases": (
            ("dispatch", "JobScheduler 选择并绑定 JobService，目标 job、约束和调度时刻待核验。", ("jobscheduler", "jobservice", "bind")),
            ("callback", "JobService 执行 onStartJob/onStopJob 或关联工作。", ("onstartjob", "onstopjob", "jobservice")),
            ("completion", "核验 onStartJob/onStopJob 及时返回；用户发起任务另查 setNotification。jobFinished 是工作完成通知，不替代回调返回。", ("jobfinished", "onstartjob", "onstopjob", "setnotification")),
            ("notification", "记录 ANR 上报原文；回调或通知超时、系统归因仍需另外举证。", ("am_anr", "anr in", "jobservice timeout", "jobscheduler")),
            ("recovery", "job 取消、重试或完成状态，需结合 JobScheduler 证据核验。", ("retry", "cancel", "completed anr")),
        ),
        "action": "在隔离环境按已确认的 job 约束调度 JobService，并采集 onStartJob/onStopJob 返回、setNotification 分支、工作完成和线程状态。",
        "missing": ("jobId/JobService 身份", "约束和调度入口", "回调返回/通知分支", "有效 timeout 与线程负载"),
    },
    "system_watchdog_swt": {
        "label": "System Watchdog/SWT",
        "phases": (
            ("dispatch", "Watchdog monitor/checker 或 system_server handler 开始等待目标组件。", ("watchdog", "monitor", "checker", "system_server")),
            ("callback", "目标 monitor、handler 或 system_server 线程未按预期返回；阻塞栈需具体到线程。", ("blocked in handler", "waiting", "futex", "binder")),
            ("completion", "目标检查返回或继续未观察到，Trace 快照不能证明完整等待区间。", ("completed", "returned", "handled")),
            ("notification", "记录明确的 ANR/Watchdog 处置原文；checker 到期及实际责任归因仍需另外举证。", ("watchdog", "swt", "am_anr", "anr in")),
            ("recovery", "system_server/进程处置、重启或恢复状态，必须单独取证。", ("restart", "kill", "recovered", "completed")),
        ),
        "action": "不自动注入故障；仅在已有明确前置条件和授权测试环境时观察同类 monitor/handler 链，并记录 system_server 与线程状态。",
        "missing": ("Watchdog monitor/handler 身份", "有效 deadline", "阻塞线程与 owner", "处置策略及设备版本"),
    },
}


def _text(item: dict[str, Any]) -> str:
    return " ".join(str(item.get(key) or "") for key in ("rawLine", "summary", "recordType", "eventType")).casefold()


def _ref(item: dict[str, Any]) -> str | None:
    return item.get("eventId") or item.get("entryId") or item.get("recordRef")


def _is_report_line(line: str) -> bool:
    text = line.strip()
    timestamp = TIMESTAMP_RE.match(text)
    if timestamp:
        text = re.sub(r"^(?:Z|[+-]\d{2}:?\d{2})?\s*", "", text[timestamp.end():])
    text = re.sub(r"^(?:\S+\s+)?\d+\s+\d+\s+[VDIWEFAS]\s+", "", text)
    brief = re.match(r"^[VDIWEFAS]/([\w.]+)\(\s*\d+\):\s*(.*)$", text)
    if brief:
        text = f"{brief[1]}: {brief[2]}"
    # Match actual report tags/headers, never a keyword mentioned in a message.
    if re.match(r"^(?:[VDIWEFAS]\s+)?am_anr\s*:?\s*(?:\[|ANR\s+in\s+)", text, re.I):
        return package_name_from_am_anr_line(text) is not None
    header = re.match(r"^(?:[VDIWEFAS]\s+)?(?:ActivityManager|AnrManager|Watchdog)\s*:\s*(.*)$", text, re.I)
    message = header[1] if header else text
    return bool(re.match(r"^(?:ANR\s+in\s+[\w.]+|(?:\*{3}\s*)?WATCHDOG KILLING SYSTEM PROCESS\b)", message, re.I))


def _anchor_refs(anchor: dict[str, Any] | None, observations: list[dict[str, Any]]) -> list[str]:
    line = (anchor or {}).get("line")
    refs = []
    if not line or not _is_report_line(line):
        return refs
    for item in observations:
        raw = item.get("rawLine") or ""
        if raw == line and item.get("sourceKind") != "trace":
            if _ref(item) and _ref(item) not in refs:
                refs.append(_ref(item))
    return refs


def _phase_entries(config: dict[str, Any], anchor: dict[str, Any] | None, observations: list[dict[str, Any]]) -> tuple[list[dict], list[str]]:
    anchor_refs = _anchor_refs(anchor, observations)
    phases = [{
        "phase": "timing", "summary": "逐项核验计时起点、有效超时配置、截止时间和时钟域；不把过滤窗口或缺省阈值当作实测。",
        "certainty": "unknown", "supportingEventIds": [], "candidateObservationIds": [], "causal": False,
    }]
    gaps = ["缺少经过逐事件核验的有效超时/时钟映射；本模板不推定默认时限、持续阻塞区间或因果关系。"]
    for phase, summary, patterns in config["phases"]:
        refs = []
        for item in observations:
            if any(pattern in _text(item) for pattern in patterns):
                if _ref(item) and _ref(item) not in refs:
                    refs.append(_ref(item))
        # Keywords locate candidate observations, not successful execution or ownership.
        supporting = anchor_refs if phase == "notification" else []
        certainty = "observed" if supporting else "unknown"
        phases.append({
            "phase": phase,
            "summary": summary,
            "certainty": certainty,
            "supportingEventIds": list(supporting),
            "candidateObservationIds": refs,
            "causal": False,
        })
        if not supporting:
            gaps.append(f"未观察到 {phase} 阶段的直接证据；不能把机制模板当作本次事故已执行。")
    return phases, gaps


def _generic_steps(config: dict[str, Any], package: str | None, refs: list[str]) -> list[dict]:
    target = package or "目标包名待补"
    expected = [
        "目标组件身份、调度条件和系统版本与事故证据一致。",
        "同一责任边界的回调、完成信号和阻塞线程可在日志或 trace 中对齐。",
        "只在证据支持的情况下比较 ANR、清理和恢复；未出现同分支 ANR 不算复现成功。",
    ]
    summaries = [
        f"在隔离测试环境核对设备版本、目标包 {target}、组件身份和原始前置状态。",
        config["action"] + " 入口、参数或前置条件尚未确认时先补证，不直接执行。",
        "同步采集 EventLog、目标组件日志、trace/Perfetto、线程/锁/Binder 与系统负载，不自动执行设备命令。",
    ]
    return [{
        "order": index,
        "summary": summary,
        "expectedObservation": expected[index - 1],
        "preconditions": ["事故证据中的组件/入口已确认；缺失时先补证。"],
        "supportingEventIds": list(refs),
        "missingParameters": list(config["missing"]),
        "certainty": "inferred",
        "validation": "unverified",
        "executed": False,
        "requiresHumanConfirmation": True,
    } for index, summary in enumerate(summaries, 1)]


def _fallback_config(anr_type: str) -> dict[str, Any]:
    return {
        "label": f"{anr_type or 'unknown'} ANR",
        "phases": (
            ("trigger", "根据当前分类和 ANR 锚点定位触发入口；具体机制未知。", ("anr", "timeout", "not responding")),
            ("blocking", "检查目标组件/线程是否存在阻塞或完成责任未履行；当前不能指定函数。", ("blocked", "waiting", "timeout")),
            ("notification", "记录已观察到的 ANR 上报原文；类型、超时检查和责任归因仍需核验。", ("am_anr", "anr in")),
            ("recovery", "核验清理、恢复或处置结果；缺少证据时保持未知。", ("recovered", "resumed", "completed")),
        ),
        "action": "先补齐 ANR 类型、目标组件、触发入口和有效 timeout，再制定受控验证步骤；当前不执行猜测性操作。",
        "missing": ("准确 ANR 类型", "目标组件/入口", "有效 timeout", "触发前后完整日志和线程证据"),
    }


def build_incident_scenario(
    anr_type: str,
    anchor: dict[str, Any] | None,
    observations: list[dict[str, Any]],
    *,
    input_anr: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a conservative model and verification plan for every ANR type."""
    if input_anr is None:
        from .input_anr import build_input_anr

        input_anr = build_input_anr(anr_type, anchor, observations)
    if input_anr is not None:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "anrType": anr_type,
            "label": "Input ANR",
            "anrModel": deepcopy(input_anr.get("anrModel", [])),
            "simulationSteps": deepcopy(input_anr.get("simulationSteps", [])),
            "missingParameters": deepcopy(input_anr.get("missingParameters", [])),
            "evidenceGaps": deepcopy(input_anr.get("evidenceGaps", [])),
            **_FLAGS,
        }
    config = _TYPE_CONFIG.get(anr_type, _fallback_config(anr_type))
    phases, gaps = _phase_entries(config, anchor, observations)
    refs = _anchor_refs(anchor, observations)
    anchor_line = (anchor or {}).get("line") or ""
    package = package_name_from_am_anr_line(anchor_line)
    match = re.search(r"\bANR in\s+([\w.]+(?::[\w.]+)?)", anchor_line)
    if not package and match:
        package = match[1]
    if not refs:
        gaps.append("缺少可定位的 ANR 上报证据；当前模型只表示待验证机制，不代表事故已经完整还原。")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "anrType": anr_type or "unknown",
        "label": config["label"],
        "anrModel": phases,
        "simulationSteps": _generic_steps(config, package, refs),
        "missingParameters": list(config["missing"]),
        "evidenceGaps": list(dict.fromkeys(gaps)),
        **_FLAGS,
    }


def render_incident_sections(incident_scenario: dict[str, Any], input_anr: dict[str, Any] | None = None) -> list[str]:
    """Render model and simulation sections for every type; input adds its legacy section."""
    if input_anr is not None:
        from .input_anr import render_input_anr_sections

        input_view = deepcopy(input_anr)
        for field in ("anrModel", "simulationSteps", "missingParameters", "evidenceGaps"):
            input_view[field] = deepcopy(incident_scenario.get(field, []))
        return render_input_anr_sections(input_view)
    lines = ["", "## ANR 发生模型", "", f"- 类型：`{incident_scenario.get('anrType', 'unknown')}`；模型：`inferred / unverified`。", "机制模板不是已证实的连续执行或因果链；unknown 节点不补成 observed，Trace 仅为采样快照。关键词仅定位候选观察，不证明执行或归属；完整候选引用见 JSON。"]
    for phase in incident_scenario.get("anrModel", []):
        lines.append(f"- {phase.get('certainty', 'unknown')} / {phase.get('phase', 'unknown')}：{phase.get('summary', '内容未知')}；证据 {phase.get('supportingEventIds', [])}。")
    lines.extend(["", "## 模拟操作步骤", "", "候选验证方案：inferred / unverified；executed=false；不是真实操作记录，不保证复现，不自动运行设备命令。"])
    for step in incident_scenario.get("simulationSteps", []):
        lines.append(f"{step.get('order', '?')}. {step.get('summary', '动作未知')} 预期观察：{step.get('expectedObservation', '待补')}。")
        for field, label in (
            ("preconditions", "前置条件"), ("missingParameters", "步骤待补参数"),
            ("captureChecks", "采集核验"), ("expectedObservations", "补充预期观察"),
            ("uncertainties", "步骤缺口"),
        ):
            values = step.get(field)
            if values:
                text = "、".join(str(value) for value in values) if isinstance(values, list) else str(values)
                text = " ".join(text.split())
                lines.append(f"{label}：{text[:400]}" + ("... [截断，完整限制见 JSON]" if len(text) > 400 else "") + "。")
    lines.append("- 全局待补参数：" + "、".join(incident_scenario.get("missingParameters", [])) + "。")
    for gap in incident_scenario.get("evidenceGaps", []):
        lines.append(f"- {gap}")
    return lines + [""]
