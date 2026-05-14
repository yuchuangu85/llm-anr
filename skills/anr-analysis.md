---
name: anr-analysis
description: ANR 全流程与最终固定步骤分析 skill。Use when analyzing a bugreport, trace, logcat, EventLog, AnrManager block, or generated anr_ai_context; orchestrates the four-stage flow and, after Trace/EventLog/Logcat analyses are filled, synthesizes a conservative final ANR report with timeline, direct blocker, ranked candidate root-cause chains, evidence quality, remediation, and JSON tail.
---

# ANR 全流程与最终固定步骤分析 Skill

目标：从 bugreport/trace/logcat 中形成可审计的 ANR 报告。每个判断都要附证据来源和时间点；证据不足时输出候选链而不是武断结论。最终结论必须保守、区分证据与推断。

## 0. 输入与产物

如果用户给目录、ZIP、TAR 或 fixture，优先生成 AI context：

```bash
python3 scripts/anr_to_ai.py <path_to_bugreport> [--package <pkg>] [--anr-type <type>]
```

然后读取 `anr_ai_context/<group-id>/anr_analysis.md`（包含完整 AI 指令、过滤后证据和四个固定分析位）。按固定顺序填写：

1. `anr-trace-analysis` → `#### AI Analysis — Trace`
2. `anr-eventlog-analysis` → `#### AI Analysis — EventLog`
3. `anr-logcat-analysis` → `#### AI Analysis — Logcat/AnrManager`
4. `anr-analysis` → `#### AI Analysis — Final ANR`

Final ANR 只能在前三段专项分析完成后整合时间线、直接阻塞点、候选根因链（按置信度）、证据质量、修复建议和 JSON tail。
综合分析必须写回同一个 `anr_analysis.md` 的 `#### AI Analysis — Final ANR` 分析位；可以先用 `## 综合分析结论` 摘要，再输出 Timeline / Direct blocking point / Candidate root-cause chains / Evidence quality / Remediation suggestions / JSON tail。不得只在对话中给出综合结论而不落盘。

## 1. 锚定 ANR 类型和真实时间

1. 从 `event_log` 的 `am_anr` 取 PID、进程名、reason、基准时间。
2. 用类型特定 log 校正真实触发时间：
   - Input/No Focus：`WindowManager: ANR in`、`Input dispatching timed out`、`input_focus`。
   - Broadcast：`BroadcastQueue: Timeout of broadcast BroadcastRecord`。
   - Service：`ActivityManager: Timeout executing service:`。
   - ContentProvider：Provider publish timeout 或 `setDetectNotResponding` 触发的 provider not responding。
3. 读取 AnrManager dump 生命周期：`startAnrDump` → `dumpStackTraces begin/end` → `ANR in` → `Completed ANR`。
4. 不要把 `AnrManager: ANR in` 当作真实发生时间；它通常是 dump 后滞后打印。

## 2. 按类型分流

| 类型 | 关键问题 | 首看证据 |
|---|---|---|
| Input dispatch timeout | 主线程是否 5s 内未处理输入？ | main trace、`Slow dispatch`、Binder/锁/IO、CPU/IO 压力 |
| No Focus Window | 焦点窗口为何未建立？ | `wm_on_resume_called`、relayout、finishDrawing、`input_focus`、启动超时/被杀 |
| Broadcast timeout | `onReceive()` 或 `goAsync()` 是否超时？ | `BroadcastRecord`、前台/后台 flag、主线程/工作线程、是否 `finish()` |
| Service timeout | 生命周期方法或冷启动是否超时？ | `handleCreateService`、`handleBindService`、`handleServiceArgs`、冷启动日志 |
| ContentProvider timeout | publish 慢、远程查询慢或启动慢？ | `publishContentProviders`、`ContentProvider$Transport.*`、Binder 线程、客户端设置 timeout |
| SWT/System ANR | system_server/watchdog 是否卡死？ | SWT DB/log、system_server trace、Watchdog、Binder/AMS/WMS 锁链 |

## 3. No Focus Window 三步检查

参考 [../ANR-分析流程.md](../ANR-分析流程.md) 与 [../机制/ANR-HasNoFocusWindow.md](../机制/ANR-HasNoFocusWindow.md)。

1. `wm_on_resume_called` 是否晚于 ANR？晚于则 Activity 未完成 resume，偏应用启动/生命周期问题。
2. resume 后是否在 ANR 前调用 relayout window？未调用偏应用侧；已调用但焦点未更新，继续看 WMS/Input/SF。
3. `reportDrawFinished` / `finishDrawingWindow` 是否在 ANR 前完成？若已完成，应怀疑焦点转移、目标应用、系统或其他窗口问题，而非当前应用主线程阻塞。

## 4. Trace 与阻塞点定位

1. 找 ANR 进程 main thread；System ANR 同时看 `system_server` 关键线程，Input 相关可看 `android.ui`。
2. 读取原始 trace 代码块并结合 Trace Hints 核对；内部 metadata 不在 Markdown 输出中单独展示，引用结论时仍需贴近原始 trace 行或 hint id。
3. 按 `ANR-trace文件分析.md` 的粒度展开主线程字段：
   - 线程头：`name/prio/tid/ART state`；注意 `tid` 是 ART 线程标识，`sysTid` 才是 Linux 线程号。
   - 对象/组：`group/sCount/dsCount/ucsCount/flags/obj/self`，`dsCount` 只能提示调试器挂起历史。
   - 调度上下文：`sysTid/nice/cgrp/sched/handle`、`state/schedstat/utm/stm/core/HZ`；`schedstat=(runNs waitNs timeSlices)`，等待远大于运行只支持“调度等待”候选，必须结合 CPU/Load。
   - 栈帧：top native frame、top Java frame、looper frame、held mutexes、`waiting to lock / waiting on / locked`。
4. 按主线程状态解释：
   - `Runnable`：检查业务代码、schedstat run time、CPU 是否被抢占。
   - `Blocked`：沿 `waiting to lock ... held by tid=N` 找持锁线程，继续追锁链。
   - `Waiting/TimedWaiting`：确认等待对象、notify/CountDownLatch/Thread.sleep 来源。
   - `Native`：区分 Binder、IO、futex、Surface/Buffer/Vsync、正常 idle。
5. 按状态映射写清语义：RUNNABLE/NATIVE、BLOCKED/MONITOR、WAITING/WAIT/VMWAIT、TIMED_WAITING/TIMED_WAIT、SUSPENDED、ZOMBIE/TERMINATED、UNKNOWN，并结合 Linux `R/S/D` 判断执行/睡眠/不可中断等待/采样不明。
6. 只把 `nativePollOnce` 视为“当前快照空闲/等待”，不能单独作为根因。
7. 对 Binder 阻塞，必须找对端进程/线程和服务端堆栈；找不到对端时标记为证据缺口。
8. 对自动分类规则要明确证据门槛：CPU 执行超时需要持续时间或 CPU 旁证；锁竞争需要 owner 线程；死锁需要锁环；Input 需要 InputDispatcher/Slow dispatch；GC/STW 需要多线程暂停或 GC；Render/GPU 需要 main/RenderThread/SF/fence 闭环。

## 5. 负载与系统侧交叉验证

1. CPU 总负载：高于 95% 时考虑 CPU 抢占；80% 以上也可能影响调度。
2. AnrManager 负载归因必须先看 `TOTAL`/`iowait` 判断整体 CPU 或 IO 是否高，再看 Top 进程是否为目标包。
3. 若目标包高负载，继续查该包 meminfo/am_pss、Java/native heap、GC、LMK/OOM、PSI memory，判断是否内存泄漏、内存抖动或 OOM 放大；证据不足时只能写缺口。
4. 若其它进程高负载，继续查该进程内存/IO/GC/LMK 证据，并作为外部系统压力或跨进程影响候选，不能直接归因到目标应用。
5. `iowait`、`mmcqd`、`exe_cq` 高：偏 IO 瓶颈。
6. `kswapd` Top、PSI memory、LMK、可用内存低：偏内存压力。
7. CPU/Load 区间必须贴近 ANR 前几秒至几十秒；过长区间不能排除瞬时高负载。
8. 若 dump 期间耗时很长，需区分“ANR 根因”和“dump 放大/污染”。

## 6. Final ANR 固定步骤

Final ANR 段的输入包括：

- `#### AI Analysis — Trace`
- `#### AI Analysis — EventLog`
- `#### AI Analysis — Logcat/AnrManager`
- 原始证据块与 hints，用于复核引用。

按以下顺序整合：

1. 确认 ANR 类型与 anchor：优先 EventLog `am_anr` 和 AnrManager reason/hint。
2. 汇总 Trace 直接阻塞点：线程、状态、栈、等待对象、owner/peer、Trace Hints。
3. 汇总 EventLog 时间线：ANR 前窗口、生命周期、焦点/输入/进程变化。
4. 汇总 Logcat/AnrManager：真实触发点、dump 生命周期、负载/PSI/meminfo、恢复/kill。
5. 交叉验证三源：一致、矛盾、缺失、fallback anchor 或时间偏差。
6. 输出 Direct blocking point：只写证据能直接支持的阻塞点。
7. 输出 Candidate root-cause chains：触发类型 → 直接阻塞点 → 上游诱因 → 责任边界 → 证据强度，按置信度排序。
8. 输出 Evidence quality：覆盖、缺口、矛盾、primary evidence。
9. 输出 Remediation suggestions：应用侧、系统侧、监控/复现建议。
10. 追加 fenced JSON tail，保持 `finalJudgment=false`、`notRootCauseYet=true`、`requiresHumanConfirmation=true`。

候选链结构：触发类型 → 直接阻塞点 → 上游诱因 → 责任边界 → 证据强度。

示例：

```text
链 A（高置信）：Input dispatch timeout → main BLOCKED 等待 <lock> → worker 持锁执行 SharedPreferences/IO >5s → 应用侧主线程锁竞争。
证据：am_anr 时间、main trace、held by tid、worker trace、Slow dispatch。
缺口：无业务代码上下文/无复现。
```

## 7. 输出报告模板

```markdown
#### AI Analysis — Final ANR

## 综合分析结论
- <一句话 ANR 类型与直接阻塞点>
- <最可信根因链>
- <不支持或降级的候选方向>

## Timeline
- <time> <source> <event>

## Trace evidence analysis
- trace 文件/分组、pid/process、选中 section、dump 时间与 ANR anchor 的关系。
- main thread name/tid/sysTid/prio、ART/Java/Linux state、group/sCount/dsCount/obj/self、nice/cgrp/sched/core、top native/java/looper frames、schedstat/utm/stm/HZ、held mutexes、waitObject/lockOwnerTid。
- Deadlock/Trace Hints 命中情况；直接阻塞类型 lock/binder/io/db/network/render/nativePoll/idle-or-ambiguous。
- owner/peer 线程证据；binder/render/suspend/cpu summary 与主线程的关系；找不到时写明缺口。
- 对每个 trace 自动分类候选写清“已证明 / 未证明 / 需要跨源补证”。

## EventLog evidence analysis
- am_anr 基准：timestamp、pid、process、reason。
- am_anr 前 12 秒内保留的 am_* / wm_* / input_* / power/battery/ssm tag，按时间顺序解释。
- 每条关键事件写明 ΔT、类别（进程/Activity/窗口/焦点/输入/内存等）和对根因链的意义。
- 不要求所有上下文行都包含目标包名；解释 next app、system_server 或其它进程事件如何影响 ANR。

## Logcat and AnrManager evidence analysis
- InputDispatcher / WindowManager / ActivityManager / AnrManager 的关键行和真实触发点。
- focus/window/surface/transition 顺序：focus from/to、relayout、surface show/hide、finishDrawing/reportDrawFinished、window death。
- AnrManager CPU/PSI/Load/trace dump 字段；按 Total 整体负载/IO → 目标包 Top 负载 → 高负载进程内存证据 → 外部进程压力顺序归因；区分 ANR 前、dump 期间、ANR 后恢复/重启证据。
- 若 cache 中存在 `Meminfo Target/High-Load Follow-up`，必须紧跟 AnrManager 负载分析引用该节，验证目标包和 Top 高负载进程的 PSS/RSS/系统内存状态。

## Direct blocking point
- 线程/堆栈/等待对象/对端/负载证据。

## Candidate root-cause chains
1. [High|Medium|Low] ...

## Evidence quality
- 覆盖：trace/logcat/event/AnrManager/CPU/内存/IO。
- 缺口：缺失进程、dump 滞后、时间不一致、对端不可见。
- 交叉验证：Trace ↔ EventLog ↔ Logcat 是否互相印证或矛盾；哪个来源是 primary evidence。

## Remediation suggestions
- 应用侧、系统侧、监控/复现建议分别列出。

```json
{
  "finalJudgment": false,
  "notRootCauseYet": true,
  "requiresHumanConfirmation": true
}
```
```

## 保守规则

- 专项分析之间冲突时，明确冲突并说明哪个来源是 primary evidence。
- 直接阻塞点不等于最终根因；没有上游证据时只给候选链。
- 不允许把单条日志、单份 trace 快照或 dump 后现象当最终定责。

## 回源阅读

- 流程与规范：[../ANR-分析流程.md](../ANR-分析流程.md)、[../ANR-规范.md](../ANR-规范.md)、[../ANR时间问题.md](../ANR时间问题.md)
- 官方指南：[../Diagnose and fix ANRs    App quality.md](../Diagnose%20and%20fix%20ANRs%20%20%20%20App%20quality.md)、[../Find the unresponsive thread    App quality.md](../Find%20the%20unresponsive%20thread%20%20%20%20App%20quality.md)
- 案例：[../实例/](../实例/)、[../Android ANR 系列 3 ：ANR 案例分享.md](../Android%20ANR%20系列%203%20：ANR%20案例分享.md)
- MTK SWT：[../MTK/swt/3.分析流程.md](../MTK/swt/3.分析流程.md)、[../MTK/swt/8.ANR Analysis Flow.md](../MTK/swt/8.ANR%20Analysis%20Flow.md)
