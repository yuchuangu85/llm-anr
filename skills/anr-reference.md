---
name: anr-reference
description: ANR 知识库总索引与技能路由。Use when needing to choose the right ANR skill/reference, locate wiki source files, or quickly map ANR topics to staged Trace, EventLog, Logcat/AnrManager, final ANR synthesis, classification, principle, load, and root-cause guidance.
---

# ANR 速查手册

使用本文件作为 `wiki/` ANR 知识库入口；按任务选择更窄的 skill，再按需回读源 wiki。不要把本文件当作唯一证据来源，具体结论必须回到 trace、event log、AnrManager、logcat 或源文档交叉验证。

## 固定 AI 分析工作流

1. **过滤日志**：对目录、ZIP、TAR 或 fixture 先运行 `python3 scripts/anr_to_ai.py <input> [--package <pkg>] [--anr-type <type>]`，生成 `anr_ai_context/<anr-id>/anr_analysis.md`。
2. **Trace AI 分析**：使用 [anr-trace-analysis.md](anr-trace-analysis.md) 填写 `#### AI Analysis — Trace`。
3. **EventLog AI 分析**：使用 [anr-eventlog-analysis.md](anr-eventlog-analysis.md) 填写 `#### AI Analysis — EventLog`。
4. **Logcat AI 分析**：使用 [anr-logcat-analysis.md](anr-logcat-analysis.md) 填写 `#### AI Analysis — Logcat/AnrManager`，同时覆盖 AnrManager 与 Meminfo follow-up。
5. **最终 ANR AI 分析**：使用 [anr-analysis.md](anr-analysis.md) 填写 `#### AI Analysis — Final ANR`，整合前三段输出最终报告与 JSON tail；综合结论必须写回 `anr_analysis.md`，不得只在聊天回复中输出。

## 技能选择

| 任务 | 优先读取 | 适用场景 |
|---|---|---|
| Trace 专项分析 | [anr-trace-analysis.md](anr-trace-analysis.md) | 主线程、锁/Binder/render/STW/CPU、Trace Hints |
| EventLog 专项分析 | [anr-eventlog-analysis.md](anr-eventlog-analysis.md) | `am_anr` anchor、pre-ANR 事件窗口、生命周期/窗口/焦点/输入序列 |
| Logcat + AnrManager 专项分析 | [anr-logcat-analysis.md](anr-logcat-analysis.md) | 真实触发点、dump 生命周期、AnrManager CPU/PSI/Load、Meminfo follow-up |
| 最终 ANR 固定步骤分析 | [anr-analysis.md](anr-analysis.md) | 汇总三源分析，输出时间线、阻塞点、候选根因链、证据质量、修复建议、JSON tail |
| 判断 ANR 类型/超时/责任线程 | [anr-classification.md](anr-classification.md) | Input、No Focus、Broadcast、Service、ContentProvider、Silent/System ANR 分类 |
| 理解触发机制/源码路径 | [anr-principle.md](anr-principle.md) | 埋雷/拆雷/爆雷、InputDispatcher、Android 10/13/14/15 差异、trace 生成 |
| 解释 trace、CPU、内存、IO、监控信号 | [anr-load.md](anr-load.md) | trace 线程头/状态映射、schedstat、utm/stm、lock owner、Binder/render waits、Load、iowait、kswapd、PSI、WatchDog/监控归因 |
| 根因定位和案例匹配 | [anr-root-cause.md](anr-root-cause.md) | 锁、死锁、Binder、CPU、内存、IO、Surface/Buffer/Vsync、启动/被杀 |

## 分析时的最小证据链

1. `am_anr` / 类型特征：确认 ANR 时间、PID、进程、reason。
2. 类型特定实际触发 log：如 `WindowManager: ANR in`、`BroadcastQueue: Timeout`、`Timeout executing service`。
3. AnrManager block：dump 生命周期、CPU/Load/PSI/内存、trace dump 是否滞后。
4. trace 主线程与相关线程：主线程状态、堆栈、锁关系、Binder 对端、schedstat。
5. ANR 前窗口/生命周期/调度/系统负载日志：`wm_*`、`input_focus`、`Slow dispatch`、LMK、kswapd、mmcqd。

输出结论时保持保守：区分“直接阻塞点”“候选根因”“系统侧诱因”“证据缺口”。不要只凭 `nativePollOnce`、单条 `AnrManager: ANR in` 或 dump 后日志下最终结论。

## Wiki 源文件地图

### 核心流程与规范

- [../ANR-分析流程.md](../ANR-分析流程.md) — 标准化分流流程，尤其 No Focus Window 三步检查。
- [../ANR-规范.md](../ANR-规范.md) — 完整 ANR log 流程、关键日志要素、CPU loading 解读。
- [../ANR关键字.md](../ANR关键字.md) — `am_anr`、`AnrManager`、Slow dispatch/delivery、lowmemorykiller 等关键字。
- [../ANR时间问题.md](../ANR时间问题.md) — dump 时间滞后、真实发生时间与日志时间对齐。
- [../ANR分析.md](../ANR分析.md) — traces、DropBox、CPU 使用率等基础分析入口。

### 分类与原理

- [../ANR-分类.md](../ANR-分类.md) — 场景分类和成因分类。
- [../ANR基础知识.md](../ANR基础知识.md) — Linux/Java/native 状态、trace 字段、负载信息。
- [../ANR原理代码分析.md](../ANR原理代码分析.md) — Service/Broadcast/Provider/Input 触发入口和源码逻辑。
- [../ANR详细对比13&10.md](../ANR详细对比13&10.md) — Android 10 vs 13 Input ANR 检测差异。
- [../机制/](../机制/) — Broadcast、Service、ContentProvider、No Focus Window、Trace 产生过程专题。

### Trace、监控与平台专题

- [../ANR-trace文件分析.md](../ANR-trace文件分析.md) — trace 字段、线程状态、自动分类规则；trace-only 分析需要达到此粒度。
- [../ANR-trace覆盖清单.md](../ANR-trace覆盖清单.md) — 当前 trace 解析覆盖能力和边界。
- [../ANR监控.md](../ANR监控.md) — 监控资料。
- [../DouYin/](../DouYin/) — 抖音 ANR 设计、监控、Barrier、SharedPreferences、自动归因实践。
- [../MTK/swt/](../MTK/swt/) — MTK SWT 分析流程、DB/Log、CPU/IO/Low Memory/Binder/Deadlock 专项。

### 案例库

- [../实例/](../实例/) — Binder、CPU、Input、Locked、SurfaceSyncer、Vsync、Buffer、主线程超时、内存、应用被杀、死锁、负载过高等案例。
- [../Android ANR 系列 1 ：理解 Android ANR 设计思想.md](../Android%20ANR%20系列%201%20：理解%20Android%20ANR%20设计思想.md) — 设计思想。
- [../Android ANR 系列 2 ：ANR 分析套路和关键 Log 介绍.md](../Android%20ANR%20系列%202%20：ANR%20分析套路和关键%20Log%20介绍.md) — 分析套路与关键 log。
- [../Android ANR 系列 3 ：ANR 案例分享.md](../Android%20ANR%20系列%203%20：ANR%20案例分享.md) — 典型案例。
- [../Diagnose and fix ANRs    App quality.md](../Diagnose%20and%20fix%20ANRs%20%20%20%20App%20quality.md) — Android 官方修复指南本地副本。
- [../Find the unresponsive thread    App quality.md](../Find%20the%20unresponsive%20thread%20%20%20%20App%20quality.md) — Android 官方无响应线程定位本地副本。
