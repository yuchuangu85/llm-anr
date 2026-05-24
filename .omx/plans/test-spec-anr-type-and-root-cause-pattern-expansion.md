# Test Spec — ANR Type and Root-Cause Pattern Expansion

## Metadata
- Target PRD: `.omx/plans/prd-anr-type-and-root-cause-pattern-expansion.md`
- Depends on:
  - Phase 1 evidence extraction contract
  - Phase 2 evidence normalization contract
- Scope: additional trigger type strategies and root-cause pattern hints

## Test Objective
验证新增 ANR 触发类型与根因模式 hints 能在不破坏 baseline P0 evidence、高召回原则和 fallback/degraded 语义的前提下工作。

## Quality Bar
> **trigger classification and root-cause hints must enrich evidence, never replace or suppress baseline evidence.**

## Fixtures Required
### Trigger type fixtures
新增最少 5 个 clean fixtures：
1. `bcast_01.json` — Broadcast timeout。
2. `svc_01.json` — Service timeout。
3. `cp_01.json` — ContentProvider timeout。
4. `job_01.json` — JobScheduler timeout。
5. `swt_01.json` — System Watchdog/SWT。

### Root-cause hint fixtures
新增最少 3 个 root-cause pattern fixtures：
1. `deadlock_01.json` — main/worker 锁等待链或闭环。
2. `memory_leak_oom_01.json` — meminfo/AnrManager/kernel 显示内存压力或 OOM/LMK 相关证据。
3. `high_load_01.json` — CPU/load/PSI/sched 证据显示高负载或调度饥饿。

### Edge fixtures
新增或复用 degraded/ambiguous fixtures：
- broadcast async `goAsync` 未 finish。
- service 冷启动缺 trace。
- content provider publish vs query 信号混杂。
- system watchdog 缺 app package 或目标进程不明确。
- unknown trigger + deadlock hint。
- unknown trigger + memory/high-load hint。

## Test Dimensions
### T1 — Trigger type inference
验证常见 reason/log pattern 能推断到正确 `triggerType`：
- BroadcastQueue timeout → `broadcast_timeout`
- Timeout executing service → `service_timeout`
- timeout publishing content providers / provider not responding → `content_provider_timeout`
- JobService onStart/onStop timeout → `job_scheduler_timeout`
- Watchdog/SWT/system_server timeout → `system_watchdog_swt`

### T2 — Explicit type override
验证 CLI / API explicit `--anr-type` 能选择对应 strategy；未知 explicit type 仍 fallback 到 `unknown`。

### T3 — Strategy defaults
每个新增 trigger strategy 都要验证：
- event/logcat windows 使用类型默认值。
- event tags / logcat patterns 被用于过滤。
- fallback anchor patterns 生效。
- analysis focus 出现在 AI prompt 或等价输出中。

### T4 — Root-cause hint detection
验证 root-cause hints 可多选输出：
- deadlock fixture 输出 `deadlock`。
- memory fixture 输出 `memory_leak_oom_pressure`。
- high-load fixture 输出 `high_load_anr`。
- 同一个 ANR 可同时有多个 hints。

### T5 — Trigger/hint separation
验证：
- `triggerType` 不被 root-cause hint 覆盖。
- `rootCausePatternHints[]` 不被误写成 trigger type。
- `silent_anr` 只作为 attribute，不进入 trigger type。

### T6 — Baseline P0 preservation
对所有新增类型和 hints，验证 trace / EventLog / logcat / kernel / meminfo 中已存在的 baseline P0 evidence 不会因 strategy 或 hint 过滤被删除。

### T7 — Unknown compatibility
验证 unknown trigger + known root-cause hint 可以输出：
- `triggerType = unknown`
- `rootCausePatternHints[]` 包含命中的 hint
- fallback/degraded status 明确保留

### T8 — Multi-ANR isolation
多 ANR 输入中，每个 ANR group 的 trigger type、root-cause hints、AnrManager/meminfo/trace/logcat/kernel 证据必须只绑定当前 anchor。

## Acceptance Mapping
| PRD acceptance criterion | Verification |
| --- | --- |
| 新 trigger type 可 explicit 选择 | T2 |
| reason/log pattern 推断正确 | T1 |
| root-cause hints 可多选 | T4 |
| trigger 与 hints 分层 | T5 |
| P0 evidence 不被删除 | T6 |
| unknown + known hint fallback 安全 | T7 |
| 多 ANR 不串证据 | T8 |

## Concrete Verification Steps
1. 为 5 类 trigger type 和 3 类 root-cause hint 准备 fixtures。
2. 对每个 fixture 运行 AI context / evidence extraction pipeline。
3. 检查 classification 中的 `triggerType`、`isSilentAnr`、`rootCausePatternHints[]`。
4. 检查 strategy window、keywords、fallback anchors 和 analysis focus。
5. 检查 baseline P0 evidence checklist。
6. 对 unknown/degraded fixtures 验证 fallback 语义。
7. 对 multi-ANR fixture 验证每个 group 的证据隔离。

## Pass/Fail Rules
### Must-pass release conditions
- 5 个新增 trigger type clean fixtures 均能生成 evidence/context 输出。
- 3 个 root-cause hint fixtures 均能输出对应 hint。
- `triggerType` 与 `rootCausePatternHints[]` 分层正确。
- 所有新增策略不删除 baseline P0 evidence。
- unknown/degraded/multi-ANR 场景语义真实。

### Automatic fail conditions
- 根因 hint 被输出为最终根因裁决。
- root-cause hint 覆盖 trigger type。
- 新 strategy 删除已有 P0 evidence。
- unknown trigger 被伪装成 supported trigger。
- 多 ANR 场景串用不同 anchor 的 AnrManager/meminfo/trace/logcat/kernel 证据。

## Suggested Test Levels
### Unit
- type pattern inference tests。
- explicit override tests。
- root-cause hint detector tests。
- trigger/hint schema separation tests。

### Integration
- fixture end-to-end extraction/context generation。
- unknown + known hint fallback flow。
- multi-ANR group isolation。

### Manual
- 抽检 deadlock 锁链是否可追溯到 raw trace。
- 抽检 memory/high-load case 是否只输出 candidate hints，不做最终根因承诺。
- 抽检 AI prompt 是否使用保守措辞。
