# agent-anr

> [中文版](README-zh.md)

`agent-anr` 是一个智能体驱动的 Android ANR（应用无响应）证据抽取与 AI 辅助分析工具链。它将原始堆栈、EventLog、logcat、AnrManager 转储、meminfo 和内核日志转换为按 ANR 独立隔离、可审计的证据工作区，再引导 AI 或人工分析者按来源逐段完成保守分析。

项目注重**高召回与可审计性**，不提前最终定责：触发类型、根因模式提示和修复草案在人工确认之前，都只是有证据支撑的候选结论。

## 运行要求

- Python 3.12 或更新版本。CI 验证 Python 3.12 和 3.13。
- 运行时依赖：仅 Python 标准库。

## 当前能力

### 证据抽取与路由

- 支持加载 JSON 测试样例、已提取日志目录、bugreport 目录、ZIP 归档和各种 TAR 归档。
- 自动发现常见厂商/系统目录，包括 `data/anr/`、`FS/data/anr/`、`System_log/`、`event-log/`、`logs/`、`android-logs/`、`dropbox/`、`meminfo.txt`、`last_kmsg`、`lastkmsg` 和 `console_ramoops`。
- 有可用的目标包名匹配记录时，优先使用 `am_anr` 作为主锚点；严格包名匹配，避免无关 ANR 被当成当前分析锚点。
- 无锚点的回退上下文会保留从堆栈选取的 ANR 时间，使回退输出仍携带有效时间戳。
- 同一份输入中的多个 ANR 会拆分为独立的 `anr-*` 工作区；AnrManager、meminfo、堆栈、EventLog、logcat 和内核证据不得跨锚点串用。
- 基线证据只做加法：类型策略和提示可以增加关注点、窗口和关键词，但不能删除关键来源。

### 分来源分析支持

- **Trace 堆栈**：主线程状态、schedstat、Java/ART/Linux 状态映射、锁等待、锁持有者及相关线程、Binder 等待、渲染/栅栏等待、原生轮询解释、死锁检测和堆栈提示。
- **EventLog**：以 `am_anr` 为锚点的 ANR 前窗口、AM/WM/Input/进程/内存标签保留、ΔT 解读，以及按包名发现锚点而不误删系统上下文行的过滤策略。
- **Logcat / AnrManager**：完整提取 AnrManager 转储流程，包括 `startAnrDump`、堆栈转储、`ANR in`、原因、负载、PSI、CPU 统计窗口、`TOTAL`、CPU 占用最高的进程、DropBox 和转储完成行。
- **Meminfo**：AnrManager 之后的可选跟进证据；关联目标包和高负载进程中最接近 ANR 锚点的 PSS/RSS 快照。
- **内核日志**：作为基线上下文保留调度器、Binder、任务挂起、低内存、OOM/LMK 和压力信号。
- **上下文控制**：过滤后的 logcat 单独写入每个 ANR 的 `logcat.txt`，不内联进主分析文件，在减少提示词膨胀的同时保留可审阅证据。

### 分类与候选根因提示

`triggerType` 和 `rootCausePatternHints[]` 按设计相互独立：

- `triggerType` 描述 ANR 触发机制。
- `rootCausePatternHints[]` 描述候选根因模式，只用于丰富分析，不替代触发类型分类。

已支持的触发类型策略：

| 触发类型 | 分析重点 |
|---|---|
| `input_dispatching_timeout` | InputDispatcher 超时、分发缓慢、焦点窗口状态、主线程 Binder/锁/IO/渲染等待、CPU/IO 压力 |
| `no_focus_window` | 焦点/窗口/表面生命周期、Activity 恢复、relayout、finishDrawing、无焦点窗口（`no focused window`） |
| `broadcast_timeout` | BroadcastQueue 超时、`Broadcast of Intent`、广播接收器 `onReceive`、`goAsync`、`finish()` |
| `service_timeout` | `Timeout executing service`、前台服务启动、服务生命周期、冷启动 |
| `content_provider_timeout` | 内容提供者发布超时、无响应、冷启动、查询/Binder 等待 |
| `job_scheduler_timeout` | JobService `onStartJob` / `onStopJob`、JobScheduler 分发、服务生命周期 |
| `system_watchdog_swt` | system_server Watchdog/SWT、被监控的 Handler、系统锁、Binder 线程 |
| `unknown` | 未知/未来 ANR 类型的安全回退；保留基线证据和已知提示 |

已支持的根因模式提示：

| 提示 | 含义 |
|---|---|
| `deadlock` | 死锁、自锁、锁持有者阻塞链、长时间监视器/互斥锁竞争 |
| `memory_leak_oom_pressure` | 内存泄漏/内存增长/OOM/LMK/PSI 内存压力候选 |
| `high_load_anr` | 高 CPU 占用、IO 等待、高负载、调度压力或目标/系统进程过载候选 |

## 通过 AI 智能体交互式使用

在仓库根目录启动 AI 编程智能体（Claude Code / Codex CLI / Hermes 等），然后输入自然语言请求：

```text
分析 <log_directory_path> 目录中包名为 com.example.app 的 ANR 原因
```

智能体应完成以下步骤：

1. 运行 `python3 scripts/anr_to_ai.py <path> --package com.example.app`；默认在输入日志目录下生成 `anr_ai_context/`（可用 `--out-dir` 覆盖）。
2. 阅读命令输出的 `docs/anr-ai-analysis-guide.md`；指南不再重复写入每个 `anr_analysis.md`。
3. 打开输入日志目录下的 `anr_ai_context/index.json`，独立分析索引列出的每个 ANR 工作区。解释证据前，根据请求及当前组的原因/信号加载适用的[专项分析技能](#specialized-anr-analysis-skills)。
4. 按顺序填写输入日志目录下 `anr_ai_context/<anr-id>/anr_analysis.md` 中的四个分析槽位：Trace → EventLog → Logcat/AnrManager → 最终 ANR 综合分析。
5. Logcat/AnrManager 槽位引用日志文件时，必须读取同一工作区中的 `anr_ai_context/<anr-id>/logcat.txt`。
6. 回复前必须把综合结论写回最终 ANR 综合分析槽位（`#### AI Analysis — 最终 ANR 综合分析`）。
7. 填完四个槽位后，运行 `python3 scripts/reconstruct_anr.py <context-root|group-directory|anr_analysis.md>` 刷新各组独立重建产物，不重新提取证据、不改写 AI 槽位。
8. 按[统一报告验收](#report-acceptance-and-finalization)运行 `--finalize` 显式刷新、验收并写逐组回执；只需预检时运行只读的 `--validate-report`，两者均支持 `--review-record <JSON>`。
9. 最终输出结构化报告，包含时间线、直接阻塞点、排序后的候选根因链、证据质量和修复建议，并交付各组的 `anr_analysis.md`、已刷新的 `anr_reconstruction.md` / `anr_reconstruction.json`、`acceptance.json` 路径及就绪状态；未通过组单独列出，不把部分成功当成全部交付。

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
<input-log-directory>/anr_ai_context/
  index.json
  anr-<timestamp-or-anchor>/
    anr_analysis.md
    logcat.txt
    anr_reconstruction.md
    anr_reconstruction.json
```

说明：

- 独立脚本默认在目录输入内部、或归档/测试样例文件的同级目录生成工作区；模块入口默认输出到 `./anr_ai_context`。两种入口都可通过 `--out-dir <directory>` 显式指定位置。
- `index.json` 是所有已生成 ANR 组的权威目录索引。
- `anr_analysis.md` 是人工/AI 工作区：分析指令、过滤证据摘要、分来源分析槽位和最终综合槽位都在同一个文件中。
- `logcat.txt` 保存当前单个 ANR 组过滤后的 logcat 行。
- `anr_reconstruction.md` 和 `anr_reconstruction.json` 是独立的证据驱动过程重建产物，默认基于日志生成，AI 槽位为空也不阻止输出。每个 ANR 组独立保留 `<group-id>/anr_reconstruction.md`，不生成跨组汇总的重建 Markdown。`index.json` 通过各组的 `artifactPaths.reconstruction` 和 `artifactPaths.reconstructionJson` 暴露路径；原四个分析槽位和 `logcat.txt` 保持不变。
- `<group-id>/acceptance.json` 仅在显式 finalize 时生成，在此之前可不存在；它是内容绑定的验收回执，不是额外 Markdown 或第五个分析槽位。
- 顶层旧式 `cache.md`、`ai_prompt.md` 和 `summary.json` 不再是主流程产物；生成产物时会清理陈旧文件。

重建区分 `observed`（日志观察）、`inferred`（机制或 AI 假设）和 `unknown`（缺口）：

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

传入上下文根目录会处理其中所有组；组目录或分析文件只选择一个组。命令不重新提取证据、不改写 AI 槽位。外部报告引用必须回到原始证据核验，报告本身不能升级为 `observed` 事实。

重建与重新生成的保护机制：

- 多组外部 AI 报告必须通过 `{"groups": [{"groupId": "<target-group-id>", "candidateChains": [...]}]}` 将候选解释显式归属到组。引用必须使用当前组 `anr_reconstruction.json` 中已有事件的 `eventId`，或唯一精确匹配的完整 `rawLine`；`references_resolved` 仅表示引用存在，不代表解释或因果链已核实。
- 刷新会检查工作区证据摘要。若手动修改了过滤证据或锚点元数据，必须先重新提取再刷新；只编辑 AI 槽位不需要重新提取。
- 重新生成会保留已填写的 AI 槽位。同一输入的证据变化时，`analysisRevision` 将保留的分析标为 `stale` 或 `unknown`，不视为已重新核验。复用输出目录处理不同输入时，会校验 `inputIdentity` 并另建组目录，保留旧目录和人工分析；当前索引只列出本次输入的组。
- 墙上时钟与运行时间（uptime）/启动时间（boottime）证据不会自动校准到同一时钟；无法映射的时钟域分开呈现，不跨时钟排序或计算 ΔT。时间戳接近或单份堆栈快照都不能证明整个超时区间持续阻塞。

可选导出组内逐行证据和相对时间：

```bash
python3 scripts/anr_to_ai.py <input> --build-ess --delta-t-normalized
# 额外生成 evidence.jsonl 和 time_normalized.json；不执行跨时钟校准。
```

证据边界与最终交付要求见[分析指南](docs/anr-ai-analysis-guide.md)。

<a id="report-acceptance-and-finalization"></a>

## 统一报告验收

统一验收已接入默认工作区、Manager 和 Phase 7 候选交付。实现清单与验证证据见 [report-acceptance-design.md](docs/report-acceptance-design.md)；通过交付验收不等于根因已确认。

`analysisComplete` 仍只表示四槽已填，不代表可交付。规则计算 `deliveryReadiness`，优先级从高到低为：

- `blocked`：结构、身份、引用、过期支撑或产物不一致等硬错误，不可交付。
- `needs_review`：机器检查通过，但具体问题缺少有效复核，不可交付。
- `ready_with_limitations`：缺口已披露、相关断言已降级，可带限制交付。
- `ready`：当前规则的必需检查通过，无重大交付限制；不证明根因真值。

四槽填完并刷新重建后，再执行正式收尾；草稿提取和重建仍允许 pending：

```bash
# 只读检查现有工作区，不刷新、不写回执或索引
python3 -m anr_evidence --validate-report <context-root|group-directory|anr_analysis.md>
# 显式刷新 + 验收 + 写每组 acceptance.json 与索引就绪度
python3 -m anr_evidence --finalize <context-root|group-directory|anr_analysis.md>
# 两个命令均可追加 --review-record <JSON>
```

两个入口输出机器可读 JSON，明确逐组状态与问题；任一所选组不可交付或处理失败时退出码非零。`ready` 与 `ready_with_limitations` 均可交付，但受限组须披露限制；批量部分成功不能掩盖待复核、阻断或失败组。finalize 不改写四槽，也不重新提取证据。

`--review-record <JSON>` 向验证器或 finalizer 传入逐问题复核记录，绑定 `reviewer`、`scope`、`evidenceRevision`、`analysisDigest`；只能处理相应复核项，不能覆盖硬错误或接受模型自报通过。缺 Trace、根因未知不自动阻断：须明确缺口、补证计划并降低相关断言，才可能 `ready_with_limitations`。坏引用、过期证据仍支撑当前结论、产物不一致均硬阻断。

每组回执绑定当前证据、分析与依赖产物内容；任一被编辑后旧回执失效，须重新验收。仅排版变化也需重验，但不因此直接判定解释过期。`index.json` 只聚合就绪度与路径，不裁定根因真值。正式交付提供匹配当前内容的回执，而不是只看文件是否存在；不增加第五槽位或额外 Markdown。

`acceptance.py` 提供包 API `validate_workspace_reports(path, *, review_records=None)`、`finalize_workspace_reports(path, *, review_records=None)`；纯校验内核 `validate_manager_report`、`validate_phase_report` 分别适配 manager 报告和确定性阶段报告，不要求内存报告具有工作区四槽或磁盘产物。确定性 `--deliver` 对 Phase 7 执行交付门禁，`--report` 草稿仍可访问。所有结果仍是候选，保留 `finalJudgment=false`、`notRootCauseYet=true`、`requiresHumanConfirmation=true`，模拟 `executed=false`。详见[验收指南](docs/anr-ai-analysis-guide.md#report-acceptance-and-finalization)。

## 四阶段 AI 分析契约

每个 `anr_analysis.md` 固定包含四个分析槽位，必须按顺序填写：

1. [`anr-trace-analysis`](skills/anr-trace-analysis.md) → 仅做 Trace 堆栈分析，写入 `#### AI Analysis — Trace 堆栈`。
2. [`anr-eventlog-analysis`](skills/anr-eventlog-analysis.md) → 仅做 EventLog/锚点分析，写入 `#### AI Analysis — EventLog 事件日志`。
3. [`anr-logcat-analysis`](skills/anr-logcat-analysis.md) → Logcat + AnrManager + meminfo 跟进分析，写入 `#### AI Analysis — Logcat / AnrManager`。
4. [`anr-analysis`](skills/anr-analysis.md) → 最终跨来源 ANR 综合分析，写入 `#### AI Analysis — 最终 ANR 综合分析`。

约束：

- 前三个阶段只做分来源分析，不能提前下最终根因结论。
- 最终 ANR 综合分析只能在 Trace、EventLog 和 Logcat/AnrManager 槽位完成后进行。
- 综合结论必须写回同一个 `anr_analysis.md`，不能只留在聊天中。
- 最终 Markdown 应包含：综合分析结论、时间线、Trace 证据分析、EventLog 证据分析、Logcat/AnrManager 证据分析、直接阻塞点、候选根因链、证据质量、修复建议，以及位于末尾的 JSON 围栏代码块。
- 所有 ANR 类型的 `#### AI Analysis — 最终 ANR 综合分析` 槽位均须填写 `## ANR 发生模型`、`## 模拟操作步骤`，仍在原 `final-anr` 内，不新增第五个槽位。两个公共章节和 `anrModel`、`simulationSteps` 须有候选内容或具体缺口与补证计划，不能因非 input 写不适用或一律留空。仅 `## 超时输入事件` 是 input 专项，非 input 可在该节写不适用、`inputEventAssessments=[]`。unknown 列分类/触发点缺口，不猜成 input。
- 超时输入事件区分 reason 描述事件与真正最早到期条目，列明类型、action/keyCode/native seq/eventId/device/source/display/window/channel，未知用 `null`。无焦点是等待投递事件，不是已发送未 ACK。发生模型区分 observed 支撑、inferred 机制与 unknown 缺口，分开计时、阻塞/等待、通知和恢复；禁止固定 5 秒或把快照当作持续阻塞。
- 模拟步骤包含前置条件、编号动作、预期观察、采集核验、缺失参数，明确“候选未执行，不保证复现”。不编造坐标、业务入口、设备配置或故障注入，不自动运行设备命令；`replay` 仍为回归重跑。
- JSON 新增候选数组 `inputEventAssessments`、`anrModel`、`simulationSteps`；元素统一含 `summary`、`supportingEventIds` 或 `evidenceRefs`、`uncertainties`，保留 `certainty=inferred`、`validation=unverified`、`executed=false`、`requiresHumanConfirmation=true`。AI 引用不升级为事实；manager raw `integrated_report` 须经 `validate_manager_report` 实际校验，提示约束不保证响应合规，运行时接入须另行核验。完整结构见[分析指南](docs/anr-ai-analysis-guide.md#final-anr-固定输出)。
- 公共派生 `incidentScenario`（`anr-incident-scenario-v1`）适用于所有类型，含 `anrModel`、`simulationSteps`、`missingParameters`、`evidenceGaps` 和保守 flags。按 `anr_strategy.py` 分型生成模型/计划，经 Phase 3 → 5 → 6 → 7 透传，`--report` / `--deliver` 和重建 Markdown 同步两个公共章节；不新增固定超时或真实故障注入。
- `incidentScenario` 顶层保持 `certainty=inferred`、`validation=unverified`、`executed=false`。非 input 模型顶层仍为候选，模板节点除精确 anchor ANR 上报外均为 `unknown`；关键词只形成 `candidateObservationIds`，仅保留在 JSON，不升级 `observed` 或已观测时间线，保留缺口。JobService 的 `jobFinished` 不等于 onStartJob/onStopJob 按时返回，用户发起任务 `setNotification` 是另一分支，须分别核验。
- input 专项 `inputAnr` 保留并复用模型/步骤；`reportedEvents` 保存原样 reason 与 `nativeSeq` / `reportedSeq` / `seqDomain` / window / connection 身份，匿名 reason 为 `unverified` 候选；真正最早到期条目 `triggeringEvent` 未核验时保持 `null`。AI 三数组独立保存在重建 `aiClaims`，`referenceValidation` 仅表示引用解析结果，`validation` 始终 `unverified`、`executed=false`，不覆盖派生数据或改变 `observed` 事件。
- 构建及刷新旧 `anr-reconstruction-v1` sidecar 均派生 `incidentScenario`，input 仍有 `inputAnr`，不改四槽。manager prompt 接收两种已有摘要；重建导入校验与 manager raw 输出验收的边界不同，前者不代替 `validate_manager_report`。
- JSON 默认保持以下保守字段：
  - `finalJudgment = false`
  - `notRootCauseYet = true`
  - `requiresHumanConfirmation = true`

<a id="specialized-anr-analysis-skills"></a>

## ANR 专项分析技能

这些仓库内置技能用于指导 AI 智能体在基线提取后做增量分析，不是新增 CLI 触发类型，也不自动判定根因。解释证据前应完整阅读适用的技能。仅出现通用 `Input dispatching timed out` 原因时，应先区分分发前焦点等待、已发布事件的完成等待，或保留为未明确的分支。

### 输入分发超时：已发布事件的完成 / ACK 等待

用户明确请求（包括指定 `input_dispatching_timeout`），或同一 ANR 显示已发布事件等待 FINISHED、连接存在未完成的 `waitQueue` / `AnrTracker` 记录，或通道的 `is not responding. Waited ...ms for ...` 原因指向未完成的分发时，使用 [`input-dispatch-timeout-anr-analysis`](skills/input-dispatch-timeout-anr-analysis/SKILL.md)。

- 使用连接/输入通道令牌和原生分发序号（`seq`）关联发布、逐事件投递/截止时间、接收端处理、完成处理（`finish`）、FINISHED 传输、Dispatcher 清理及剩余队列恢复。
- 最早到期的记录可能不同于原因描述的最早发送记录；不能仅凭 `Waited N ms` 反推配置的超时时间。
- Java 完成处理（`finish`）返回、`handled=false` 或单个 ACK 均不能单独证明连接已恢复。纯无焦点窗口等待属于下方独立专项，不能套用此 ACK 超时机制。

### 无焦点窗口：目标分发前等待

用户明确请求（包括指定 `no_focus_window`），或同一 ANR 出现 `does not have a focused window`、`no focused window`、`no focus window`、`no window has focus`，或已存在焦点应用而需要焦点的事件正在等待窗口时，使用 [`no-focused-window-anr-analysis`](skills/no-focused-window-anr-analysis/SKILL.md)。

- 区分焦点应用与真正生效的原生输入窗口焦点；Activity 恢复、绘制完成或 WMS 焦点请求本身都不能证明窗口已获得焦点。
- 按同一显示屏/应用/窗口身份重建首次目标查找、实际生效的超时配置、到期复查、重置/恢复及 WMS 归因。
- 这是目标分发前的等待，不是已投递事件缺少 ACK，也不能单独证明主线程阻塞。没有等待起点与超时证据时，不得用 `am_anr - 5s` 补造起点。

### Launcher / Quickstep / Recents 动画

用户明确要求分析 Launcher/Quickstep 手势导航或 Recents 动画 ANR，或同一 ANR **同时具有**输入/焦点锚点（输入超时、无焦点窗口、或目标已绘制但仍为 `NOT_VISIBLE`）**以及** Launcher/Recents 信号，如 `recents_animation_input_consumer`、`OtherActivityInputConsumer`、`AbsSwipeUpHandler`、`startRecentsAnimation`、`onTasksAppeared`、`finishRecentsAnimation` 或 `mRecentsAnimationStartPending` 时，使用 [`gesture-anr-analysis`](skills/gesture-anr-analysis/SKILL.md)。

- 将 `am_anr` 与同一手势的 `logId` / 处理器哈希、Recents 启动/回调/结束状态、过渡可见性和窗口焦点关联，关键结论附原始日志片段及来源定位。
- 框架回调日志不能证明指定处理器收到了回调；缺少 `trackEvent(NO_OP)` 文本不能证明事件从未执行。时间接近只能建立候选，不能直接确定根因。
- 没有手势/Recents 信号的通用输入超时/无焦点 ANR 不加载此技能。本技能只分析已抓取日志；缺失的设备转储仍属于证据缺口，不能假定其结果。

仅当各自触发条件满足时，才将手势技能与 ACK 超时或无焦点窗口技能叠加。若两条输入分支都有证据，必须分开记录各自事件和超时计时起点。所有专项均保留基线来源，写回原有四个槽位（不新增第五槽位），保留保守标志，并在交付前刷新重建产物。

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

# 草稿 Markdown：不要求正式交付验收通过
python3 -m anr_evidence --report tests/fixtures/nfw_01.json

# 第 8 阶段：对 Phase 7 包验收后输出候选交付 Markdown
python3 -m anr_evidence --deliver tests/fixtures/nfw_01.json
```

所有确定性阶段都保持候选语义：输出证据、缺口和假设，不做不可逆的最终定责。`--deliver` 使用 `validate_phase_report` 检查 Phase 7 包，不要求 Markdown 工作区四槽或回执；工作区正式收尾则使用 `--finalize`。`--report` 成功不代表 `--deliver` 门禁通过。

## 分来源 CLI 工具

独立过滤器可用于调试、复用或逐个校验证据源：

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
- 已提取日志目录
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

在内存中构建 AI 上下文：

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

生成落盘的 AI 工作区：

```python
from anr_evidence import AiContextOptions, build_ai_context_artifacts, load_package_from_path

package = load_package_from_path("tests/fixtures/nfw_01.json")
index = build_ai_context_artifacts(
    package,
    AiContextOptions(out_dir="anr_ai_context", package_name="com.example.app"),
)
print(index["artifactPaths"]["index"])
```

验证或正式收尾已有工作区（前者只读，后者会刷新并写回执和索引）：

```python
from anr_evidence import finalize_workspace_reports, validate_workspace_reports

validation = validate_workspace_reports("anr_ai_context", review_records=None)
acceptance = finalize_workspace_reports("anr_ai_context", review_records=None)
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
tests/          单元、集成、回归、测试样例与评估测试
docs/           设计笔记、操作日志与规划文档
skills/         ANR 领域知识、分来源分析与 ANR 专项分析技能
```

## 设计原则

- **基线提取是硬保证**：关键证据始终保留。
- **类型策略只做加法**：ANR 类型只能增加窗口、关键词、锚点和分析重点，不能删除基线来源。
- **触发类型与根因提示相互独立**：提示只能丰富分析，不能单独成为最终根因。
- **未知/不明确的 ANR 类型**安全回退到基线，同时保留已知候选提示。
- **存在时，EventLog `am_anr` 是主锚点**；严格包名匹配用于避免跨 ANR 污染。
- **AnrManager 行始终属于 CRITICAL 级别**：必须保留完整转储流程，不能只保留连续行或窄窗口行。
- **Meminfo 可选，但必须关联锚点**；存在时应紧跟 AnrManager 负载归因使用。
- **直接阻塞点不一定是根因**；上游诱因必须有跨来源证据支持。
- **所有自动/AI 分析输出默认需要人工确认。**

## 验证

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q anr_evidence tests scripts
```

测试包含按 ANR 隔离、专项技能路由、重建及不可信 AI 引用等回归覆盖。当前数量和跳过项以测试运行摘要为准；本地测试不能证明真机事故根因。

## 常见用法

### 快速生成供 AI 使用的材料

```bash
python3 scripts/anr_to_ai.py bugreport.zip --package com.example.app
```

然后打开输入日志目录下的这些文件：

```text
<input-log-directory>/anr_ai_context/index.json
<input-log-directory>/anr_ai_context/<anr-id>/anr_analysis.md
<input-log-directory>/anr_ai_context/<anr-id>/logcat.txt
<input-log-directory>/anr_ai_context/<anr-id>/anr_reconstruction.md
<input-log-directory>/anr_ai_context/<anr-id>/anr_reconstruction.json
```

### 没有包名

可以省略 `--package`；工具会从 `am_anr`、AnrManager 块、堆栈 `Cmd line` 或安全回退证据中推断锚点。

```bash
python3 scripts/anr_to_ai.py bugreport.zip
```

### 一份日志包里有多个 ANR

打开 `anr_ai_context/index.json`，再独立分析每个 `<anr-id>/anr_analysis.md`。除非证据明确关联，否则不要跨 ANR 组合并结论。

### 只生成确定性报告，不使用 AI

```bash
python3 -m anr_evidence --report tests/fixtures/nfw_01.json > draft.md
# 需要正式交付时走 Phase 7 验收门禁；失败不能当作成功交付
python3 -m anr_evidence --deliver tests/fixtures/nfw_01.json
```
