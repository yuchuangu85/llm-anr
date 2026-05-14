---
name: anr-logcat-analysis
description: ANR Logcat + AnrManager 专项分析 skill。Use when analyzing filtered Logcat with merged AnrManager raw lines, AnrManager Summary, and Meminfo Target/High-Load Follow-up evidence for trigger point, dump lifecycle, CPU/IO/memory pressure, focus/window/surface sequence, and recovery/kill flow.
---

# ANR Logcat / AnrManager 分析 Skill

目标：分析普通 Logcat（其中已合并 AnrManager 原始行）、AnrManager Summary，以及 Meminfo follow-up。重点确认真实触发点、dump 生命周期、系统负载与恢复/kill 流程。

## 输入

- `### Logcat` 过滤后的 logcat lines。
- `### AnrManager Summary`（若存在）以及 `### Logcat` 中的 AnrManager 原始行。
- `### Meminfo Target/High-Load Follow-up`（若存在）。

## 固定步骤

1. 找真实触发行：InputDispatcher、WindowManager、ActivityManager、BroadcastQueue、Service/Provider timeout 等。
2. 区分触发时间、AnrManager dump 时间、`ANR in` 打印时间、Completed/kill/restart 时间；不要把 dump 后日志当前因。
3. 还原 focus/window/surface/transition 顺序：focus from/to、relayout、surface show/hide、finishDrawing/reportDrawFinished、window death。
4. 分析 AnrManager：Reason、pid/process、CPU window、CPU TOTAL、iowait、Top CPU processes、PSI memory、derived hints、tracesFile。
5. 负载归因顺序固定为：Total 整体 CPU/IO → 目标包 Top 负载 → 高负载进程内存证据 → 外部进程压力。
6. 若存在 Meminfo follow-up，紧跟 AnrManager 负载分析引用 PSS/RSS/heap/free/available/LMK/OOM/GC 证据。
7. 输出 Logcat-only 结论：真实触发点、系统侧诱因或放大因素、不能证明的线程阻塞点、需要 Trace/EventLog 补证。

## 输出格式

```markdown
#### AI Analysis — Logcat/AnrManager
- Trigger line / real timeout: ...
- Dump lifecycle: ...
- Window/focus/surface sequence: ...
- Load / PSI / meminfo: ...
- Logcat-only conclusion: ...
- Evidence gaps: ...
- Confidence: high|medium|low
```

## 保守规则

- `AnrManager: ANR in` 常是 dump 后打印，不默认等于真实触发点。
- CPU/IO/memory 只能解释对应时间窗口；dump 后压力不能直接解释 dump 前 ANR。
- 没有 meminfo/GC/LMK/OOM 旁证时，不能直接下“内存泄漏”或“OOM”结论。
