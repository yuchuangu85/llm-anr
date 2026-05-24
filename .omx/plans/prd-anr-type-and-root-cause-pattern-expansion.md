# PRD — ANR Type and Root-Cause Pattern Expansion

## Metadata
- Status: draft-ready
- Depends on:
  - `.omx/plans/prd-anr-phase1-evidence-extraction-mvp.md`
  - `.omx/plans/prd-anr-phase2-evidence-normalization.md`
- Primary deliverable: extension planning contract for additional ANR trigger types and root-cause pattern hints

## Summary
在现有 `input_dispatching_timeout`、`no_focus_window` 支持基础上，补齐两层扩展规划：

1. **ANR trigger type strategies**：补充 Broadcast、Service、ContentProvider、JobScheduler、System Watchdog/SWT 等触发机制。
2. **Root-cause pattern hints**：补充死锁、内存泄漏/内存溢出压力、高负载导致 ANR 等根因模式提示。

本阶段仍遵守既有规划原则：baseline extraction 是硬保证；类型/根因模板只能 additive refinement；不得删除 P0 证据；不得把 hint 升级为最终根因裁决。

## Product Goal
给定原始 ANR 日志包或 Phase 1 evidence package，系统能够在不破坏既有高召回证据提取契约的前提下：

- 识别更多 ANR 触发类型并应用对应证据过滤策略。
- 识别可疑根因模式并附加为 `rootCausePatternHints[]`。
- 为后续 Phase 3/4 的候选根因链排序与报告生成提供更稳定的输入。

## Key Design Decisions
- 将分类输出拆成两层：
  - `triggerType`: ANR 触发机制，例如 input / broadcast / service / provider / watchdog。
  - `rootCausePatternHints[]`: 可疑根因模式，例如 deadlock / memory pressure / high load。
- `silent_anr` 不作为独立触发类型，而作为 classification attribute：`isSilentAnr=true`。
- 根因模式只作为 evidence hint，不得在 Phase 1/2 中输出确定性根因。
- `unknown` trigger type 仍必须安全 fallback；已知 root-cause hint 可以与 unknown trigger 并存。

## Trigger Type Expansion
新增 trigger strategies：

### `broadcast_timeout`
- 关注：`BroadcastQueue Timeout`、`Broadcast of Intent`、receiver `onReceive`、`goAsync`、`finish()`。
- 建议窗口：EventLog 前置 90s；Logcat 前置 90s / 后置 30s。
- 首要线程：同步 broadcast 默认 main；异步 `goAsync` 检查 worker/thread pool。

### `service_timeout`
- 关注：`Timeout executing service`、foreground service start、service lifecycle、冷启动。
- 建议窗口：EventLog 前置 240s；Logcat 前置 240s / 后置 30s。
- 首要线程：main thread。

### `content_provider_timeout`
- 关注：`timeout publishing content providers`、provider not responding、provider cold start、query binder。
- 建议窗口：EventLog 前置 60s；Logcat 前置 60s / 后置 30s。
- 首要线程：publish 看 provider main；query 看 provider Binder 线程与远端进程。

### `job_scheduler_timeout`
- 关注：`onStartJob`、`onStopJob`、JobService lifecycle、主线程卡顿。
- 建议窗口：EventLog 前置 120s；Logcat 前置 120s / 后置 30s。
- 首要线程：main thread。

### `system_watchdog_swt`
- 关注：`Watchdog`、`SWT`、`system_server` trace、关键 system lock/binder。
- 建议窗口：EventLog 前置 300s；Logcat 前置 300s / 后置 60s。
- 首要线程：system_server watchdog-monitored threads。

## Root-Cause Pattern Hints
新增 root-cause pattern hint strategies：

### `deadlock`
- Trace 关注：`waiting to lock`、`held by tid`、环形锁链、main thread blocked on monitor。
- Logcat 关注：`Long monitor contention`、`dvm_lock_sample`、binder/lock 慢调用。
- 输出重点：锁等待链、owner thread、owner 是否也被阻塞、是否形成闭环。

### `memory_leak_oom_pressure`
- Meminfo / AnrManager 关注：RSS/PSS 持续偏高、low memory、PSI memory、LMK、OOM、频繁 GC。
- Kernel 关注：`lowmemorykiller`、`Out of memory`、`kswapd`、memory pressure。
- 输出重点：内存压力是直接阻塞因素、放大因素，还是 ANR 后续结果；不得仅凭一次 OOM/LMK 反推内存泄漏。

### `high_load_anr`
- AnrManager 关注：Load、CPU usage windows、TOTAL、top CPU process。
- Trace 关注：main `Runnable` 但长时间未调度、system_server 或目标进程 CPU 饥饿。
- Kernel 关注：sched、iowait、CPU pressure。
- 输出重点：目标线程是否因 CPU/IO/系统负载无法及时运行，而非自身代码阻塞。

## Functional Requirements
### FR1 — Additive trigger strategies
每个新增 trigger type 必须通过 strategy registry 注册自己的窗口、关键词、fallback anchors、analysis focus；不得在 orchestration flow 中硬编码分支。

### FR2 — Root-cause hint separation
根因模式必须输出到 `rootCausePatternHints[]`，不得覆盖或替代 `triggerType`。

### FR3 — Evidence preservation
新增 trigger strategy 或 root-cause hint strategy 不得删除 baseline P0 evidence。

### FR4 — Unknown compatibility
未知触发类型必须继续走 `unknown` fallback；如果证据命中根因模式，可输出 known hint + unknown trigger 的 partial/degraded package。

### FR5 — Prompt wording
AI prompt / cache 中可以增加根因模式提示区，但必须使用 candidate / hint / evidence suggests 等保守措辞，不得输出 final root cause。

### FR6 — Normalization compatibility
Phase 2 normalized schema 不做破坏性变更；新增字段必须可选并向后兼容。

## Acceptance Criteria
1. 所有新增 trigger type 都能通过 explicit `--anr-type` 选择对应 strategy。
2. 常见 reason/log pattern 能推断到对应 trigger type。
3. `deadlock`、`memory_leak_oom_pressure`、`high_load_anr` 可作为多选 hints 附加到 classification。
4. `triggerType` 与 `rootCausePatternHints[]` 不互相覆盖。
5. baseline P0 evidence 不因任何新增 strategy 被删除。
6. unknown trigger + known root-cause hint 能诚实输出 fallback/degraded 状态。
7. 多 ANR 输入中，不同 ANR 的 trigger 与 root-cause hint 证据不得串联。

## Risks and Mitigations
| Risk | Impact | Mitigation |
| --- | --- | --- |
| 把 root-cause hint 误当最终根因 | High | prompt 与 schema 都明确 hint/candidate 语义 |
| 新类型窗口过大导致噪音增加 | Medium | 接受高召回优先；后续 Phase 再优化摘要 |
| trigger type 与 root-cause pattern 混淆 | High | 分类字段分层：`triggerType` vs `rootCausePatternHints[]` |
| system watchdog 与 app ANR 混合分析 | Medium | `system_watchdog_swt` 单独 strategy，保留 system_server ownership |

## Implementation Notes
- 优先扩展 `SUPPORTED_TYPES`、`TYPE_PATTERNS`、`ANR_TYPE_STRATEGIES`。
- 新增 root-cause hint registry，避免把 hint 逻辑塞进 trigger strategy。
- CLI/Web UI 文案补齐新 trigger types。
- `silent_anr` 作为 attribute，不进入 `ANR_TYPE_STRATEGIES`。
