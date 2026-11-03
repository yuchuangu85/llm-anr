---
name: anr-analysis
description: ANR 全流程与最终固定步骤分析技能。用于分析 bugreport、trace、logcat、EventLog、AnrManager 日志块或生成的 anr_ai_context；组织四阶段流程，在填完 Trace/EventLog/Logcat 分析后，综合生成保守的最终 ANR 报告，涵盖时间线、直接阻塞点、按置信度排序的候选根因链、证据质量、修复建议和末尾 JSON。
---

# ANR 全流程与最终固定步骤分析技能

目标：从 bugreport/trace/logcat 中形成可审计的 ANR 报告。每个判断都要附证据来源和时间点；证据不足时输出候选链而不是武断结论。最终结论必须保守、区分证据与推断。

## 0. 输入与产物

如果用户给目录、ZIP、TAR 或测试样例，优先生成 AI 上下文：

```bash
python3 scripts/anr_to_ai.py <path_to_bugreport> [--package <pkg>] [--anr-type <type>]
# 等价入口（默认产物相同）：
python3 -m anr_evidence --build-ai-context <path_to_bugreport> [--package <pkg>] [--anr-type <type>]
```

然后读取 `anr_ai_context/<group-id>/anr_analysis.md`（包含完整 AI 指令、过滤后证据和四个固定分析位）。按固定顺序填写：

1. `anr-trace-analysis` → `#### AI Analysis — Trace 堆栈`
2. `anr-eventlog-analysis` → `#### AI Analysis — EventLog 事件日志`
3. `anr-logcat-analysis` → `#### AI Analysis — Logcat / AnrManager`
4. `anr-analysis` → `#### AI Analysis — 最终 ANR 综合分析`

最终 ANR 综合分析只能在前三段专项分析完成后整合时间线、直接阻塞点、候选根因链（按置信度）、证据质量、修复建议和末尾 JSON。
综合分析必须写回同一个 `anr_analysis.md` 的 `#### AI Analysis — 最终 ANR 综合分析` 分析位；可以先用 `## 综合分析结论` 摘要，再输出时间线 / 直接阻塞点 / 候选根因链 / 证据质量 / 修复建议 / 末尾 JSON。不得只在对话中给出综合结论而不落盘。

两个生成入口默认还会在每个 `anr_ai_context/<group-id>/` 下输出独立的 `anr_reconstruction.md` 和 `anr_reconstruction.json`，`index.json` 通过各组的 `artifactPaths.reconstruction` / `artifactPaths.reconstructionJson` 暴露路径。默认先基于日志生成，AI 槽位未填不阻止输出；原四个槽位和 `logcat.txt` 不变。

填写完四个 AI 槽位后，交付前必须刷新独立重建产物：

```bash
python3 scripts/reconstruct_anr.py <context-root|group-directory|anr_analysis.md>
# 可选外部 AI 报告，仅纳入未验证候选解释：
python3 scripts/reconstruct_anr.py <context-root|group-directory|anr_analysis.md> --ai-report <JSON>
```

上下文根目录处理其中各组，组目录或分析文件只处理该组；刷新不重新提取证据、不改写 AI 槽位。外部报告引用须回到原始证据核验，候选解释不能升级为观测事实。最终交付包含 `anr_analysis.md` 和各组已刷新的两个重建文件路径。详细约定见 [AI 分析指南](../docs/anr-ai-analysis-guide.md)。

## 1. 锚定 ANR 类型和真实时间

1. 从 `event_log` 的 `am_anr` 取 PID、进程名、原因、基准时间。
2. 用类型特定日志校正真实触发时间：
   - 输入分发/无焦点窗口：`WindowManager: ANR in`、`Input dispatching timed out`、`input_focus`。
   - 广播：`BroadcastQueue: Timeout of broadcast BroadcastRecord`。
   - 服务：`ActivityManager: Timeout executing service:`。
   - ContentProvider：发布超时或 `setDetectNotResponding` 触发的提供程序无响应。
3. 读取 AnrManager 转储生命周期：`startAnrDump` → `dumpStackTraces begin/end` → `ANR in` → `Completed ANR`。
4. 不要把 `AnrManager: ANR in` 当作真实发生时间；它通常是转储后滞后打印。

## 2. 按类型分流

| 类型 | 关键问题 | 首看证据 |
|---|---|---|
| 输入分发超时 | 实际等待/处理时限与超时证据是否可核验？ | 主线程 trace、`Slow dispatch`、Binder/锁/IO、CPU/IO 压力 |
| 无焦点窗口 | 焦点窗口为何未建立？ | `wm_on_resume_called`、relayout、finishDrawing、`input_focus`、启动超时/被杀 |
| 广播超时 | `onReceive()` 或 `goAsync()` 是否超时？ | `BroadcastRecord`、前台/后台标志、主线程/工作线程、是否 `finish()` |
| 服务超时 | 生命周期方法或冷启动是否超时？ | `handleCreateService`、`handleBindService`、`handleServiceArgs`、冷启动日志 |
| ContentProvider 超时 | 发布慢、远程查询慢或启动慢？ | `publishContentProviders`、`ContentProvider$Transport.*`、Binder 线程、客户端设置的超时时限 |
| SWT/系统 ANR | system_server/看门狗是否卡死？ | SWT 数据库/日志、system_server trace、Watchdog、Binder/AMS/WMS 锁链 |

### 输入分发超时专项检查

用户明确要求输入分发超时分析，或同一 ANR 的证据指向已发布事件等待完成时，读取 [input-dispatch-timeout-anr-analysis](input-dispatch-timeout-anr-analysis/SKILL.md)。先区分分发前无焦点窗口等待和已发布连接的 ACK 等待；通用 `Input dispatching timed out` 不足以确定分支。

按连接/原生 seq 关联发布、逐事件截止时间、客户端接收/处理/finish、FINISHED 传输与清理；区分最早到期记录和 reason 描述的最早发送记录。Java finish、handled=false 或一条 ACK 都不能单独证明整条连接恢复。完成四阶段分析后按第 0 节刷新独立重建文档，不把机制节点补造成已观察事件。

## 3. 无焦点窗口专项检查

用户明确指定无焦点窗口分析，或同一 ANR 原因/等待证据指向无焦点窗口时，先完整读取 [no-focused-window-anr-analysis](no-focused-window-anr-analysis/SKILL.md)。通用输入超时或单次焦点变化不足以确认该分支；存在 Launcher/Quickstep/Recents 信号时，再叠加 [gesture-anr-analysis](gesture-anr-analysis/SKILL.md)。

1. 确认同一显示屏的焦点应用与实际原生焦点窗口；WMS 焦点请求、Activity 恢复和绘制完成均不等于输入焦点已生效。
2. 关联需要焦点的事件、首次目标查找、有效超时时限、到期复查、重置/恢复和 WMS 最终归因。计时不从启动或原始 eventTime 开始；找不到等待起点时不能用 `am_anr - 5s` 补造。
3. 用 resume → relayout → finishDrawing/reportDrawFinished → 窗口信息/焦点更新定位缺失阶段，再结合 trace/负载/转场证据排序上游原因。日志缺失不等于调用未发生，已画完不能单独排除应用问题，无窗口不能单独证明主线程阻塞。

继续按四个原有槽位分析与落盘，不以专项取代 Trace/EventLog/Logcat 的基线结论。

## 4. Trace 与阻塞点定位

1. 找 ANR 进程主线程；系统 ANR 同时看 `system_server` 关键线程，输入相关问题可看 `android.ui`。
2. 读取原始 trace 代码块并结合 Trace Hints 核对；内部元数据不在 Markdown 输出中单独展示，引用结论时仍需贴近原始 trace 行或提示 ID。
3. 按 `ANR-trace文件分析.md` 的粒度展开主线程字段：
   - 线程头：`name/prio/tid/ART state`；注意 `tid` 是 ART 线程标识，`sysTid` 才是 Linux 线程号。
   - 对象/组：`group/sCount/dsCount/ucsCount/flags/obj/self`，`dsCount` 只能提示调试器挂起历史。
   - 调度上下文：`sysTid/nice/cgrp/sched/handle`、`state/schedstat/utm/stm/core/HZ`；`schedstat=(runNs waitNs timeSlices)`，等待远大于运行只支持“调度等待”候选，必须结合 CPU/Load。
   - 栈帧：栈顶原生帧、栈顶 Java 帧、Looper 帧、持有的互斥锁、`waiting to lock / waiting on / locked`。
4. 按主线程状态解释：
   - `Runnable`：检查业务代码、schedstat 运行时间、CPU 是否被抢占。
   - `Blocked`：沿 `waiting to lock ... held by tid=N` 找持锁线程，继续追锁链。
   - `Waiting/TimedWaiting`：确认等待对象、notify/CountDownLatch/Thread.sleep 来源。
   - `Native`：区分 Binder、IO、futex、Surface/Buffer/Vsync、正常空闲。
5. 按状态映射写清语义：RUNNABLE/NATIVE、BLOCKED/MONITOR、WAITING/WAIT/VMWAIT、TIMED_WAITING/TIMED_WAIT、SUSPENDED、ZOMBIE/TERMINATED、UNKNOWN，并结合 Linux `R/S/D` 判断执行/睡眠/不可中断等待/采样不明。
6. 只把 `nativePollOnce` 视为“当前快照空闲/等待”，不能单独作为根因。
7. 对 Binder 阻塞，必须找对端进程/线程和服务端堆栈；找不到对端时标记为证据缺口。
8. 对自动分类规则要明确证据门槛：CPU 执行超时需要持续时间或 CPU 旁证；锁竞争需要持锁线程；死锁需要锁环；输入问题需要 InputDispatcher/Slow dispatch；GC/STW 需要多线程暂停或 GC；渲染/GPU 问题需要主线程/RenderThread/SF/fence 闭环。

## 5. 负载与系统侧交叉验证

1. CPU 总负载：高于 95% 时考虑 CPU 抢占；80% 以上也可能影响调度。
2. AnrManager 负载归因必须先看 `TOTAL`/`iowait` 判断整体 CPU 或 IO 是否高，再看 Top 进程是否为目标包。
3. 若目标包高负载，继续查该包 meminfo/am_pss、Java/native heap、GC、LMK/OOM、PSI memory，判断是否内存泄漏、内存抖动或 OOM 放大；证据不足时只能写缺口。
4. 若其它进程高负载，继续查该进程内存/IO/GC/LMK 证据，并作为外部系统压力或跨进程影响候选，不能直接归因到目标应用。
5. `iowait`、`mmcqd`、`exe_cq` 高：偏 IO 瓶颈。
6. `kswapd` Top、PSI memory、LMK、可用内存低：偏内存压力。
7. CPU/Load 区间必须贴近 ANR 前几秒至几十秒；过长区间不能排除瞬时高负载。
8. 若 dump 期间耗时很长，需区分“ANR 根因”和“dump 放大/污染”。

## 6. 最终 ANR 综合分析固定步骤

最终 ANR 综合分析段的输入包括：

- `#### AI Analysis — Trace 堆栈`
- `#### AI Analysis — EventLog 事件日志`
- `#### AI Analysis — Logcat / AnrManager`
- 原始证据块与提示，用于复核引用。

按以下顺序整合：

1. 确认 ANR 类型与锚点：优先 EventLog `am_anr` 和 AnrManager 原因/提示。
2. 汇总 Trace 直接阻塞点：线程、状态、栈、等待对象、持锁线程/对端、Trace Hints。
3. 汇总 EventLog 时间线：ANR 前窗口、生命周期、焦点/输入/进程变化。
4. 汇总 Logcat/AnrManager：真实触发点、转储生命周期、负载/PSI/meminfo、恢复/进程被杀。
5. 交叉验证三源：一致、矛盾、缺失、兜底锚点或时间偏差。
6. 输出直接阻塞点：只写证据能直接支持的阻塞点。
7. 输出候选根因链：触发类型 → 直接阻塞点 → 上游诱因 → 责任边界 → 证据强度，按置信度排序。
8. 输出证据质量：覆盖、缺口、矛盾、主要证据。
9. 输出修复建议：应用侧、系统侧、监控/复现建议。
10. 在末尾追加 JSON 代码围栏，保持 `finalJudgment=false`、`notRootCauseYet=true`、`requiresHumanConfirmation=true`。

候选链结构：触发类型 → 直接阻塞点 → 上游诱因 → 责任边界 → 证据强度。

示例：

```text
链 A（候选）：Input dispatch timeout → dump 中 main BLOCKED 等待 <lock> → worker 持锁执行 SharedPreferences/IO → 应用侧主线程锁竞争候选。
证据：am_anr 时间、main trace、held by tid、worker trace、Slow dispatch。
缺口：缺少等待起点与持锁时长证据，不能据 dump 推定连续阻塞 5s；无业务代码上下文/无复现。
```

## 7. 输出报告模板

```markdown
#### AI Analysis — 最终 ANR 综合分析

## 综合分析结论
- <一句话 ANR 类型与直接阻塞点>
- <最可信根因链>
- <不支持或降级的候选方向>

## 时间线
- <时间> <来源> <事件>

## Trace 证据分析
- 堆栈文件/分组、pid/process、选中片段、转储时间与 ANR 锚点的关系。
- 主线程 name/tid/sysTid/prio、ART/Java/Linux 状态、group/sCount/dsCount/obj/self、nice/cgrp/sched/core、顶部原生/Java/消息循环栈帧、schedstat/utm/stm/HZ、持有的互斥锁、waitObject/lockOwnerTid。
- 死锁检测/堆栈提示命中情况；直接阻塞类型：锁、Binder、I/O、数据库、网络、渲染、原生轮询、空闲或不明确。
- 持锁者/对端线程证据；Binder/渲染/暂停/CPU 摘要与主线程的关系；找不到时写明缺口。
- 对每个堆栈自动分类候选写清“已证明 / 未证明 / 需要跨源补证”。

## EventLog 证据分析
- am_anr 基准：时间戳、pid、进程、原因。
- am_anr 前 12 秒内保留的 am_* / wm_* / input_* / power/battery/ssm 标签，按时间顺序解释。
- 每条关键事件写明 ΔT、类别（进程/Activity/窗口/焦点/输入/内存等）和对根因链的意义。
- 不要求所有上下文行都包含目标包名；解释下一个应用、system_server 或其它进程事件如何影响 ANR。

## Logcat 与 AnrManager 证据分析
- InputDispatcher / WindowManager / ActivityManager / AnrManager 的关键行和真实触发点。
- 焦点/窗口/图形表面/转场顺序：焦点来源与去向、重新布局、表面显示/隐藏、finishDrawing/reportDrawFinished、窗口消亡。
- AnrManager CPU/PSI/Load/堆栈转储字段；按整体负载/I/O → 目标包最高负载 → 高负载进程内存证据 → 外部进程压力顺序归因；区分 ANR 前、转储期间、ANR 后恢复/重启证据。
- 若缓存中存在 `Meminfo Target/High-Load Follow-up`，必须紧跟 AnrManager 负载分析引用该节，验证目标包和高负载进程的 PSS/RSS/系统内存状态。

## 直接阻塞点
- 线程/堆栈/等待对象/对端/负载证据。

## 候选根因链
1. [高|中|低] ...

## 证据质量
- 覆盖：trace/logcat/event/AnrManager/CPU/内存/IO。
- 缺口：缺失进程、转储滞后、时间不一致、对端不可见。
- 交叉验证：Trace ↔ EventLog ↔ Logcat 是否互相印证或矛盾；哪个来源是主要证据。

## 修复建议
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

- 专项分析之间冲突时，明确冲突并说明哪个来源是主要证据。
- 直接阻塞点不等于最终根因；没有上游证据时只给候选链。
- 不允许把单条日志、单份 trace 快照或 dump 后现象当最终定责。
- 独立重建区分 `observed`（日志观察）、`inferred`（机制或 AI 假设）、`unknown`（缺口），逐事件给出时间、ΔT、身份与原始证据定位；缺失字段明确记为缺口。
- 时间先后不等于因果；不推定未观测的 5s 等待，不将 dump 当作整个超时窗口的连续阻塞。证据重建不是真实执行重放，现有 `replay` 语义仍是回归重跑。

## 回源阅读

- 流程与规范：[../ANR-分析流程.md](../ANR-分析流程.md)、[../ANR-规范.md](../ANR-规范.md)、[../ANR时间问题.md](../ANR时间问题.md)
- 官方指南：[../Diagnose and fix ANRs    App quality.md](../Diagnose%20and%20fix%20ANRs%20%20%20%20App%20quality.md)、[../Find the unresponsive thread    App quality.md](../Find%20the%20unresponsive%20thread%20%20%20%20App%20quality.md)
- 案例：[../实例/](../实例/)、[../Android ANR 系列 3 ：ANR 案例分享.md](../Android%20ANR%20系列%203%20：ANR%20案例分享.md)
- MTK SWT：[../MTK/swt/3.分析流程.md](../MTK/swt/3.分析流程.md)、[../MTK/swt/8.ANR Analysis Flow.md](../MTK/swt/8.ANR%20Analysis%20Flow.md)
