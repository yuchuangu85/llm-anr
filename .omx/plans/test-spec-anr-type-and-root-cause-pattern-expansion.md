# 测试规范 — ANR 类型与根因模式扩展

## 元数据
- 目标产品需求文档：`.omx/plans/prd-anr-type-and-root-cause-pattern-expansion.md`
- 依赖：
  - 第 1 阶段证据提取契约
  - 第 2 阶段证据标准化契约
- 范围：新增触发类型策略与根因模式提示

## 测试目标
验证新增 ANR 触发类型与根因模式提示能在不破坏基线 P0 证据、高召回原则和回退/降级语义的前提下工作。

## 质量标准
> **触发类型分类与根因提示必须丰富证据，绝不能替代或抑制基线证据。**

## 所需测试样例
### 触发类型测试样例
新增最少 5 个无歧义测试样例：
1. `bcast_01.json` — 广播超时。
2. `svc_01.json` — 服务超时。
3. `cp_01.json` — ContentProvider 超时。
4. `job_01.json` — JobScheduler 超时。
5. `swt_01.json` — 系统看门狗（System Watchdog/SWT）。

### 根因提示测试样例
新增最少 3 个根因模式测试样例：
1. `deadlock_01.json` — 主线程/工作线程的锁等待链或闭环。
2. `memory_leak_oom_01.json` — meminfo/AnrManager/内核日志显示内存压力或 OOM/LMK 相关证据。
3. `high_load_01.json` — CPU/负载/PSI/调度证据显示高负载或调度饥饿。

### 边界测试样例
新增或复用降级/类型模糊测试样例：
- 广播异步 `goAsync` 后未通过 finish 完成处理。
- 服务冷启动缺少堆栈转储。
- 内容提供程序发布与查询信号混杂。
- 系统看门狗缺少应用包名或目标进程不明确。
- 未知触发类型 + 死锁提示。
- 未知触发类型 + 内存/高负载提示。

## 测试维度
### T1 — 触发类型推断
验证常见原因/日志模式能推断到正确 `triggerType`：
- BroadcastQueue timeout（广播队列超时）→ `broadcast_timeout`
- Timeout executing service（服务执行超时）→ `service_timeout`
- timeout publishing content providers / provider not responding（内容提供程序发布超时 / 无响应）→ `content_provider_timeout`
- JobService onStart/onStop timeout（JobService 启动/停止回调超时）→ `job_scheduler_timeout`
- Watchdog/SWT/system_server timeout（看门狗/SWT/system_server 超时）→ `system_watchdog_swt`

### T2 — 显式类型覆盖
验证 CLI / API 显式指定 `--anr-type` 能选择对应策略；显式指定的未知类型仍回退到 `unknown`。

### T3 — 策略默认值
每个新增触发类型策略都要验证：
- 事件日志/logcat 窗口使用类型默认值。
- 事件标签 / logcat 模式被用于过滤。
- 回退锚点模式生效。
- 分析重点出现在 AI 提示词或等价输出中。

### T4 — 根因提示检测
验证根因提示可多选输出：
- 死锁测试样例输出 `deadlock`。
- 内存测试样例输出 `memory_leak_oom_pressure`。
- 高负载测试样例输出 `high_load_anr`。
- 同一个 ANR 可同时有多个提示。

### T5 — 触发类型与提示分离
验证：
- `triggerType` 不被根因提示覆盖。
- `rootCausePatternHints[]` 不被误写成触发类型。
- `silent_anr` 只作为属性，不进入触发类型。

### T6 — 基线 P0 证据保留
对所有新增类型和提示，验证堆栈转储 / EventLog / logcat / 内核日志 / meminfo 中已存在的基线 P0 证据不会因策略或提示过滤被删除。

### T7 — 未知类型兼容性
验证未知触发类型 + 已知根因提示可以输出：
- `triggerType = unknown`
- `rootCausePatternHints[]` 包含命中的提示
- 明确保留回退/降级状态

### T8 — 多 ANR 隔离
多 ANR 输入中，每个 ANR 分组的触发类型、根因提示、AnrManager/meminfo/堆栈转储/logcat/内核日志证据必须只绑定当前锚点。

## 验收映射
| 产品需求文档验收标准 | 验证项 |
| --- | --- |
| 新触发类型可显式选择 | T2 |
| 原因/日志模式推断正确 | T1 |
| 根因提示可多选 | T4 |
| 触发类型与提示分层 | T5 |
| P0 证据不被删除 | T6 |
| 未知类型 + 已知提示安全回退 | T7 |
| 多 ANR 不串证据 | T8 |

## 具体验证步骤
1. 为 5 类触发类型和 3 类根因提示准备测试样例。
2. 对每个测试样例运行 AI 上下文 / 证据提取流水线。
3. 检查分类结果中的 `triggerType`、`isSilentAnr`、`rootCausePatternHints[]`。
4. 检查策略窗口、关键词、回退锚点和分析重点。
5. 检查基线 P0 证据清单。
6. 对未知类型/降级测试样例验证回退语义。
7. 对多 ANR 测试样例验证每个分组的证据隔离。

## 通过/失败规则
### 发布必须通过的条件
- 5 个新增触发类型的无歧义测试样例均能生成证据/上下文输出。
- 3 个根因提示测试样例均能输出对应提示。
- `triggerType` 与 `rootCausePatternHints[]` 分层正确。
- 所有新增策略不删除基线 P0 证据。
- 未知类型/降级/多 ANR 场景语义真实。

### 自动判定失败的条件
- 根因提示被输出为最终根因裁决。
- 根因提示覆盖触发类型。
- 新策略删除已有 P0 证据。
- 未知触发类型被伪装成受支持的触发类型。
- 多 ANR 场景串用不同锚点的 AnrManager/meminfo/堆栈转储/logcat/内核日志证据。

## 建议测试层级
### 单元测试
- 类型模式推断测试。
- 显式覆盖测试。
- 根因提示检测器测试。
- 触发类型/提示的数据模式分离测试。

### 集成测试
- 测试样例端到端提取/上下文生成。
- 未知类型 + 已知提示的回退流程。
- 多 ANR 分组隔离。

### 人工测试
- 抽检死锁锁链是否可追溯到原始堆栈转储。
- 抽检内存/高负载用例是否只输出候选提示，不做最终根因承诺。
- 抽检 AI 提示词是否使用保守措辞。
