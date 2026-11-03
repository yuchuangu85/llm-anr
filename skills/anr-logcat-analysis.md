---
name: anr-logcat-analysis
description: ANR Logcat + AnrManager 专项分析技能。用于分析经过过滤且合并了 AnrManager 原始行的 Logcat、AnrManager Summary 和 Meminfo Target/High-Load Follow-up 证据，涵盖触发点、转储生命周期、CPU/IO/内存压力、焦点/窗口/Surface 序列及恢复/进程被杀流程。
---

# ANR Logcat / AnrManager 分析技能

目标：分析普通 Logcat（其中已合并 AnrManager 原始行）、AnrManager Summary，以及 Meminfo 后续证据。重点确认真实触发点、转储生命周期、系统负载与恢复/进程被杀流程。

## 输入

- `### Logcat` 过滤后的 logcat 日志行。
- `### AnrManager Summary`（若存在）以及 `### Logcat` 中的 AnrManager 原始行。
- `### Meminfo Target/High-Load Follow-up`（若存在）。

## 固定步骤

1. 找真实触发行：InputDispatcher、WindowManager、ActivityManager、BroadcastQueue、服务/ContentProvider 超时等。
2. 区分触发时间、AnrManager 转储时间、`ANR in` 打印时间、完成/进程被杀/重启时间；不要把转储后日志当作前因。
3. 还原焦点/窗口/Surface/转场顺序：焦点来源/去向、relayout、Surface 显示/隐藏、finishDrawing/reportDrawFinished、窗口死亡。
4. 分析 AnrManager：Reason、PID/进程、CPU 统计窗口、CPU TOTAL、iowait、CPU 高占用进程、PSI 内存压力、派生提示、tracesFile。
5. 负载归因顺序固定为：整体 CPU/IO 总负载 → 目标包高负载 → 高负载进程内存证据 → 外部进程压力。
6. 若存在 Meminfo 后续证据，紧跟 AnrManager 负载分析引用 PSS/RSS/堆/空闲内存/可用内存/LMK/OOM/GC 证据。
7. 输出仅基于 Logcat 的结论：真实触发点、系统侧诱因或放大因素、不能证明的线程阻塞点、需要 Trace/EventLog 补证。

## 输出格式

```markdown
#### AI Analysis — Logcat/AnrManager
- 触发行 / 实际超时：...
- 转储生命周期：...
- 窗口/焦点/图形表面序列：...
- 负载 / PSI / meminfo：...
- 仅基于 Logcat 的结论：...
- 证据缺口：...
- 置信度：高|中|低
```

## 保守规则

- `AnrManager: ANR in` 常是 dump 后打印，不默认等于真实触发点。
- CPU/IO/内存只能解释对应时间窗口；转储后压力不能直接解释转储前 ANR。
- 没有 meminfo/GC/LMK/OOM 旁证时，不能直接下“内存泄漏”或“OOM”结论。
