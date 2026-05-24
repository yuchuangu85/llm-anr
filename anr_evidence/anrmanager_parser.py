"""Structured parser for AnrManager diagnostic blocks.

The AnrManager block is the *gold-standard* AOSP-side ANR summary:
    - PSI memory pressure (some/full avg10/avg60/avg300)
    - CPU usage window: per-process and total user/kernel/iowait %
    - The ANR reason text (Input dispatching timed out / etc.)
    - The DropBox tracesFile path

Until now we have been shipping the raw text to the AI and asking it to
re-parse those numbers — fragile and lossy. This module turns that block
into a strongly-typed summary with derived hints so the AI consumes
structured fields.
"""

from __future__ import annotations

import re
from typing import Any

# AnrManager logcat lines look like either:
#   07-02 10:14:00.882  1000  1674  2963 I AnrManager: <payload>
#   10-14 15:38:05.086 I/AnrManager( 1377): <payload>
# The payload is what we parse; we strip the leading log header.
_LOG_PREFIX_RE = re.compile(
    r"^\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3}\s+"
    r"(?:(?:\S+\s+){2,3}[A-Z]\s+AnrManager:|[A-Z]/AnrManager\(\s*\d+\):)\s*"
)
_LOAD_RE = re.compile(
    r"^Load:\s*(?P<load1>[\d.]+)\s*/\s*(?P<load5>[\d.]+)\s*/\s*(?P<load15>[\d.]+)"
)
_PSI_RE = re.compile(
    r"^(?P<kind>some|full)\s+avg10=(?P<avg10>[\d.]+)\s+"
    r"avg60=(?P<avg60>[\d.]+)\s+avg300=(?P<avg300>[\d.]+)\s+total=(?P<total>\d+)"
)
_CPU_WINDOW_RE = re.compile(
    r"^CPU usage from (?P<from_ms>-?\d+)ms to (?P<to_ms>-?\d+)ms (?P<direction>ago|later).*:?"
)
_CPU_PROC_RE = re.compile(
    r"^(?P<pct>\+?\d+(?:\.\d+)?)%\s+(?P<pid>\d+)/(?P<name>[^:]+):"
    r"\s+(?P<user>\d+(?:\.\d+)?)%\s+user\s*\+\s*(?P<kernel>\d+(?:\.\d+)?)%\s+kernel"
    r"(?:\s+/\s+faults:\s*(?P<faults>.*))?"
)
_CPU_TOTAL_RE = re.compile(
    r"^(?P<pct>\d+(?:\.\d+)?)%\s+TOTAL:\s+(?P<user>\d+(?:\.\d+)?)%\s+user\s*\+\s*"
    r"(?P<kernel>\d+(?:\.\d+)?)%\s+kernel(?P<rest>.*)"
)
_CPU_TOTAL_COMPONENT_RE = re.compile(r"\+\s*(?P<pct>\d+(?:\.\d+)?)%\s+(?P<name>iowait|irq|softirq)")
_REASON_RE = re.compile(
    r"AnrDumpRecord\{\s*(?P<reason>.*?)\s+ProcessRecord\{(?:\S+\s+)?(?P<pid>\d+):(?P<package>[^/]+)/(?P<user>[^}]+)\}.*?\}"
)
_REASON_LINE_RE = re.compile(r"^Reason:\s*(?P<reason>.+)")
_ANR_IN_RE = re.compile(r"^ANR in (?P<package>\S+)")
_TRACES_FILE_RE = re.compile(r"(?:mTracesFile|tracesFile)\s*=\s*(?P<path>\S+)")


def parse_anrmanager_block(lines: list[str]) -> dict[str, Any]:
    """Extract structured fields from an AnrManager block.

    Returns an empty (but well-formed) dict if no recognisable fields are
    present, so callers can rely on the schema.
    """

    summary: dict[str, Any] = {
        "load": None,
        "pressure": {
            "memory": {"some": None, "full": None},
            "cpu": {"some": None, "full": None},
            "io": {"some": None, "full": None},
        },
        "memoryPressure": {"some": None, "full": None},
        "cpuWindow": None,
        "cpuWindows": [],
        "cpuTotal": None,
        "cpuTopProcesses": [],
        "anrReason": None,
        "anrPackage": None,
        "anrPid": None,
        "tracesFilePath": None,
        "derivedHints": [],
    }
    current_cpu_window: dict[str, Any] | None = None
    current_pressure_section: str | None = None

    for raw_line in lines:
        payload = _LOG_PREFIX_RE.sub("", raw_line).strip()
        if not payload:
            continue

        current_pressure_section = _update_pressure_section(payload, current_pressure_section)

        load_match = _LOAD_RE.match(payload)
        if load_match:
            summary["load"] = {
                "load1": float(load_match.group("load1")),
                "load5": float(load_match.group("load5")),
                "load15": float(load_match.group("load15")),
            }
            continue

        psi_match = _PSI_RE.match(payload)
        if psi_match:
            kind = psi_match.group("kind")
            pressure = {
                "avg10": float(psi_match.group("avg10")),
                "avg60": float(psi_match.group("avg60")),
                "avg300": float(psi_match.group("avg300")),
                "total": int(psi_match.group("total")),
            }
            section = current_pressure_section or "memory"
            summary["pressure"].setdefault(section, {"some": None, "full": None})[kind] = pressure
            if section == "memory":
                summary["memoryPressure"][kind] = pressure
            continue

        cpu_window_match = _CPU_WINDOW_RE.match(payload)
        if cpu_window_match:
            summary["cpuWindow"] = {
                "fromMsAgo": int(cpu_window_match.group("from_ms")),
                "toMsAgo": int(cpu_window_match.group("to_ms")),
            }
            current_cpu_window = {
                "fromMsAgo": int(cpu_window_match.group("from_ms")),
                "toMsAgo": int(cpu_window_match.group("to_ms")),
                "direction": cpu_window_match.group("direction"),
                "processes": [],
                "total": None,
            }
            summary["cpuWindows"].append(current_cpu_window)
            continue

        cpu_total_match = _CPU_TOTAL_RE.match(payload)
        if cpu_total_match:
            cpu_total = {
                "totalPct": float(cpu_total_match.group("pct")),
                "userPct": float(cpu_total_match.group("user")),
                "kernelPct": float(cpu_total_match.group("kernel")),
                "iowaitPct": None,
                "irqPct": None,
                "softirqPct": None,
            }
            for component in _CPU_TOTAL_COMPONENT_RE.finditer(cpu_total_match.group("rest") or ""):
                cpu_total[f"{component.group('name')}Pct"] = float(component.group("pct"))
            summary["cpuTotal"] = cpu_total
            if current_cpu_window is not None:
                current_cpu_window["total"] = cpu_total
            continue

        cpu_proc_match = _CPU_PROC_RE.match(payload)
        if cpu_proc_match:
            process = {
                "totalPct": float(cpu_proc_match.group("pct").lstrip("+")),
                "pid": int(cpu_proc_match.group("pid")),
                "processName": cpu_proc_match.group("name").strip(),
                "userPct": float(cpu_proc_match.group("user")),
                "kernelPct": float(cpu_proc_match.group("kernel")),
                "faults": cpu_proc_match.group("faults"),
            }
            summary["cpuTopProcesses"].append(process)
            if current_cpu_window is not None:
                current_cpu_window["processes"].append(process)
            continue

        reason_match = _REASON_RE.search(payload)
        if reason_match:
            summary["anrReason"] = reason_match.group("reason").strip()
            summary["anrPackage"] = reason_match.group("package").strip()
            summary["anrPid"] = int(reason_match.group("pid"))

        reason_line_match = _REASON_LINE_RE.match(payload)
        if reason_line_match:
            summary["anrReason"] = reason_line_match.group("reason").strip()

        anr_in_match = _ANR_IN_RE.match(payload)
        if anr_in_match:
            summary["anrPackage"] = anr_in_match.group("package").strip()

        traces_match = _TRACES_FILE_RE.search(payload)
        if traces_match:
            summary["tracesFilePath"] = traces_match.group("path").strip()

    summary["cpuTopProcesses"].sort(key=lambda item: -item["totalPct"])
    summary["derivedHints"] = _derive_hints(summary)
    return summary


def _update_pressure_section(payload: str, current: str | None) -> str | None:
    lowered = payload.lower()
    if "----- output from /proc/pressure/memory -----" in lowered:
        return "memory"
    if "----- output from /proc/pressure/cpu -----" in lowered:
        return "cpu"
    if "----- output from /proc/pressure/io -----" in lowered:
        return "io"
    if "----- end output from /proc/pressure/" in lowered:
        return None
    return current


def _derive_hints(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Promote a few high-signal AnrManager findings to typed hints.

    These are *system-level* hints (category=system) that complement the
    per-thread trace hints. They only fire when the parsed numbers cross
    well-established thresholds, so they err on the side of silence.
    """

    hints: list[dict[str, Any]] = []

    # CPU saturation: TOTAL >= 90% during the ANR window strongly suggests
    # CPU starvation; the offending process is the top entry by totalPct.
    cpu_total = summary.get("cpuTotal")
    if cpu_total and cpu_total.get("totalPct", 0) >= 90:
        top = summary["cpuTopProcesses"][0] if summary["cpuTopProcesses"] else None
        msg = f"系统 CPU 总占用 {cpu_total['totalPct']}% (user={cpu_total['userPct']}% kernel={cpu_total['kernelPct']}%)"
        if top:
            msg += f"；最高 {top['totalPct']}% 来自 pid={top['pid']} {top['processName']}"
        hints.append({
            "id": "SYSTEM_CPU_SATURATED",
            "category": "system",
            "severity": "warning",
            "confidence": "strong",
            "scope": "global",
            "cpuTotal": cpu_total,
            "topProcess": top,
            "message": msg + "：系统 CPU 几乎打满，主线程被调度饿死的可能性高。",
            "wikiRefs": ["wiki/AnrManager.md"],
            "nextActions": [
                "确认 top CPU 进程是不是被分析进程本身（autopilot busy loop）还是其他进程（系统压力）",
                "结合 trace 主线程 schedstat.waitNs 验证是否被饿死",
            ],
        })

    # IO wait pressure: iowait >= 20% during the window.
    if cpu_total and cpu_total.get("iowaitPct") and cpu_total["iowaitPct"] >= 20:
        hints.append({
            "id": "SYSTEM_IO_PRESSURE",
            "category": "system",
            "severity": "warning",
            "confidence": "strong",
            "scope": "global",
            "iowaitPct": cpu_total["iowaitPct"],
            "message": (
                f"AnrManager iowait={cpu_total['iowaitPct']}%：磁盘/Flash IO 排队严重，"
                "主线程任何同步 IO（SP / DB / 文件读写）都会被显著放大。"
            ),
            "wikiRefs": ["wiki/AnrManager.md"],
            "nextActions": [
                "查 kernel log 是否有 hung_task、IO scheduler stall",
                "检查应用是否在主线程做 file/db 操作",
            ],
        })

    anr_package = summary.get("anrPackage")
    top_processes = summary.get("cpuTopProcesses") or []
    system_server_top = next((proc for proc in top_processes if proc.get("processName") == "system_server"), None)
    if system_server_top and system_server_top.get("totalPct", 0) >= 80:
        hints.append({
            "id": "SYSTEM_SERVER_CPU_HIGH",
            "category": "system",
            "severity": "warning",
            "confidence": "strong",
            "scope": "global",
            "process": system_server_top,
            "message": (
                f"system_server CPU={system_server_top['totalPct']}%：系统服务侧负载很高，"
                "可能放大 input/window/binder 调度延迟。"
            ),
            "wikiRefs": ["wiki/AnrManager.md"],
            "nextActions": [
                "结合 AnrManager 前后 CPU window 判断 system_server 是否持续最高",
                "检查 system_server trace 中 InputDispatcher/WindowManager/AMS 是否被锁、binder 或调度阻塞",
            ],
        })

    if anr_package:
        anr_process = next((proc for proc in top_processes[:3] if proc.get("processName") == anr_package), None)
        # Target-process overload: when the ANR process itself is above 85%,
        # even a non-saturated TOTAL line should be treated as an app-side load
        # problem first.  The next mandatory step is to correlate target
        # PSS/RSS/heap/GC evidence; high app CPU plus high app memory is a
        # strong memory-leak / memory-bloat candidate, not merely generic
        # scheduler pressure.
        if anr_process and anr_process.get("totalPct", 0) > 85:
            hints.append({
                "id": "ANR_PROCESS_CPU_HIGH",
                "category": "app",
                "severity": "warning",
                "confidence": "strong",
                "scope": "target_process",
                "thresholdPct": 85,
                "process": anr_process,
                "requiresMemoryCorrelation": True,
                "suspectedIssue": "app_load_high_possible_memory_leak",
                "message": (
                    f"目标进程 {anr_package} CPU={anr_process['totalPct']}% (>85%)："
                    "应用自身负载过高。必须结合目标包 meminfo/ANR metadata/GC 证据；"
                    "若同时存在 PSS/RSS/Anon RSS 偏高或 GC 等待，应判为应用负载问题，"
                    "大概率为内存泄漏或内存膨胀导致的 GC/分配抖动。"
                ),
                "wikiRefs": ["wiki/AnrManager.md"],
                "nextActions": [
                    "查看目标进程 trace 中 main/RenderThread/高 CPU 线程的栈，确认是否 GC/分配或 busy loop",
                    "进入 meminfo 跟进目标进程 PSS/RSS/heap/Anon RSS；高内存时优先按内存泄漏/内存膨胀方向排查",
                    "抓取 HProf、GC log、heap histogram 验证泄漏对象或大对象分配来源",
                ],
            })

    # Memory pressure: PSI some.avg10 >= 20 (kernel under memory stress).
    psi = (summary.get("memoryPressure") or {}).get("some") or {}
    if psi.get("avg10", 0) >= 20:
        hints.append({
            "id": "SYSTEM_MEMORY_PRESSURE",
            "category": "system",
            "severity": "warning",
            "confidence": "strong",
            "scope": "global",
            "psiSome": psi,
            "message": (
                f"PSI memory.some avg10={psi['avg10']}: 内核内存压力高，"
                "易触发 GC 暴涨 / kswapd 长时间运行 / LMK 抖动。"
            ),
            "wikiRefs": ["wiki/AnrManager.md"],
            "nextActions": [
                "结合 suspendSummary.stwPauseDetected 排查 STW",
                "查 logcat 是否有 lowmemorykiller 杀进程记录",
            ],
        })

    # ANR reason classification — convert the free-text into a typed hint
    reason = summary.get("anrReason")
    if reason:
        reason_lower = reason.lower()
        if "input dispatching" in reason_lower:
            classified = "input_dispatching_timeout"
        elif "no focused window" in reason_lower or "focused window" in reason_lower:
            classified = "no_focus_window"
        elif "broadcast" in reason_lower:
            classified = "broadcast_timeout"
        elif "provider" in reason_lower:
            classified = "content_provider_timeout"
        elif "jobservice" in reason_lower or "jobscheduler" in reason_lower or "onstartjob" in reason_lower or "onstopjob" in reason_lower:
            classified = "job_scheduler_timeout"
        elif "watchdog" in reason_lower or "swt" in reason_lower:
            classified = "system_watchdog_swt"
        elif "executing service" in reason_lower or "service" in reason_lower:
            classified = "service_timeout"
        else:
            classified = "unknown"
        legacy_anr_type = "provider_timeout" if classified == "content_provider_timeout" else classified
        hints.append({
            "id": "ANR_REASON_CLASSIFIED",
            "category": "system",
            "severity": "info",
            "confidence": "strong",
            "scope": "global",
            "anrType": legacy_anr_type,
            "triggerType": classified,
            "rawReason": reason,
            "anrPackage": summary.get("anrPackage"),
            "anrPid": summary.get("anrPid"),
            "message": f"AnrManager 报告 ANR 类型 = `{classified}` (原文: {reason})",
            "wikiRefs": ["wiki/ANR-类型.md"],
            "nextActions": [
                "把分析重心对齐到该 ANR 类型对应的判定 SOP",
            ],
        })

    return hints


__all__ = ["parse_anrmanager_block"]
