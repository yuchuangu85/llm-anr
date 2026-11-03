<a id="operation-log-anr-intelligent-analysis-system-optimization"></a>
# 操作日志：ANR 智能分析系统优化

> **规则：条目按日期反序排列（最新的在最前面）。新增记录时请插入到标题下方第一条位置，保持反序一致。**

<a id="2026-07-26---fix-enforce-evidence-windows-and-centralize-phase-execution"></a>
## [2026-07-26] - 修复：落实证据窗口并集中阶段执行逻辑
- **背景**: 设计回顾复核发现 Meminfo 窗口是未生效的配置、CLI 会为早期阶段计算全部后续阶段、
  EventLog 不能直接复用首锚点 API，以及 Trace ESS 会被上下文裁剪当作上下文证据丢弃。
- **状态**: 已实现并验证。
- **主要变更**:
    1. Meminfo 按 `[-before,+after]` 窗口选择快照，并优先选择锚点时刻/之前的证据。
    2. `parse_log_timestamp` 支持显式年份和可变小数精度，Trace 复用规范解析器。
    3. 新增已知锚点 EventLog 过滤器，保持多 ANR 分组隔离。
    4. 新增共享 `pipeline.payload_phase/run_until`，CLI/回放不再重复维护阶段级联逻辑。
    5. 上下文裁剪保留锚点、报告关键证据超预算，并将紧凑 Trace 标为 `critical`。
    6. 新增归档 → `scripts/anr_to_ai.py` → 产物全链路回归测试。
- **验证**:
    - `python3 -m unittest discover -s tests -v` → 303 个测试通过，跳过 1 个
    - `python3 -m compileall -q anr_evidence tests` → OK
    - `git diff --check` → OK
- **后续要求**:
    - 产物若需要硬 token 上限，必须保留完整外部证据并单独生成紧凑 LLM 视图；不得静默删除基线/关键证据。

<a id="2026-06-17---chore-clean-up-stale-markdown-files"></a>
## [2026-06-17] - 维护：清理过时的 Markdown 文件
- **提交**: `52c3d13`
- **背景**: 删除不再需要的 Markdown 文件。
- **状态**: 已完成。

<a id="2026-06-17---feat-strengthen-evidence-extraction-pipeline"></a>
## [2026-06-17] - 功能：增强证据提取流水线
- **提交**: `93890a1`
- **背景**: AI 上下文构建、AnrManager 解析、包加载、trace 预处理和根因提示检测需要全面增强，提升提取准确性和分析质量。
- **状态**: 已实现并验证。
- **主要变更**:
    1. 增强 AI 上下文构建逻辑。
    2. 增强 AnrManager 解析：更鲁棒的块提取和字段解析。
    3. 增强包加载：更精确的多 ANR 包名选择。
    4. 增强 trace 预处理：更好的线程状态和栈帧结构化。
    5. 增强根因提示检测：更丰富的 `rootCausePatternHints`。
    6. 扩展测试覆盖：`ai_context`、`anrmanager_parser`、证据源工作流。
    7. 新增 `docs/` 下的设计/开发博客文档。
- **验证**:
    - `python3 -m unittest discover -s tests -v` → OK
    - `python3 -m compileall -q anr_evidence tests scripts` → OK

<a id="2026-06-11---fix-analysis-slot-template-detection-enhancement"></a>
## [2026-06-11] - 修复：增强分析槽位模板检测
- **提交**: `67d225f`
- **背景**: 旧模板检测只检查 `_Pending AI analysis` / `待 AI 分析填写` 两个遗留标记，无法识别新格式模板占位，导致已填写的分析槽被覆盖。
- **状态**: 已实现并验证。
- **主要变更**:
    1. 新增 `_请用`/`_列出`/`_评估` 标记检测，防止已填写槽位被覆盖。
    2. 新增按槽位划分的结构化输出模板，包含结论、证据、缺口、置信度等分段，替代模糊的一行提示词。
- **验证**:
    - `python3 -m unittest discover -s tests -v` → OK
    - `python3 -m compileall -q anr_evidence tests scripts` → OK
- **后续要求**:
    - 新增分析槽位格式时，必须同步添加模板检测标记，避免回退覆盖。

<a id="2026-06-10---refactor-per-anr-independent-workspace-architecture"></a>
## [2026-06-10] - 重构：每个 ANR 独立工作区架构
- **提交**: `6b5b9db`
- **背景**: 多 ANR 分析需要每个 ANR 分组拥有完全独立的工作空间，避免证据混杂和跨 ANR 状态泄露。
- **状态**: 已实现并验证。
- **主要变更**:
    1. 智能体文档与每个 ANR 独立的分析工作空间契约对齐。
    2. 新增 CI/打包支持。
    3. LLM API 调用增强错误处理。
    4. 多 ANR 上下文生成复用昂贵的解析（trace/EventLog 扫描），但不共享分组间的可变证据状态。
    5. AI 上下文使用 `index.json` + 每个 ANR 独立的 `anr_analysis.md`/`logcat.txt` 工作空间，替代旧的 `cache.md`/`ai_prompt.md`/`summary.json`。
- **决策**:
    - **已否决**: 缓存完整 trace 结果但不加分组级防御性拷贝 — 存在跨 ANR 旁证串扰风险。
    - **已否决**: 将预处理的 logcat 行传入 `filter_timestamp_window(content, ...)` — `filter_prepared_timestamp_window` 保持预处理证据源的溯源明确。
- **验证**:
    - `python3 -m unittest discover -s tests -v` → 285 个测试，跳过 1 个，其余通过
    - `python3 -m compileall -q anr_evidence tests scripts` → OK
    - `scripts/anr_to_ai.py tests/fixtures/nfw_01.json` 冒烟测试 → OK
- **后续要求**:
    - 新的证据源必须限定在各自 ANR 分组的范围内。
    - 永远不要在多 ANR 之间共享可变证据状态。
    - `index.json` + `anr_analysis.md` + `logcat.txt` 是新的标准上下文格式。

<a id="2026-05-29---docs-development-handoff-guide"></a>
## [2026-05-29] - 文档：开发交接指南
- **提交**: `5b7cea3`
- **背景**: 记录仓库结构和扩展工作流，确保后续 ANR 流水线变更能保持证据保证和验证预期。
- **状态**: 已完成（仅文档）。
- **主要变更**:
    1. 新增 `docs/development-guide.md`：包含仓库地图、扩展工作流、证据保证和验证预期。
- **后续要求**:
    - 修改流水线阶段、证据源过滤器或 AI 上下文产物时，同步更新该指南。

<a id="2026-05-27---docs-update-bilingual-readme"></a>
## [2026-05-27] - 文档：更新双语 README
- **提交**: `30d5793`
- **背景**: README 需要反映当前每个 ANR 独立工作区的工作流、扩展触发策略、候选根因提示、分源证据处理和最新测试数量。
- **状态**: 已完成（仅文档）。
- **主要变更**:
    1. 更新中英文 README，同步最新流水线能力和工作流。
    2. 确保 `triggerType` 与 `rootCausePatternHints` 语义独立。
- **后续要求**:
    - README 必须保持与当前实现一致；新增 ANR 类型或工作流变更后同步更新。

<a id="2026-05-24---feat-expand-anr-trigger-strategy-coverage"></a>
## [2026-05-24] - 功能：扩展 ANR 触发策略覆盖范围
- **提交**: `d21d430`, `0be9bf4`
- **背景**: 当前 `AnrTypeStrategy` 主要覆盖 `input_dispatching_timeout` 和 `no_focus_window`，缺少广播、服务、ContentProvider、JobScheduler、系统看门狗/SWT 等 ANR 触发策略。
- **状态**: 已实现并验证。
- **主要变更**:
    1. 新增广播、服务、ContentProvider、JobScheduler、系统看门狗/SWT 触发策略。
    2. 基线证据提取保持只增不减，新类型不删除现有证据源。
    3. `triggerType` 与 `rootCausePatternHints[]` 明确分离：提示为候选且非最终结论。
    4. 死锁、内存压力、高负载证据可丰富分析但不替换 ANR 类型分类。
    5. `0be9bf4` 将扩展计划独立文档化：新增 ANR 触发策略和根因模式提示的规划文档。
- **决策**:
    - **已否决**: 把根因模式编码为 ANR 类型 — 会混淆触发机制与候选根因，破坏未知触发类型的回退语义。
- **验证**:
    - `python3 -m unittest discover -s tests -q` → OK
    - `python3 -m compileall -q anr_evidence tests scripts` → OK
- **后续要求**:
    - `rootCausePatternHints` 永远是候选非最终；未来类型专用技能应挂载到 `AnrTypeStrategy` 而非硬编码提示词分支。
    - 新增 ANR 类型时必须同步更新 `AnrTypeStrategy` 注册表，不要直接改主流程。

<a id="2026-05-16---fix-preserve-anr-time-in-unanchored-context-ids"></a>
## [2026-05-16] - 修复：在无锚点的上下文 ID 中保留 ANR 时间
- **提交**: `235a393`
- **背景**: 缺少 EventLog 锚点的上下文目录使用 `anr-unanchored` 命名，多次回退生成时冲突；ANR1 风格日志集应匹配对应 `am_anr` 而非过期归档 trace。
- **状态**: 已实现并验证。
- **主要变更**:
    1. 未锚定 AI 上下文目录使用 trace 中提取的 ANR 时间戳命名（如 `anr-20260422-023434-522`）。
    2. 松散目录证据合并到归档旁，使 ANR1 风格日志集解析到匹配的 `am_anr`。
    3. 包名过滤 `am_anr` 匹配保持严格：指定包名时，不匹配的 `am_anr` 不能成为锚点。
- **验证**:
    - `python3 -m unittest discover -s tests -v` → OK
    - `python3 -m compileall -q anr_evidence tests` → OK
    - 案例冒烟测试: `scripts/anr_to_ai.py` 对 ANR1 样本生成 `anr-20260422-023434-522`
- **后续要求**:
    - 上下文目录命名必须包含 ANR 时间，禁止使用不含时间的无歧义名称。

<a id="2026-05-16---refactor-extract-logcat-to-separate-ai-context-artifact"></a>
## [2026-05-16] - 重构：将 Logcat 提取为独立的 AI 上下文产物
- **提交**: `47bb654`
- **背景**: 当前 `anr_analysis.md` 内联大量 logcat 文本，导致分析提示词臃肿，浪费上下文窗口。
- **状态**: 已实现并验证。
- **主要变更**:
    1. logcat 过滤结果写入独立 `logcat.txt` 文件。
    2. `anr_analysis.md` 改为引用 `logcat.txt` 而非内联。
    3. AI 上下文生成输出结构调整：每个 ANR 分组目录下包含 `anr_analysis.md` + `logcat.txt`。
- **验证**:
    - `python3 -m unittest discover -s tests -v` → OK
    - `python3 -m compileall -q anr_evidence tests scripts` → OK
- **后续要求**:
    - logcat 文本量极大，必须始终保持为独立文件，不要回到内联模式。

<a id="2026-05-13---skill-optimization-complete-anr-trace-analysis-skill-consolidation"></a>
## [2026-05-13] - 技能优化：完成 ANR Trace 分析技能整合
- **背景**: 用户要求持续优化 Trace 分析技能，使其达到 `ANR-trace文件分析.md` 同等详细度，并将 `ANR-trace覆盖清单.md`、`ANR基础知识.md`、`ANR-trace文件分析.md` 的 Trace 专项知识合并到 `skills/anr-trace-analysis.md`。
- **状态**: 已实现并验证。
- **主要变更**:
    1. `skills/anr-trace-analysis.md` 扩展为完整 Trace 专项分析技能：保留"只基于 Trace 形成专项结论、跨源证据只标缺口"的边界。
    2. 合并 `ANR-trace覆盖清单.md`：新增当前实现落点、可稳定依赖能力、字段覆盖状态、7 类自动分类规则覆盖边界、不能仅凭单份 trace 证明的结论、P1/P2/P3 后续增强待办事项。
    3. 合并 `ANR基础知识.md`：新增 Linux 状态解释、常见 ANR 原因分类、Trace 字段含义、Thread.java ↔ ART Thread.cpp 状态映射、CPU/内存负载旁证解释。
    4. 合并 `ANR-trace文件分析.md`：新增 Trace 文件阅读顺序、进程归属判断、特殊线程说明、互斥锁缩写、Java/ART/Linux/Perfetto 根因映射、MONITOR/SUSPENDED 判定边界、典型 Trace 形态速查。
    5. 输出契约增强：Trace 分析输出必须包含 `Coverage boundary`，明确"已覆盖 / 部分覆盖 / 未证明 / 需要跨源补证"。
- **验证**:
    - `python3 -m unittest tests.test_ai_context -v` → OK
    - `python3 -m compileall -q anr_evidence tests` → OK
    - 人工内容检查: `skills/anr-trace-analysis.md` 包含 `ANR 基础知识速查（Trace 分析必用）`、`Trace 文件阅读顺序与归属判断`、`Java / ART / Linux / Perfetto 根因映射`、`Trace 典型形态速查` 和 `最可靠结论与禁止过度承诺`。
- **后续要求**:
    - Trace 技能的结论必须保持保守：单份 trace 只能证明采样瞬间的阻塞/等待形态；CPU 抢占、STW/GC、Binder 对端归属、渲染/GPU 闭环、InputDispatcher 超时真实触发点必须通过 EventLog/Logcat/AnrManager/Perfetto/CPU/meminfo 等跨源补证。
    - 后续修改 Trace 分析输出时，必须保留 `Coverage boundary` 字段，避免把静态 trace 提示误写成最终根因。

<a id="2026-05-13---output-contract-one-independent-ai-context-per-anr"></a>
## [2026-05-13] - 输出契约：每个 ANR 一份独立 AI 上下文
- **背景**: 用户要求一份日志中可能包含同一包名的多个 ANR，输出的 `anr_ai_context` 必须独立拆分，每个 `anr_ai_context` 只保留一份 ANR 信息，避免多个 ANR 的 Trace/EventLog/logcat/AnrManager/meminfo 证据混杂。
- **状态**: 已实现并验证。
- **主要变更**:
    1. `build_ai_context_artifacts(...)` 输出策略调整：
        - 单 ANR 场景保持兼容，继续输出顶层 `cache.md`、`ai_prompt.md`、`summary.json`。
        - 多 ANR 场景不再生成混合顶层 `cache.md` / `ai_prompt.md` / `summary.json`。
        - 多 ANR 场景改为输出 `index.json`，并为每个 ANR 分组生成独立目录：`<out-dir>/<groupId>/cache.md`、`ai_prompt.md`、`summary.json`。
    2. AnrManager 提取改为感知锚点：
        - 新增 `AnrManagerBlock` 与 `extract_anrmanager_blocks(...)`，先提取同包名全部 AnrManager 转储流程。
        - `extract_anrmanager_block(..., anchor_dt=...)` 根据当前 ANR 锚点选择最近的块。
        - 找不到匹配块时返回 `missing-anrmanager-for-anchor`，禁止复用其它 ANR 的 AnrManager 信息。
    3. ANR 锚点去重规则收紧：
        - 不再仅按时间窗口合并锚点。
        - 只有近时间且归一化内容一致的重复 `am_anr` 才合并，避免短时间内多个真实 ANR 被误合并。
    4. Meminfo 后续分析改为感知锚点：
        - `filter_meminfo_source(...)` 在存在 `anchor_dt` 时选择距离当前 ANR 最近的快照。
        - 元数据增加 `selectedTimestamp`、`selectedSnapshotIndex`、`anchorTimestamp`，便于审计当前 ANR 的内存证据来源。
    5. CLI 提示同步：
        - `scripts/anr_to_ai.py` 在多 ANR 输出时提示逐个分析 `<out-dir>/<anr-id>/ai_prompt.md`。
- **新增/更新测试**:
    - `test_multi_anr_builds_independent_contexts_and_index`
    - `test_multi_anr_contexts_keep_distinct_anrmanager_blocks`
    - `test_extract_anchor_aware_block_from_repeated_package_anrs`
    - `test_extract_anchor_aware_block_does_not_reuse_wrong_anr`
    - `test_meminfo_filter_selects_snapshot_nearest_anchor`
- **验证**:
    - `python3 -m unittest tests.test_anrmanager tests.test_ai_context tests.test_meminfo_filter -v`
    - 结果: OK
    - `python3 -m unittest discover -s tests -v`
    - 结果: `Ran 270 tests ... OK`
    - `python3 -m compileall -q anr_evidence tests`
    - 结果: OK
    - 冒烟检查: 合成多 ANR 包仅输出 `index.json` 及两个独立的 `anr-*` 目录。
- **后续要求**:
    - 多 ANR 日志不能再生成混合 AI 提示词；每个 AI 分析输入必须只包含一个 ANR 的证据。
    - AnrManager、meminfo、trace、EventLog、logcat 后续过滤都必须以当前分组锚点为边界，禁止跨 ANR 复用证据。

<a id="2026-05-13---fix-complete-logcat-anrmanager-flow-extraction"></a>
## [2026-05-13] - 修复：完整提取 Logcat 中的 AnrManager 流程
- **背景**: 用户反馈过滤结果中的 logcat/AnrManager 信息不全面，要求保证完整过滤出 AnrManager 转储流程，覆盖 `startAnrDump`、堆栈转储、`ANR in`、`Reason`、`Load`/PSI、CPU 窗口、`dumpAnrDebugInfo end`、DropBox 等关键行。
- **状态**: 已实现并验证。
- **主要变更**:
    1. `anr_evidence/log_filter.py`:
        - AnrManager 锚点改为优先选择 `dumpAnrDebugInfo end` / `addErrorToDropBox` / `ANR in` / `Reason` 等更完整的结束侧锚点。
        - AnrManager 块提取不再要求物理连续行；允许普通 logcat 行穿插，按 AnrManager 标签从最近的 `startAnrDump` / `dumpAnrDebugInfo begin` 收集到 `controller = null` / `addErrorToDropBox` / `dumpAnrDebugInfo end`。
        - 支持旧式 `I/AnrManager( 1377):` 与新式 `I AnrManager:` 日志格式，避免普通消息文本中包含 "AnrManager" 时误判为标签行。
    2. `anr_evidence/anrmanager_parser.py`:
        - 支持旧式 AnrManager 前缀、`Load: x / y / z`、多段 CPU 窗口（`ago` + `later`）、`softirq/irq/iowait`、`+0%` 进程行、`mTracesFile`。
        - 保留 `cpuWindows[]`，同时保持旧字段 `cpuWindow` / `cpuTotal` 兼容。
        - 区分 `/proc/pressure/memory|cpu|io`，避免 CPU/IO PSI 覆盖内存 PSI。
        - 新增 `SYSTEM_SERVER_CPU_HIGH` 与 `ANR_PROCESS_CPU_HIGH` 提示，用于先看总负载后的高负载进程归因。
    3. 测试:
        - 增加旧式完整 AnrManager 流程 + 穿插 logcat 行的提取测试。
        - 增加旧式多 CPU 窗口、`Load`、PSI、`mTracesFile`、高负载提示解析测试。
- **验证**:
    - `python3 -m unittest tests.test_anrmanager tests.test_anrmanager_parser -v`
    - `python3 -m unittest tests.test_ai_context tests.test_meminfo_filter tests.test_source_workflow -v`
    - `python3 -m unittest discover -s tests -v` → 266 个测试通过
    - `python3 -m compileall -q anr_evidence tests scripts`
    - 案例重新生成: `python3 scripts/anr_to_ai.py /Users/yuchuan.gu/Downloads/5LLN85HMOBSK9P65_2026_05_07_12_04_07 --package com.tcl.android.launcher`
    - 自动检查: 重新生成的 `cache.md` 中，AnrManager 块包含堆栈转储开始/结束、`ANR in com.tcl.android.launcher`、`Reason`、`Load`、内存/CPU/IO PSI、`TOTAL`、`dumpAnrDebugInfo end` 和 `addErrorToDropBox`。
- **后续要求**:
    - AnrManager 是关键证据；后续 logcat 过滤必须保留完整转储流程，而非仅保留时间窗口内的子集或物理连续行。

<a id="2026-05-13---workflow-meminfo-follow-up-after-logcatanrmanager"></a>
## [2026-05-13] - 工作流：在 Logcat/AnrManager 之后进行 Meminfo 后续分析
- **背景**: 用户要求把内存过滤分析放到 logcat 中 AnrManager 分析后面的工作流中，而不是只提供独立脚本。
- **状态**: 已实现并验证。
- **主要变更**:
    1. 目录/包加载现在将 `System_log/meminfo.txt` 识别为可选的 `meminfo` 证据源，而不将其作为必需的基线证据源。
    2. 智能 Monkey 发现逻辑在 `System_log/meminfo.txt` 存在时会将其纳入。
    3. `run_filter_workflow(...)` 现在紧接 logcat 之后、内核证据之前运行可选的 meminfo 过滤；缺失 meminfo 不会发出缺失证据源警告。
    4. `build_ai_context(...)` 现在在提取/解析 AnrManager 块之后运行 meminfo 后续分析，并以 AnrManager 中 CPU 占用最高的进程作为高负载进程输入。
    5. `cache.md` 现在在 `### AnrManager Diagnostic Block` 之后渲染 `### Meminfo Target/High-Load Follow-up`。
    6. 提示词/技能文档现在要求紧接 AnrManager 负载分析后使用 meminfo 后续分析小节。
- **验证**:
    - `python3 -m unittest tests.test_meminfo_filter -v`
    - `python3 -m unittest tests.test_ai_context tests.test_meminfo_filter tests.test_source_workflow -v`
    - `python3 -m compileall -q anr_evidence tests scripts`
    - `python3 -m unittest discover -s tests -v`
    - 案例重新生成: `python3 scripts/anr_to_ai.py /Users/yuchuan.gu/Downloads/5LLN85HMOBSK9P65_2026_05_07_12_04_07 --package com.tcl.android.launcher --anr-type no_focus_window`
    - 自动检查: 生成的 `cache.md` 紧接 `### AnrManager Diagnostic Block` 之后渲染 `### Meminfo Target/High-Load Follow-up`；工作流证据源顺序为 `trace,event_log,logcat,meminfo,kernel_log`。
- **后续要求**:
    - meminfo 对基线完整性而言仍为可选，但在存在时必须紧接 AnrManager 总负载/高负载进程归因进行内存后续分析。

<a id="2026-05-13---tooling-system_logmeminfo-target-and-high-load-process-filter"></a>
## [2026-05-13] - 工具：System_log/meminfo 目标进程和高负载进程过滤器
- **背景**: 用户说明内存信息位于 `System_log/meminfo.txt`，要求基于该文件新增脚本，过滤当前包名内存信息以及高负载进程的内存信息，用于配合 AnrManager 总负载优先的负载归因。
- **状态**: 已实现并验证。
- **主要变更**:
    1. 新增 `anr_evidence/sources/meminfo/`:
        - `parse_meminfo_snapshots(...)` 解析重复的 dumpsys meminfo 快照。
        - `filter_meminfo_source(...)` 保留目标包的 PSS/RSS 历史、目标包最新条目、所请求的高负载进程/PID 的内存信息，以及最新的 PSS/RSS 占用最高进程。
        - `MeminfoFilterOptions` 控制包名、高负载进程名/PID、排名前 N 项的数量及全快照搜索。
    2. 新增独立 CLI:
        - `scripts/anr_meminfo_filter.py <meminfo-or-bugreport-dir> --package <pkg> [--process <name>] [--pid <pid>] [--top N] [--all-snapshots]`
        - 目录输入优先使用 `System_log/meminfo.txt`。
    3. 从 `anr_evidence` 和 `anr_evidence.sources` 导出 meminfo 过滤 API。
    4. 新增 `tests/test_meminfo_filter.py`，覆盖解析器/过滤器和 CLI 目录输入冒烟测试。
- **验证**:
    - `python3 -m unittest tests.test_meminfo_filter -v`
    - `python3 -m compileall -q anr_evidence tests scripts`
    - 案例冒烟测试: `python3 scripts/anr_meminfo_filter.py /Users/yuchuan.gu/Downloads/5LLN85HMOBSK9P65_2026_05_07_12_04_07 --package com.tcl.android.launcher --top 5`
- **后续要求**:
    - 当 AnrManager 显示目标进程或外部进程的 CPU/IO 高负载时，先使用此 meminfo 过滤器验证内存增长/压力，再断言内存泄漏、OOM 或外部内存压力的因果关系。

<a id="2026-05-13---skill-rule-anrmanager-total-first-load-attribution"></a>
## [2026-05-13] - 技能规则：AnrManager 总负载优先的负载归因
- **背景**: 用户要求优化 logcat/AnrManager 分析技能：先分析 `Total` 值判断整体负载或 IO，再看目标包负载；若目标包或其它进程负载高，需要继续看内存信息，判断泄漏/OOM 或外部压力。
- **状态**: 已实现并验证。
- **主要变更**:
    1. `anr_evidence/ai_context.py` 的提示词增加 AnrManager 负载归因顺序：
        - `CPU TOTAL` / `iowait` 先判断整体 CPU 或 IO。
        - `Top CPU processes` 再判断目标包是否高负载。
        - 目标包高负载时联动 meminfo/PSI/GC/LMK/OOM 判断内存泄漏、内存抖动或 OOM 放大。
        - 其它进程高负载时检查该进程内存/IO 证据，作为外部系统压力或跨进程影响候选。
        - 缺少内存证据时禁止直接下泄漏/OOM 结论。
    2. `skills/anr-load.md`、`skills/anr-analysis.md`、`skills/anr-root-cause.md` 同步该判断准则。
    3. `tests/test_ai_context.py` 增加生成提示词规则校验。
- **验证**:
    - `python3 -m unittest tests.test_ai_context -v`
    - `python3 -m compileall -q anr_evidence tests`
    - `python3 -m unittest discover -s tests -v`
    - 案例重新生成: `python3 scripts/anr_to_ai.py /Users/yuchuan.gu/Downloads/5LLN85HMOBSK9P65_2026_05_07_12_04_07 --package com.tcl.android.launcher --anr-type no_focus_window`
    - 自动检查: 生成的 `anr_ai_context/ai_prompt.md` 包含优先查看 `CPU TOTAL`/`iowait`、`Top CPU processes`、内存证据后续分析及外部进程压力指引。
- **后续要求**:
    - 以后分析 AnrManager CPU/`Load` 时必须遵循总负载优先，再看高负载进程，再用内存证据归因；不要只凭某进程 CPU 占用最高就直接定责。

<a id="2026-05-13---prompt-standard-mandatory-source-by-source-anr-analysis"></a>
## [2026-05-13] - 提示词标准：强制逐源展开 ANR 分析
- **背景**: 用户反馈 ANR 分析结果未按技能标准展开 Trace、EventLog、logcat 的详细分析，只看到综合结论，缺少可审计的分源证据链。
- **状态**: 已实现并验证。
- **主要变更**:
    1. `anr_evidence/ai_context.py` 的 AI 提示词新增 `Mandatory Source-by-Source Analysis Standard`：
        - Trace 必须展开文件/分组、进程、主线程、栈帧、`schedstat`、死锁/Trace 提示、持锁者/对端证据缺口。
        - EventLog 必须以 `am_anr` 为基准，解释前 12 秒保留标签的 ΔT、类别和根因链意义。
        - Logcat/AnrManager 必须解释输入/WMS/AM/AnrManager 关键行、窗口/焦点/表面顺序、CPU/PSI/`Load`/转储字段。
        - 必须做 Trace ↔ EventLog ↔ Logcat 交叉验证。
    2. `Required Markdown Output`（必需的 Markdown 输出）强制包含：
        - `## Trace evidence analysis`
        - `## EventLog evidence analysis`
        - `## Logcat and AnrManager evidence analysis`
    3. 结构化 JSON 尾部新增 `sourceAnalyses.trace/eventLog/logcat`，便于自动检查分源分析是否缺失。
    4. `skills/anr-analysis.md` 的输出模板同步上述技能标准。
- **验证**:
    - `python3 -m unittest tests.test_ai_context -v`
    - `python3 -m compileall -q anr_evidence tests`
    - `python3 -m unittest discover -s tests -v`
    - 案例重新生成: `python3 scripts/anr_to_ai.py /Users/yuchuan.gu/Downloads/5LLN85HMOBSK9P65_2026_05_07_12_04_07 --package com.tcl.android.launcher --anr-type no_focus_window`
    - 自动检查: 生成的 `anr_ai_context/ai_prompt.md` 包含 `Mandatory Source-by-Source Analysis Standard`、`Trace evidence analysis`、`EventLog evidence analysis`、`Logcat and AnrManager evidence analysis` 和 `sourceAnalyses`。
- **后续要求**:
    - 后续 ANR 分析报告不能只输出综合根因；必须先给 Trace、EventLog、Logcat/AnrManager 分源详细分析，再汇总候选根因链。

<a id="2026-05-13---fix-eventlog-12s-pre-anr-cache-filtering-and-target-package-shard-selection"></a>
## [2026-05-13] - 修复：ANR 前 12 秒的 EventLog 缓存过滤及目标包分片选择
- **背景**: 用户在分析 `5LLN85HMOBSK9P65_2026_05_07_12_04_07` / `com.tcl.android.launcher` 时发现生成的 EventLog 缓存错误回退到无关 ANR，且 EventLog 过滤漏掉 `am_anr` 前 12 秒内的关键 AM/WM/输入标签上下文。
- **状态**: 已实现并验证。
- **主要变更**:
    1. EventLog 缓存过滤现在遵循 `docs/hermes-gemma-algorithm-design.md`:
        - 使用目标包的 `am_anr` 作为锚点。
        - 保留 ANR 前 12 秒窗口内文档规定的 EventLog 标签。
        - 仅在锚点发现时应用包名过滤，不过滤锚点前窗口中的上下文行。
    2. `docs/event-log-tags-reference.md` 从占位/截断示例扩展为焦点/窗口/生命周期分析所需的具体标签，包括 `wm_task_created`、`wm_create_activity`、`wm_pause_activity`、`wm_on_paused_called`、`wm_add_to_stopping`、`am_proc_start`、`am_proc_bound`、`am_mem_factor`、`am_freeze` 和 `am_unfreeze`。
    3. 目录/包加载现在使用目标包 EventLog 中的 `am_anr` 时间戳，在多 ANR 的 Monkey 结果目录中选择匹配的 trace、EventLog 分片和 logcat 分片。
    4. `scripts/anr_to_ai.py` 将 `--package` 传入包加载过程，使 AI 上下文生成能够在分组前选择与目标对齐的分片。
- **案例验证**:
    - 命令: `python3 scripts/anr_to_ai.py /Users/yuchuan.gu/Downloads/5LLN85HMOBSK9P65_2026_05_07_12_04_07 --package com.tcl.android.launcher --anr-type no_focus_window`
    - 结果: 分组锚点修正为 `05-03 10:00:57.460 am_anr ... com.tcl.android.launcher`。
    - EventLog 缓存保留了目标窗口内的 32 行，包括 `input_focus`、`wm_task_created`、`wm_create_activity`、`wm_pause_activity`、`wm_on_paused_called`、`wm_add_to_stopping`、`am_proc_start`、`am_proc_bound`、`am_mem_factor` 和 `am_anr`。
- **新增/更新测试**:
    - `test_event_log_package_filter_applies_to_anchor_only_when_requested`
    - `test_default_event_log_tags_cover_docs_reference`
    - `test_event_log_cache_keeps_documented_tags_without_package_filtering_context`
    - `test_package_name_prefers_matching_trace_for_sharded_event_log_selection`
- **验证**:
    - `python3 -m unittest discover -s tests -v`
    - 结果: `Ran 261 tests ... OK`
    - `python3 -m compileall -q anr_evidence tests`
    - 结果: OK
- **后续要求**:
    - 不要要求 EventLog 锚点前窗口的上下文行匹配包名；生命周期/焦点证据通常属于 system_server、下一个应用或其他进程。
    - 保持 `docs/event-log-tags-reference.md` 作为 EventLog 缓存标签覆盖范围的权威来源。

<a id="2026-05-12---review-fix-smart-discovery-trace-preservation"></a>
## [2026-05-12] - 评审修复：智能发现时保留 Trace
- **背景**: 代码评审发现，分证据源工作流重构后出现了两处智能发现回归。
- **状态**: 已在远程提交前修复并验证。
- **修复内容**:
    1. 智能目录发现不再提前结束，除非所有基线证据源类型均已齐备，从而防止 trace 位于 `System_log/anr` 之外（例如 `data/anr/`）时丢失。
    2. `System_log/anr/anr_YYYY-MM-DD-...` 下的 trace 候选现在使用 trace 专用的文件名/内容时间戳过滤，而非日志分片时间戳解析。
- **新增测试**:
    - `test_smart_discovery_falls_back_when_trace_is_outside_system_log`
    - `test_smart_discovery_filters_system_log_trace_candidates_by_trace_timestamp`
- **验证**:
    - `python3 -m unittest tests.test_directory_loading -v`
    - 结果: OK

<a id="2026-05-12---refactor-source-specific-anr-filter-entrypoints--workflow"></a>
## [2026-05-12] - 重构：分证据源 ANR 过滤入口与工作流
- **背景**: 用户要求重构智能体过滤工具，使 trace、EventLog、logcat 分别独立，启用独立入口，将依赖放入独立文件夹，并通过工作流串联完整过滤过程。
- **状态**: 已实现；评审发现仍需后续修复。
- **主要变更**:
    1. 新增分证据源过滤包:
        - `anr_evidence/sources/trace/` 用于 trace 时间戳解析和 trace 证据过滤。
        - `anr_evidence/sources/event_log/` 用于 EventLog 过滤。
        - `anr_evidence/sources/logcat/` 用于 logcat 过滤和 AnrManager 块提取。
        - `anr_evidence/sources/shared/` 用于共享结果类型、证据构建、时间戳解析和前序分片选择。
    2. 新增工作流编排:
        - `anr_evidence/workflow.py` 暴露 `run_filter_workflow(...)`，并保持阶段 1 证据顺序兼容。
        - `extract_baseline_evidence(...)` 现在将基线过滤交由工作流处理。
    3. 新增独立 CLI 入口:
        - `scripts/anr_trace_filter.py`
        - `scripts/anr_event_log_filter.py`
        - `scripts/anr_logcat_filter.py`
        - `scripts/anr_filter_workflow.py`
    4. 保持兼容性:
        - 现有阶段 1-8 的 CLI 行为保持兼容。
        - `ai_context.py` 现在复用 logcat 证据源入口来提取 AnrManager。
    5. 新增测试:
        - `tests/test_source_workflow.py` 覆盖证据源入口、共享前序分片选择、工作流证据顺序和 CLI 冒烟检查。
        - `tests/test_directory_loading.py` 已更新以覆盖带时间戳分片的前序分片选择。
- **验证**:
    - `python3 -m unittest discover -s tests -v`
    - 结果: `Ran 255 tests ... OK`
    - `python3 -m compileall -q anr_evidence tests scripts`
    - 结果: OK
- **评审后续事项**:
    1. **P1**: 智能目录发现在存在 `System_log/` 分片时会提前结束完整递归加载；这可能丢弃位于 `data/anr/` 等常规 bugreport 路径中的 trace 文件。修复方式是合并智能发现条目与完整遍历结果，或仅在所有基线证据源均已找到时提前结束。
    2. **P2**: `System_log/anr/anr_YYYY-MM-DD-...` 下的 trace 候选使用了面向 `_MM_DD_HH_MM_SS.txt` 的分片文件名解析来过滤；多 ANR 运行可能保留无关 trace。修复方式是对 trace 候选使用 `parse_trace_filename_timestamp` / trace 内容时间戳过滤。
- **后续要求**:
    - 智能发现过程中绝不能丢弃基线 trace 证据。
    - 分证据源入口应保持可独立调用，由工作流负责与现有阶段输出兼容的编排。

<a id="2026-05-05---commit-record"></a>
## [2026-05-05] - 提交记录
- **提交**: `ddc5cf9f870755c3c90bd61cf4f83b94f3150e5b`
- **说明**: 当前 ANR 分析重构和网页工作台变更已提交到仓库。此记录保留最终提交哈希以便追溯。

<a id="2026-05-05---implementation-type-aware-anr-ai-context-pipeline"></a>
## [2026-05-05] - 实现：感知类型的 ANR AI 上下文流水线
- **背景**: 当前分析流程最初偏向输入超时；后续会扩展更多 ANR 类型，因此过滤窗口、过滤关键词、分组规则、AI 分析关注点需要按类型独立分支，避免把输入超时逻辑硬编码到主流程。
- **状态**: 已实现并验证。
- **主要变更**:
    1. 新增可复用的日志过滤基础设施:
        - `anr_evidence/log_filter.py` 提供共享时间戳解析、分证据源模式过滤、EventLog 锚点前过滤和分块大文件扫描。
        - `anr_log_pattern_filter.py` 现在使用共享过滤实现，而非加载整个文件。
    2. 新增 AI 上下文产物构建器:
        - `anr_evidence/ai_context.py` 按 trace → EventLog → logcat 构建分组 ANR 上下文。
        - 在本次运行专用的输出目录中输出 `cache.md`、`ai_prompt.md` 和 `summary.json`。
        - 主要按 EventLog 的 `am_anr` 时间戳对记录分组，缺少 EventLog 时使用回退锚点。
    3. 新增感知类型的策略层:
        - `anr_evidence/anr_strategy.py` 定义 `AnrTypeStrategy` 和 `ANR_TYPE_STRATEGIES`。
        - 当前策略: `input_dispatching_timeout`、`no_focus_window` 和 `unknown`。
        - 策略控制 EventLog 窗口、logcat 窗口、分组容差、EventLog 标签、logcat 模式、回退锚点和 AI 提示词关注点。
        - 未来应通过注册新策略来添加 ANR 类型，而非改变编排流程。
    4. 新增 CLI 支持:
        - `--build-ai-context` 生成 AI 上下文产物。
        - `--out-dir` 选择产物目录。
        - `--anr-type` 可选择覆盖推断出的类型策略。
        - 省略窗口选项时，默认采用所选类型策略的配置。
    5. 新增测试:
        - `tests/test_log_filter.py` 覆盖共享过滤和分块 EventLog 扫描。
        - `tests/test_ai_context.py` 覆盖分组缓存/提示词生成及未知/未来类型兼容性。
- **验证**:
    - `python3 -m unittest discover -q`
    - 结果: `Ran 124 tests ... OK`
- **后续要求**:
    - 不要将未来 ANR 类型的专用过滤直接加入 `ai_context.py` 或 CLI 分支。
    - 在 `anr_evidence/anr_strategy.py` 中新增或更新 `AnrTypeStrategy` 条目，使每种 ANR 类型拥有各自的过滤和分析默认值。

<a id="2026-05-01-0905---implementation-phase-1-filtering-layer"></a>
## [2026-05-01 09:05] - 实现：阶段 1（过滤层）
- **任务**: 重新设计 `anr_log_pattern_filter.py`，以支持重要性加权和自适应时间窗口。
- **行动**:
    - 已创建 `docs/filtering_config_spec.md`，定义新的基于 JSON 的配置模式。
    - 正在准备重构 Python 过滤脚本。

<a id="2026-05-01-0900---initialization"></a>
## [2026-05-01 09:00] - 初始化
- **背景**: 用户要求实现架构评审中讨论的优化建议。
- **状态**: 正在初始化优化阶段。
- **计划**:
    1.  **阶段 1**: 在过滤层实现“语义敏感度加权”（投资回报率最高）。
    2.  **阶段 2**: 在采集层实现“上下文嗅探”。
    3.  **阶段 3**: 在分析层实现“证据图”逻辑。
