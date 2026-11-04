"""Input ANR incident identity and unexecuted simulation plans, not device replay."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any


_INPUT_TYPES = {"input_dispatching_timeout", "no_focus_window"}
_NO_FOCUS = re.compile(r"does not have a focused window|no focused window|no focus window|no window has focus", re.I)
_ACK = re.compile(r"is not responding.*?Waited\s+\d+\s*ms|has not finished processing", re.I)
_INPUT_REASON = re.compile(r"Input dispatching timed out|is not responding.*?Waited\s+\d+\s*ms|has not finished processing", re.I)
_FIELDS = {
    "action": r"\baction\s*=\s*([A-Z_]+(?:\(\d+\))?|\d+)",
    "keyCode": r"\bkeyCode\s*=\s*([\w]+)",
    "nativeSeq": r"\b(?:nativeSeq|dispatchSeq)\s*[=:]\s*([\w-]+)",
    "reportedSeq": r"\bseq\s*[=:]\s*([\w-]+)",
    "inputEventId": r"\b(?:eventId|id)\s*[=:]\s*(0x[\da-fA-F]+|[\w-]+)",
    "deviceId": r"\bdeviceId\s*=\s*(-?\d+)",
    "source": r"\bsource\s*=\s*([\w]+)",
    "displayId": r"\bdisplayId\s*=\s*(-?\d+)",
    "windowToken": r"\bwindowToken\s*[=:]\s*([^\s,;)]+)",
    "connectionToken": r"\b(?:connectionToken|channelToken)\s*[=:]\s*([^\s,;)]+)",
    "channel": r"\bchannel\s*[=:]\s*['\"]?([^'\"\n,;)]+)",
}
_FLAGS = {"finalJudgment": False, "notRootCauseYet": True, "requiresHumanConfirmation": True}


def _identity(raw: str) -> dict[str, Any]:
    kinds = list(re.finditer(r"\b(MotionEvent|KeyEvent)\s*\(", raw))
    kind = kinds[0] if kinds else None
    end = None
    if kind:
        depth = 1
        for index in range(kind.end(), len(raw)):
            depth += (raw[index] == "(") - (raw[index] == ")")
            if depth == 0:
                end = index + 1
                break
    # Event fields must come from the event description, not unrelated log metadata.
    description = raw[kind.start():end] if kind and end is not None else ""
    context = raw[:kind.start()] if kind else raw
    if kind and end is not None and len(kinds) == 1:
        context += raw[end:]
    metadata = description + " " + context
    result: dict[str, Any] = {"eventType": kind[1] if description else None}
    for name, pattern in _FIELDS.items():
        text = metadata if name in {"windowToken", "connectionToken", "channel", "nativeSeq", "reportedSeq"} else description
        match = re.search(pattern, text)
        result[name] = match[1].strip() if match else None
    if result["action"] is None:
        action = re.search(r"\bACTION_[A-Z_]+\b", description)
        result["action"] = action[0] if action else None
    result["seqDomain"] = "native" if result["nativeSeq"] else "unknown"
    target = re.search(r"(?:Input dispatching timed out\s*\(|Reason:\s*|\bInputDispatcher[: ]+)(.*?)\s+is not responding\.", raw)
    result["reportedTarget"] = target[1].strip() if target else None
    wait = re.search(r"\bWaited\s+(\d+)\s*ms", raw, re.I)
    result["reportedWaitMs"] = int(wait[1]) if wait else None
    configured = re.search(r"\b(?:effectiveTimeout|dispatchingTimeout)\s*[=:]\s*(\d+)\s*ms\b", metadata)
    result["effectiveTimeoutMs"] = int(configured[1]) if configured else None
    for name, pattern in (
        ("deliveryTime", r"\bdeliveryTime\s*[=:]\s*([^\s,;)]+)"),
        ("deadline", r"\b(?:timeoutTime|deadline)\s*[=:]\s*([^\s,;)]+)"),
    ):
        match = re.search(pattern, metadata)
        result[name] = match[1] if match else None
    return result


def _package(raw: str) -> str | None:
    # Match explicit ANR attribution, never an arbitrary package mentioned in a payload.
    match = re.search(r"\bANR in\s+([\w.]+(?::[\w.]+)?)", raw)
    if match:
        return match[1]
    match = re.search(r"\bam_anr\s*:\s*\[\s*\d+\s*,\s*\d+\s*,\s*([^,\s]+)", raw)
    return match[1] if match else None


def build_input_anr(anr_type: str, anchor: dict | None, observations: list[dict]) -> dict | None:
    """Derive conservative input details from this incident's retained observations.

    A reason identifies what the platform reported, not necessarily the record
    whose deadline expired first. Nearby input logs are not automatically linked.
    """
    anchor = anchor or {}
    anchor_line = anchor.get("line") or ""
    if anr_type not in _INPUT_TYPES and not (_INPUT_REASON.search(anchor_line) or _NO_FOCUS.search(anchor_line)):
        return None
    target_package = _package(anchor_line)
    records: list[dict] = []
    for event in observations:
        raw = event.get("rawLine") or ""
        if event.get("sourceKind") == "trace" or not (_INPUT_REASON.search(raw) or _NO_FOCUS.search(raw)):
            continue
        package = _package(raw)
        if package and target_package and package != target_package:
            continue
        association = "anchor" if raw == anchor_line else "unverified"
        records.append({
            "identity": _identity(raw), "rawReason": raw,
            "association": association,
            "certainty": "observed" if association != "unverified" else "inferred",
            "evidenceRefs": [event.get("eventId") or event.get("entryId") or event.get("recordRef")],
            "sourcePath": event.get("sourcePath"),
        })
    if (_INPUT_REASON.search(anchor_line) or _NO_FOCUS.search(anchor_line)) and not any(item["association"] == "anchor" for item in records):
        records.insert(0, {
            "identity": _identity(anchor_line), "rawReason": anchor_line,
            "association": "anchor", "certainty": "observed", "evidenceRefs": [],
            "anchorRef": deepcopy(anchor), "sourcePath": None,
        })
    records.sort(key=lambda item: item["association"] != "anchor")
    attributed = [item for item in records if item["association"] != "unverified"]
    anchor_has_reason = bool(_INPUT_REASON.search(anchor_line) or _NO_FOCUS.search(anchor_line))
    reason_text = anchor_line if anchor_has_reason else "\n".join(item["rawReason"] for item in attributed)
    no_focus, ack = bool(_NO_FOCUS.search(reason_text)), bool(_ACK.search(reason_text))
    branch = "ambiguous" if no_focus and ack else "no_focused_window" if no_focus else "ack_timeout" if ack else "unknown"
    if branch == "unknown" and anr_type == "no_focus_window":
        branch = "no_focused_window"
    identities = [item for item in attributed if item["identity"]["eventType"]]
    status = "reason_event_identified" if identities else "event_unspecified"
    if len({str(item["identity"]) for item in identities}) > 1:
        status = "multiple_reported_events"
    refs = list(dict.fromkeys(ref for item in attributed for ref in item["evidenceRefs"] if ref))
    gaps = [
        "reason 描述的事件不一定是最早到期条目；未核验逐事件截止时间，triggeringEvent 保持 null。",
        "未记录的字段保持 null；reportedWaitMs 不是有效超时配置，不能用 am_anr 减 5 秒补造起点；原样时间字段的时钟域/单位需另核验。",
        "通用 seq 的命名空间未知，不与 Java 序号或 nativeSeq 自动等同；单个 ACK 不证明连接恢复。",
    ]
    if not identities:
        gaps.append("无法确定具体超时输入事件；缺少与本次 ANR 直接关联的 KeyEvent/MotionEvent 描述。")
    if any(item["association"] == "unverified" for item in records):
        gaps.append("同窗口保留的匿名 reason 仅是候选，未证明与本次 ANR 的窗口/连接身份相同。")
    if branch == "ambiguous":
        gaps.append("同一归属存在无焦点等待与 ACK 超时信号，需按事件与阶段分开补证。")
    report_refs = list(dict.fromkeys(
        ref for item in attributed
        if _INPUT_REASON.search(item["rawReason"]) or re.search(r"\bam_anr\b|\bANR in\b", item["rawReason"])
        for ref in item["evidenceRefs"] if ref
    ))
    model = _model(branch, report_refs)
    steps = _simulation(branch, target_package, identities, refs)
    return {
        "schemaVersion": "input-anr-v1", "branch": branch,
        "eventIdentificationStatus": status, "targetPackage": target_package,
        "reportedEvents": records, "triggeringEvent": None,
        "anrModel": model, "simulationSteps": steps,
        "missingParameters": ["设备/系统/应用版本", "目标页面入口及前置状态", "输入坐标/轨迹/完整按键序列", "有效 timeout 与同 seq 完成链", "已验证的上游阻塞条件"],
        "evidenceGaps": gaps, **_FLAGS,
    }


def _model(branch: str, refs: list[str]) -> list[dict]:
    if branch == "no_focused_window":
        labels = ["依赖焦点的输入到达 / 查找目标", "存在焦点应用但无合格焦点窗口 / 开始等待", "按有效配置到期复查 / WMS 归因", "ANR reason / 上报", "焦点建立或等待 reset / 恢复待核验"]
    elif branch == "ack_timeout":
        labels = ["选定连接 / 发布成功 / 登记逐事件截止时间", "接收器消费 / 输入处理", "请求 finish / FINISHED 传输", "Dispatcher 清理同 native seq 的 waitQueue", "到期检测 / 策略通知 / WMS 归因", "ANR reason / 上报", "核验剩余 waitQueue / 响应恢复"]
    else:
        labels = ["输入到达 / 查找目标", "分型：分发前无焦点等待或发布后 ACK 等待（待核验）", "核验有效超时 / 到期条件", "ANR reason / 上报", "恢复条件待核验"]
    return [{
        "summary": label,
        "certainty": "observed" if refs and ("reason / 上报" in label) else "unknown",
        "supportingEventIds": list(refs) if "reason / 上报" in label else [],
        "causal": False,
    } for label in labels]


def _simulation(branch: str, package: str | None, events: list[dict], refs: list[str]) -> list[dict]:
    identities = []
    for event in events:
        if event["identity"] not in identities:
            identities.append(event["identity"])
    identity = identities[0] if len(identities) == 1 else {}
    event_type, action, key_code = (identity.get(key) for key in ("eventType", "action", "keyCode"))
    if event_type == "KeyEvent" and key_code and action:
        operation = f"在目标窗口执行 KeyEvent：action={action}，keyCode={key_code}；其余按键顺序与时序待补。"
    elif event_type == "MotionEvent" and action:
        operation = f"在目标窗口执行 MotionEvent：action={action}；坐标、轨迹和完整手势待补，不能仅凭该动作推定点击或滑动。"
    else:
        operation = "补齐并执行原始输入操作；当前未确定具体事件，不能预设点击、滑动或按键。"
    expected = (
        "核验 focused application、实际原生窗口焦点、等待起点、有效 timeout 与到期复查。不是已发布未 ACK。"
        if branch == "no_focused_window" else
        "按同一连接与 native seq 核验发布、逐事件截止时间、FINISHED、waitQueue 清理及剩余队列。"
        if branch == "ack_timeout" else
        "先核验本次属于分发前无焦点等待还是发布后 ACK 等待，不以固定 5 秒作为判据。"
    )
    return [{
        "order": index, "summary": summary, "expectedObservation": observation,
        "supportingEventIds": list(refs), "certainty": "inferred",
        "validation": "unverified", "executed": False, "requiresHumanConfirmation": True,
    } for index, (summary, observation) in enumerate([
        (f"在隔离测试环境核对版本和目标应用 {package or '（包名待补）'}，补齐页面入口及原始前置状态。", "目标窗口/连接/显示屏与事故证据一致；缺失前置条件先补采。"),
        (operation, "采集输入类型/action/keyCode、eventId、native seq、device/source/display 与目标窗口身份。"),
        ("仅在已核验的事故条件下观察等待过程；上游条件未知时先补证，不虚构卡顿或注入故障。", expected),
        ("同步采集 EventLog、InputDispatcher/窗口日志、trace 与必要的 Perfetto，核验 ANR 及后续恢复。", "对齐 reason 描述事件和实际到期条目；对比同身份完成链。未出现同分支 ANR 不算复现成功。"),
    ], 1)]


def render_input_anr_sections(input_anr: dict | None) -> list[str]:
    """Render the same incident contract in reconstruction and pipeline reports."""
    if input_anr is None:
        return []
    lines = ["", "## 超时输入事件", "", f"- 分支：`{input_anr['branch']}`；事件识别：`{input_anr['eventIdentificationStatus']}`。"]
    records = input_anr.get("reportedEvents", [])
    for record in records[:3]:
        identity = record["identity"]
        details = "; ".join(f"{name}={value if value is not None else 'null'}" for name, value in identity.items())
        lines.append(f"- {record['certainty']} / {record['association']}：{details or '具体事件未知'}；证据 {record['evidenceRefs']}。")
    if len(records) > 3:
        lines.append(f"- 其余 {len(records) - 3} 条 reason 见 JSON。")
    if not records or input_anr["eventIdentificationStatus"] == "event_unspecified":
        lines.append("- unknown：无法确定具体超时输入事件，不将附近的 ACTION_DOWN/UP 自动归为触发事件。")
    lines.append("- reason 所指事件与真正最早到期条目分开；`triggeringEvent=null`，未补造 seq、发布时间或截止时间。")
    if input_anr["branch"] == "no_focused_window":
        lines.append("- 无焦点分支描述的是等待投递的输入，不是已发布事件未收到 ACK。")
    lines.extend(["", "## ANR 发生模型", "", "机制检查项不是已证实的连续执行或因果链；未知节点不补成 observed，Trace 仅为采样快照。"])
    for step in input_anr["anrModel"]:
        lines.append(f"- {step['certainty']}：{step['summary']}；证据 {step['supportingEventIds']}。")
    lines.extend(["", "## 模拟操作步骤", "", "候选验证方案：inferred / unverified；executed=false；不是已还原的用户操作记录，不保证复现，未执行设备命令。"])
    for step in input_anr["simulationSteps"]:
        lines.append(f"{step['order']}. {step['summary']} 预期观察：{step['expectedObservation']}")
    lines.append("- 待补参数：" + "、".join(input_anr["missingParameters"]) + "。")
    lines.extend(f"- {gap}" for gap in input_anr["evidenceGaps"])
    return lines + [""]
