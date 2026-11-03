# 产品需求文档（PRD）— ANR 类型与根因模式扩展

## 元数据
- 状态：草稿已就绪（draft-ready）
- 依赖：
  - `.omx/plans/prd-anr-phase1-evidence-extraction-mvp.md`
  - `.omx/plans/prd-anr-phase2-evidence-normalization.md`
- 主要交付物：新增 ANR 触发类型与根因模式提示的扩展规划契约

## 概述
在现有 `input_dispatching_timeout`、`no_focus_window` 支持基础上，补齐两层扩展规划：

1. **ANR 触发类型策略**：补充广播、服务、ContentProvider、JobScheduler、系统看门狗（System Watchdog/SWT）等触发机制。
2. **根因模式提示**：补充死锁、内存泄漏/内存溢出压力、高负载导致 ANR 等根因模式提示。

本阶段仍遵守既有规划原则：基线提取是硬性保障；类型/根因模板只能进行增量细化；不得删除 P0 证据；不得把提示升级为最终根因裁决。

## 产品目标
给定原始 ANR 日志包或第 1 阶段证据包，系统能够在不破坏既有高召回证据提取契约的前提下：

- 识别更多 ANR 触发类型并应用对应证据过滤策略。
- 识别可疑根因模式并附加为 `rootCausePatternHints[]`。
- 为后续第 3/4 阶段的候选根因链排序与报告生成提供更稳定的输入。

## 关键设计决策
- 将分类输出拆成两层：
  - `triggerType`：ANR 触发机制，例如输入 / 广播 / 服务 / 内容提供程序 / 看门狗。
  - `rootCausePatternHints[]`：可疑根因模式，例如死锁 / 内存压力 / 高负载。
- `silent_anr` 不作为独立触发类型，而作为分类属性：`isSilentAnr=true`。
- 根因模式只作为证据提示，不得在第 1/2 阶段中输出确定性根因。
- `unknown` 触发类型仍必须安全回退；已知根因提示可以与未知触发类型并存。

## 触发类型扩展
新增触发类型策略：

### `broadcast_timeout`
- 关注：`BroadcastQueue Timeout`、`Broadcast of Intent`、接收器的 `onReceive`、`goAsync`、`finish()`。
- 建议窗口：EventLog 前置 90 秒；Logcat 前置 90 秒 / 后置 30 秒。
- 首要线程：同步广播默认为主线程；异步 `goAsync` 检查工作线程/线程池。

### `service_timeout`
- 关注：`Timeout executing service`、前台服务启动、服务生命周期、冷启动。
- 建议窗口：EventLog 前置 240 秒；Logcat 前置 240 秒 / 后置 30 秒。
- 首要线程：主线程。

### `content_provider_timeout`
- 关注：`timeout publishing content providers`、provider not responding（内容提供程序无响应）、内容提供程序冷启动、查询时的 Binder 调用。
- 建议窗口：EventLog 前置 60 秒；Logcat 前置 60 秒 / 后置 30 秒。
- 首要线程：发布过程看内容提供程序主线程；查询过程看内容提供程序 Binder 线程与远端进程。

### `job_scheduler_timeout`
- 关注：`onStartJob`、`onStopJob`、JobService 生命周期、主线程卡顿。
- 建议窗口：EventLog 前置 120 秒；Logcat 前置 120 秒 / 后置 30 秒。
- 首要线程：主线程。

### `system_watchdog_swt`
- 关注：`Watchdog`、`SWT`、`system_server` 堆栈转储、关键系统锁/Binder 调用。
- 建议窗口：EventLog 前置 300 秒；Logcat 前置 300 秒 / 后置 60 秒。
- 首要线程：system_server 中由看门狗监控的线程。

## 根因模式提示
新增根因模式提示策略：

### `deadlock`
- 堆栈转储关注：`waiting to lock`、`held by tid`、环形锁链、主线程阻塞于监视器锁。
- Logcat 关注：`Long monitor contention`、`dvm_lock_sample`、Binder/锁慢调用。
- 输出重点：锁等待链、持锁线程、持锁线程是否也被阻塞、是否形成闭环。

### `memory_leak_oom_pressure`
- Meminfo / AnrManager 关注：RSS/PSS 持续偏高、低内存、PSI 内存压力、LMK、OOM、频繁 GC。
- 内核日志关注：`lowmemorykiller`、`Out of memory`、`kswapd`、内存压力。
- 输出重点：内存压力是直接阻塞因素、放大因素，还是 ANR 后续结果；不得仅凭一次 OOM/LMK 反推内存泄漏。

### `high_load_anr`
- AnrManager 关注：负载（Load）、CPU 使用率统计窗口、TOTAL、CPU 占用最高的进程。
- 堆栈转储关注：主线程处于 `Runnable` 状态但长时间未调度、system_server 或目标进程 CPU 饥饿。
- 内核日志关注：调度（sched）、I/O 等待（iowait）、CPU 压力。
- 输出重点：目标线程是否因 CPU/IO/系统负载无法及时运行，而非自身代码阻塞。

## 功能需求
### FR1 — 增量触发类型策略
每个新增触发类型必须通过策略注册表注册自己的窗口、关键词、回退锚点、分析重点；不得在编排流程中硬编码分支。

### FR2 — 根因提示分离
根因模式必须输出到 `rootCausePatternHints[]`，不得覆盖或替代 `triggerType`。

### FR3 — 证据保留
新增触发类型策略或根因提示策略不得删除基线 P0 证据。

### FR4 — 未知类型兼容性
未知触发类型必须继续走 `unknown` 回退；如果证据命中根因模式，可输出已知提示 + 未知触发类型的部分完成/降级（partial/degraded）证据包。

### FR5 — 提示词措辞
AI 提示词 / 缓存中可以增加根因模式提示区，但必须使用“候选”/“提示”/“证据表明”等保守措辞，不得输出最终根因。

### FR6 — 标准化兼容性
第 2 阶段标准化数据模式不做破坏性变更；新增字段必须可选并向后兼容。

## 验收标准
1. 所有新增触发类型都能通过显式指定 `--anr-type` 选择对应策略。
2. 常见原因/日志模式能推断到对应触发类型。
3. `deadlock`、`memory_leak_oom_pressure`、`high_load_anr` 可作为多选提示附加到分类结果。
4. `triggerType` 与 `rootCausePatternHints[]` 不互相覆盖。
5. 基线 P0 证据不因任何新增策略被删除。
6. 未知触发类型 + 已知根因提示能如实输出回退/降级状态。
7. 多 ANR 输入中，不同 ANR 的触发类型与根因提示证据不得串联。

## 风险与缓解措施
| 风险 | 影响 | 缓解措施 |
| --- | --- | --- |
| 把根因提示误当最终根因 | 高 | 提示词与数据模式都明确提示/候选语义 |
| 新类型窗口过大导致噪音增加 | 中 | 接受高召回优先；后续阶段再优化摘要 |
| 触发类型与根因模式混淆 | 高 | 分类字段分层：`triggerType` 与 `rootCausePatternHints[]` |
| 系统看门狗与应用 ANR 混合分析 | 中 | `system_watchdog_swt` 使用单独策略，保留 system_server 归属关系 |

## 实现说明
- 优先扩展 `SUPPORTED_TYPES`、`TYPE_PATTERNS`、`ANR_TYPE_STRATEGIES`。
- 新增根因提示注册表，避免把提示逻辑塞进触发类型策略。
- 命令行/网页界面文案补齐新触发类型。
- `silent_anr` 作为属性，不进入 `ANR_TYPE_STRATEGIES`。
