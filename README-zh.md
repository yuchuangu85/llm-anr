# agent-anr

> [项目主说明](README.md)

`agent-anr` 是一个智能体驱动的 Android ANR（应用无响应）证据抽取与 AI 辅助分析工具链。它会把堆栈、EventLog、logcat、AnrManager 转储、meminfo、内核日志等原始材料转成按 ANR 独立隔离、可审计的证据工作区，再引导 AI/人工分析者按来源逐段完成保守分析。

项目目标是 **高召回、可审计、不过早定责**：触发类型、根因模式提示和修复建议都只是有证据支撑的候选结论，最终仍需要人工确认。

## 运行要求

- Python 3.12 或更新版本。CI 验证 Python 3.12 和 3.13。
- 运行时依赖：仅 Python 标准库。

## 当前能力

### 证据抽取与路由

- 支持加载 JSON 测试样例、已解压日志目录、bugreport 目录、ZIP 归档和各种 TAR 归档。
- 自动发现常见厂商/系统目录，包括 `data/anr/`、`FS/data/anr/`、`System_log/`、`event-log/`、`logs/`、`android-logs/`、`dropbox/`、`meminfo.txt`、`last_kmsg`、`lastkmsg`、`console_ramoops`。
- 有目标包名时优先使用包名匹配的 `am_anr` 作为主锚点；严格包名匹配，避免无关 ANR 被当成当前分析锚点。
- EventLog 锚点缺失时，会保留堆栈推断出的 ANR 时间，避免生成无时间信息的回退目录。
- 同一份输入中存在多个 ANR 时，会拆成多个独立 `anr-*` 工作区；AnrManager、meminfo、堆栈、EventLog、logcat、内核证据禁止跨锚点串用。
- 基线证据是硬保证：类型策略和根因提示只能增加关注点、窗口和关键词，不能删除关键证据来源。

### 分来源分析支持

- **Trace 堆栈**：主线程状态、schedstat、Java/ART/Linux 状态映射、锁等待、锁持有者及相关线程、Binder 等待、渲染/栅栏等待、原生轮询解释、死锁检测、堆栈提示。
- **EventLog**：以 `am_anr` 为锚点的 ANR 前窗口、AM/WM/Input/进程/内存标签保留、ΔT 解读，以及“只对锚点做包名过滤、不误删系统上下文行”的过滤策略。
- **Logcat / AnrManager**：完整提取 AnrManager 转储流程，包括 `startAnrDump`、堆栈转储、`ANR in`、原因（Reason）、负载（Load）、PSI、CPU 统计窗口、`TOTAL`、CPU 占用最高的进程、DropBox、转储完成等关键行。
- **Meminfo**：作为 AnrManager 后置跟进证据，按 ANR 锚点选择最近快照，关联目标包和高负载进程的 PSS/RSS。
- **内核日志**：作为基线上下文保留调度器、Binder、任务挂起、低内存、OOM/LMK、压力等信号。
- **上下文控制**：过滤后的 logcat 单独写入每个 ANR 目录的 `logcat.txt`，不再全部内联进主分析文件，降低上下文膨胀，同时保留可审计证据。

### 分类与候选根因提示

`triggerType` 和 `rootCausePatternHints[]` 是两个独立层次：

- `triggerType` 表示 ANR 触发机制。
- `rootCausePatternHints[]` 表示候选根因模式，只用于丰富分析，不覆盖触发类型，也不等于最终根因。

已支持的触发类型策略：

| 触发类型 | 分析重点 |
|---|---|
| `input_dispatching_timeout` | InputDispatcher 超时、分发缓慢、焦点窗口状态、主线程 Binder/锁/IO/渲染等待、CPU/IO 压力 |
| `no_focus_window` | 焦点/窗口/表面生命周期、Activity 恢复、relayout、finishDrawing、无焦点窗口（`no focused window`） |
| `broadcast_timeout` | BroadcastQueue 超时、`Broadcast of Intent`、广播接收器 `onReceive`、`goAsync`、`finish()` |
| `service_timeout` | `Timeout executing service`、前台服务启动、服务生命周期、冷启动 |
| `content_provider_timeout` | 内容提供者发布超时、无响应、冷启动、查询/Binder 等待 |
| `job_scheduler_timeout` | JobService `onStartJob` / `onStopJob`、JobScheduler 调度、服务生命周期 |
| `system_watchdog_swt` | system_server Watchdog/SWT、被监控的 Handler、系统锁、Binder 线程 |
| `unknown` | 未知/未来 ANR 类型的安全回退；保留基线证据和已命中的提示 |

已支持的根因模式提示：

| 提示 | 含义 |
|---|---|
| `deadlock` | 死锁、自锁、锁持有者阻塞链、长时间监视器/互斥锁竞争 |
| `memory_leak_oom_pressure` | 内存泄漏/内存膨胀/OOM/LMK/PSI 内存压力候选 |
| `high_load_anr` | 高 CPU 占用、IO 等待、负载（Load）、调度压力、目标进程或系统进程过载候选 |

## 通过 AI 智能体交互式使用

在仓库根目录启动你的 AI 编程智能体（Claude Code / Codex CLI / Hermes 等），然后输入自然语言指令：

```text
分析 <log目录路径> 目录下包名为 com.example.app 的 ANR 原因
```

智能体应完成以下步骤：

1. 运行 `python3 scripts/anr_to_ai.py <路径> --package com.example.app`；默认在输入日志目录下生成 `anr_ai_context/`（可用 `--out-dir` 覆盖）。
2. 先查看命令输出提示的 `docs/anr-ai-analysis-guide.md`；说明不再重复写入每个 `anr_analysis.md`。
3. 打开输入日志目录下的 `anr_ai_context/index.json`，按索引逐个分析每个 ANR 工作区。解释证据前，按用户请求及当前组的原因/信号加载适用的[专项分析技能](#anr-专项分析技能)。
4. 按顺序填写输入日志目录下 `anr_ai_context/<anr-id>/anr_analysis.md` 中的四个分析槽位：Trace → EventLog → Logcat/AnrManager → 最终 ANR 综合分析。
5. Logcat/AnrManager 槽位引用 `logcat.txt` 时，必须读取该工作区同目录下的 `logcat.txt`。
6. 回复前必须把综合结论写回最终 ANR 综合分析槽位（`#### AI Analysis — 最终 ANR 综合分析`）。
7. 填完四个槽位后，必须运行 `python3 scripts/reconstruct_anr.py <context-root|group-directory|anr_analysis.md>` 刷新各组独立重建产物，不重新提取证据、不改写 AI 槽位。
8. 最终输出结构化报告：时间线、直接阻塞点、候选根因链排序、证据质量、修复建议，并交付各组的 `anr_analysis.md` 与已刷新的 `anr_reconstruction.md`、`anr_reconstruction.json` 路径。

## 快速开始：生成 AI 分析工作区

推荐入口：

```bash
python3 scripts/anr_to_ai.py <bugreport_dir_or_archive_or_fixture> \
  [--package <package.name>] \
  [--anr-type input_dispatching_timeout|no_focus_window|broadcast_timeout|service_timeout|content_provider_timeout|job_scheduler_timeout|system_watchdog_swt]

# 等价入口（默认产物相同）：
python3 -m anr_evidence --build-ai-context <bugreport_dir_or_archive_or_fixture> [--package <package.name>]
```

默认输出目录（输入为日志目录时）：

```text
<输入日志目录>/anr_ai_context/
  index.json
  anr-<timestamp-or-anchor>/
    anr_analysis.md
    logcat.txt
    anr_reconstruction.md
    anr_reconstruction.json
```

说明：

- 独立脚本默认在目录输入内部、或归档/测试样例文件的同级目录生成工作区；模块入口默认输出到 `./anr_ai_context`。两种入口都可通过 `--out-dir <目录>` 显式指定位置。
- `index.json` 是所有 ANR 组的权威索引。
- `anr_analysis.md` 是人工/AI 工作区：分析指令、过滤证据摘要、分来源分析槽位和最终综合槽位都在同一个文件中。
- `logcat.txt` 保存当前单个 ANR 组的过滤后 logcat 行。
- `anr_reconstruction.md` 和 `anr_reconstruction.json` 是独立的证据驱动 ANR 过程重建产物，默认先基于日志生成，AI 槽位未填不阻止输出。每个 ANR 组独立保留 `<group-id>/anr_reconstruction.md`，不生成跨组汇总的重建 Markdown。`index.json` 通过各组的 `artifactPaths.reconstruction` / `artifactPaths.reconstructionJson` 暴露路径；原四个分析槽位和 `logcat.txt` 保持不变。
- 顶层旧式 `cache.md`、`ai_prompt.md`、`summary.json` 不再是主流程产物；生成产物时会清理陈旧文件。

重建区分 `observed`（日志观察）、`inferred`（机制或 AI 假设）、`unknown`（缺口）：

- `anr_reconstruction.md` 是每个 ANR 的紧凑视图：最多展示 24 个关键时间线节点、简短证据摘录与来源坐标、最多三条未验证候选摘要，以及重要缺口/警告。连续、内容相同且时间可解析的记录合并为首末观测端点和次数；不同身份、存在中间观察或时间未知的记录不合并。端点不能证明期间状态持续成立，未展开记录仍保留在 JSON。
- `anr_reconstruction.json` 保留全部观测事件、证据引用、AI 原文和关系，包括紧凑 Markdown 省略的细节；逐事件时间、可比较时的 ΔT、身份和原始证据定位仍保留在 JSON 中。
- 紧凑 Markdown 不逐条重复所有原始日志或堆栈、每条关系边、四个 AI 槽位全文或候选 JSON。完整分析仍保留在 `anr_analysis.md`；摘要不代表候选解释已核实，也不改变保守标志。

时间先后不等于因果；不能推定未观测的 5 秒等待，也不能将转储快照当作持续阻塞。这是证据重建，不是真实执行重放；现有 `replay` 仍表示回归重跑。

四个 AI 槽位填完后，交付前必须刷新独立产物：

```bash
python3 scripts/reconstruct_anr.py <context-root|group-directory|anr_analysis.md>
# 等价模块入口：
python3 -m anr_evidence --reconstruct <context-root|group-directory|anr_analysis.md>
# 可选外部 AI 报告，仅作为未验证候选解释：
python3 scripts/reconstruct_anr.py <context-root|group-directory|anr_analysis.md> --ai-report <JSON>
```

传入上下文根目录处理其中所有组，组目录或分析文件只处理该组。命令不重新提取证据、不改写 AI 槽位；外部报告引用必须回到原始证据核验，报告本身不能升级为 `observed` 事实。

重建与重新生成的保护机制：

- 多组外部 AI 报告必须通过 `{"groups": [{"groupId": "<目标组 id>", "candidateChains": [...]}]}` 显式归属。引用应使用当前组 `anr_reconstruction.json` 中真实事件的 `eventId`，或唯一精确匹配的完整 `rawLine`；`references_resolved` 仅表示引用存在，不代表解释或因果链已核实。
- 刷新会检查工作区证据摘要。若手动修改了过滤证据或锚点元数据，必须先重新提取再刷新；只编辑 AI 槽位不需要重新提取。
- 重新生成会保留已填写的 AI 槽位。同一输入的证据变化时，`analysisRevision` 将旧分析标为 `stale` 或 `unknown`，不视为已重新核验。复用输出目录处理不同输入时，会校验 `inputIdentity` 并另建组目录，保留旧目录和人工分析；当前索引只列出本次输入的组。
- 墙上时钟与运行时间（uptime）/启动时间（boottime）证据不会自动跨时钟校准；无法映射的时钟域分开呈现，不跨时钟排序或计算 ΔT。时间接近或单份堆栈快照不能证明整个超时区间持续阻塞。

可选导出组内逐行证据和相对时间：

```bash
python3 scripts/anr_to_ai.py <input> --build-ess --delta-t-normalized
# 额外生成 evidence.jsonl 和 time_normalized.json；不执行跨时钟校准。
```

详细边界与最终交付要求见[分析指南](docs/anr-ai-analysis-guide.md)。

## 四阶段 AI 分析契约

每个 `anr_analysis.md` 固定包含四个分析槽位，必须按顺序填写：

1. [`anr-trace-analysis`](skills/anr-trace-analysis.md) → 仅做 Trace 堆栈分析，写入 `#### AI Analysis — Trace 堆栈`。
2. [`anr-eventlog-analysis`](skills/anr-eventlog-analysis.md) → 仅做 EventLog/锚点分析，写入 `#### AI Analysis — EventLog 事件日志`。
3. [`anr-logcat-analysis`](skills/anr-logcat-analysis.md) → Logcat + AnrManager + meminfo 跟进分析，写入 `#### AI Analysis — Logcat / AnrManager`。
4. [`anr-analysis`](skills/anr-analysis.md) → 最终跨来源 ANR 综合分析，写入 `#### AI Analysis — 最终 ANR 综合分析`。

约束：

- 前三段只做分来源分析，不能提前下最终根因。
- 最终 ANR 综合分析只能在 Trace、EventLog、Logcat/AnrManager 三段完成后整合。
- 综合结论必须写回同一个 `anr_analysis.md`，不要只在聊天中输出。
- 最终 Markdown 应包含：综合分析结论、时间线、Trace 证据分析、EventLog 证据分析、Logcat/AnrManager 证据分析、直接阻塞点、候选根因链、证据质量、修复建议，以及位于末尾的 JSON 围栏代码块。
- JSON 尾部默认保留保守字段：
  - `finalJudgment = false`
  - `notRootCauseYet = true`
  - `requiresHumanConfirmation = true`

## ANR 专项分析技能

这些仓库内置技能用于指导 AI 智能体在基线提取后做增量分析，不是新增 CLI 触发类型，也不自动判定根因。解释证据前应完整阅读适用的技能。仅出现通用 `Input dispatching timed out` 时，应先区分分发前焦点等待、已发布事件的完成等待，或保留分支未知。

### 输入分发超时：已发布事件的完成 / ACK 等待

用户明确请求（包括指定 `input_dispatching_timeout`），或同一 ANR 显示已发布事件等待 FINISHED、连接存在未完成的 `waitQueue` / `AnrTracker` 记录，或通道的 `is not responding. Waited ...ms for ...` 原因指向未完成派发时，使用 [`input-dispatch-timeout-anr-analysis`](skills/input-dispatch-timeout-anr-analysis/SKILL.md)。

- 用连接/输入通道令牌与原生分发序号（`seq`）关联发布、逐事件投递/截止时间、接收端处理、完成处理（`finish`）、FINISHED 传输、Dispatcher 清理及剩余队列恢复。
- 最早到期的记录可能不同于原因描述的最早发送记录；不能仅凭 `Waited N ms` 反推配置阈值。
- Java 完成处理（`finish`）返回、`handled=false` 或单个 ACK 均不能单独证明连接已恢复。纯无焦点窗口等待属于下方独立专项，不能套用 ACK 超时机制。

### 无焦点窗口：目标分发前等待

用户明确请求（包括指定 `no_focus_window`），或同一 ANR 出现 `does not have a focused window`、`no focused window`、`no focus window`、`no window has focus`，或已有焦点应用而需要焦点的事件正在等待窗口时，使用 [`no-focused-window-anr-analysis`](skills/no-focused-window-anr-analysis/SKILL.md)。

- 区分焦点应用与真正生效的原生输入窗口焦点；Activity 恢复、绘制完成或 WMS 焦点请求本身都不等于已获得焦点。
- 按同一显示屏/应用/窗口身份关联首次目标查找、实际配置阈值、到期复查、重置/恢复及 WMS 归因。
- 这是目标分发前的等待，不是已分发未 ACK，也不单独证明主线程阻塞。没有等待起点与阈值证据时，不得用 `am_anr - 5s` 补造起点。

### Launcher / Quickstep / Recents 动画

用户明确要求 Launcher/Quickstep 手势导航或 Recents 动画 ANR 分析，或同一 ANR **同时具有**输入/焦点锚点（输入超时、无焦点窗口、或目标已绘制但仍 `NOT_VISIBLE`）**以及** Launcher/Recents 信号，如 `recents_animation_input_consumer`、`OtherActivityInputConsumer`、`AbsSwipeUpHandler`、`startRecentsAnimation`、`onTasksAppeared`、`finishRecentsAnimation`、`mRecentsAnimationStartPending` 时，使用 [`gesture-anr-analysis`](skills/gesture-anr-analysis/SKILL.md)。

- 将 `am_anr` 与同一手势的 `logId` / 处理器哈希、Recents 启动/回调/结束状态、过渡可见性和窗口焦点串联，关键结论附原始日志片段及来源定位。
- 框架回调日志不证明指定处理器收到回调；缺少 `trackEvent(NO_OP)` 文本不证明事件未执行。时间接近只能建立候选，不能直接定根因。
- 没有手势/Recents 信号的通用输入超时/无焦点 ANR 不加载此专项。本专项只分析已抓取日志；缺失设备转储应列为证据缺口，不能假定其结果。

仅当各自触发条件满足时，才将手势技能与 ACK 超时或无焦点窗口技能叠加。若两条输入分支都有证据，必须分开记录各自事件和计时起点。所有专项均保留基线来源，写回原有四槽位（不新增第五槽位），保留保守标志，并在交付前刷新独立重建。

## 确定性第 1 至 8 阶段流水线

不使用 AI 智能体时，也可以运行确定性证据流水线：

```bash
# 第 1 阶段：证据抽取
python3 -m anr_evidence tests/fixtures/nfw_01.json

# 第 2 阶段：标准化
python3 -m anr_evidence --normalize tests/fixtures/nfw_01.json

# 第 3 阶段：辅助非最终分析
python3 -m anr_evidence --analyze tests/fixtures/nfw_01.json

# 第 4/5 阶段：候选因果链
python3 -m anr_evidence --hypothesize tests/fixtures/nfw_01.json

# 第 6 阶段：保守根因候选报告
python3 -m anr_evidence --root-cause tests/fixtures/nfw_01.json

# 第 7 阶段：受门控约束的修复草案
python3 -m anr_evidence --remediate tests/fixtures/nfw_01.json

# 第 8 阶段：最终交付 Markdown 模板
python3 -m anr_evidence --deliver tests/fixtures/nfw_01.json
```

所有确定性阶段都保持候选语义：输出证据、缺口和假设，不做不可逆最终定责。

## 分来源 CLI 工具

单独过滤某类日志时，可使用以下工具做调试、复用或校验：

标签及保留规则参见 [EventLog 过滤配置参考](docs/event-log-tags-reference.md)和 [Logcat 过滤配置参考](docs/logcat-tags-reference.md)。Logcat 文档区分实际默认规则、类型增量和人工补充定位项，不是自动加载的配置。

```bash
python3 scripts/anr_trace_filter.py <trace_file_or_package>
python3 scripts/anr_event_log_filter.py <event_log_file>
python3 scripts/anr_logcat_filter.py <logcat_file>
python3 scripts/anr_meminfo_filter.py <meminfo_or_bugreport_dir> --package <package.name>
python3 scripts/anr_filter_workflow.py <bugreport_dir_or_archive_or_fixture>
```

堆栈预处理：

```bash
python3 scripts/anr_preprocessor.py tests/fixtures/nfw_01.json
```

## 输入格式

CLI 支持：

- JSON 测试样例：`tests/fixtures/*.json`
- bugreport 目录
- 已解压日志目录
- `.zip`
- `.tar`
- `.tar.gz` / `.tgz`
- `.tar.bz2`
- `.tar.xz`

常见可识别路径包括：

- `FS/data/anr/traces.txt`
- `data/anr/traces.txt`
- `dropbox/system_app_anr@*.txt`
- `event-log/events.txt`
- `events_log.txt`
- `logs/logcat_*.txt`
- `android-logs/log-main`
- `System_log/meminfo.txt`
- `last_kmsg` / `lastkmsg` / `console_ramoops`

## Python API 示例

内存中构建 AI 上下文：

```python
from anr_evidence import AiContextOptions, build_ai_context, load_package_from_path

package = load_package_from_path("tests/fixtures/nfw_01.json")
result = build_ai_context(
    package,
    AiContextOptions(
        out_dir="anr_ai_context",
        anr_type="no_focus_window",
    ),
)

print(result.summary())
print(result.groups[0]["rootCausePatternHints"])
```

生成落盘 AI 工作区：

```python
from anr_evidence import AiContextOptions, build_ai_context_artifacts, load_package_from_path

package = load_package_from_path("tests/fixtures/nfw_01.json")
index = build_ai_context_artifacts(
    package,
    AiContextOptions(out_dir="anr_ai_context", package_name="com.example.app"),
)
print(index["artifactPaths"]["index"])
```

多智能体 AI 分析入口（需要配置提供方/API 密钥）：

```python
from anr_evidence import (
    AgentConfig,
    ProviderConfig,
    ProviderKind,
    load_package_from_fixture,
    run_ai_agent_analysis,
)

package = load_package_from_fixture("tests/fixtures/nfw_01.json")
provider = ProviderConfig(kind=ProviderKind.ANTHROPIC, model="claude-sonnet-4-20250514")
result = run_ai_agent_analysis(
    package,
    provider_config=provider,
    agent_config=AgentConfig(provider=provider, max_iterations=3, verbose=True),
)

print(result.integrated_report)
```

## 项目结构

```text
anr_evidence/   核心 Python 包与 CLI
scripts/        独立入口脚本
tests/          单元测试、集成测试、回归测试、测试样例与评估测试
docs/           设计笔记、操作日志与规划文档
skills/         ANR 领域知识、分来源分析与 ANR 专项分析技能
```

## 设计原则

- **基线提取是硬保证**：关键证据始终优先保留。
- **类型策略只做加法**：ANR 类型只能增加窗口、关键词、锚点和分析重点，不能删除基线来源。
- **触发类型与根因提示分层**：提示只能丰富分析，不能单独成为最终根因。
- **未知/不明确的 ANR 类型**安全回退到基线，同时保留已命中的候选提示。
- **EventLog `am_anr` 是主锚点**；严格包名匹配用于避免跨 ANR 污染。
- **AnrManager 行始终属于 CRITICAL 级别**：必须保留完整转储流程，不能只保留连续行或窄窗口行。
- **Meminfo 可选但必须关联锚点**；存在时应紧跟 AnrManager 负载归因使用。
- **直接阻塞点不等于最终根因**；上游诱因必须有跨源证据支持。
- **所有自动/AI 分析输出默认需要人工确认。**

## 验证

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q anr_evidence tests scripts
```

测试包含按 ANR 隔离、专项技能路由、独立重建及不可信 AI 引用等回归覆盖。当前数量和跳过项以测试运行摘要为准；本地测试不能证明真机事故根因。

## 常见用法

### 快速生成供 AI 使用的材料

```bash
python3 scripts/anr_to_ai.py bugreport.zip --package com.example.app
```

然后打开输入日志目录下的：

```text
<输入日志目录>/anr_ai_context/index.json
<输入日志目录>/anr_ai_context/<anr-id>/anr_analysis.md
<输入日志目录>/anr_ai_context/<anr-id>/logcat.txt
<输入日志目录>/anr_ai_context/<anr-id>/anr_reconstruction.md
<输入日志目录>/anr_ai_context/<anr-id>/anr_reconstruction.json
```

### 没有包名

可以省略 `--package`：工具会从 `am_anr`、AnrManager 块、堆栈 `Cmd line` 或安全回退证据中推断锚点。

```bash
python3 scripts/anr_to_ai.py bugreport.zip
```

### 一份日志里有多个 ANR

先读取 `anr_ai_context/index.json`，再逐个分析每个 `<anr-id>/anr_analysis.md`。除非证据明确互相关联，否则不要跨 ANR 组合并结论。

### 只做确定性报告，不使用 AI

```bash
python3 -m anr_evidence --deliver tests/fixtures/nfw_01.json > delivery.md
```
