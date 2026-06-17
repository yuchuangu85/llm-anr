# Operation Log: ANR Intelligent Analysis System Optimization

> **规则：条目按日期反序排列（最新的在最前面）。新增记录时请插入到标题下方第一条位置，保持反序一致。**

## [2026-06-17] - Chore: Clean Up Stale Markdown Files
- **Commit**: `52c3d13`
- **Context**: 删除不再需要的 markdown 文件。
- **Status**: Completed.

## [2026-06-17] - Feat: Strengthen Evidence Extraction Pipeline
- **Commit**: `93890a1`
- **Context**: AI 上下文构建、AnrManager 解析、包加载、trace 预处理和根因 hint 检测需要全面增强，提升提取准确性和分析质量。
- **Status**: Implemented and verified.
- **Key Changes**:
    1. 增强 AI context building 逻辑。
    2. 增强 AnrManager parsing：更鲁棒的 block 提取和字段解析。
    3. 增强 package loading：更精确的多 ANR 包名选择。
    4. 增强 trace preprocessing：更好的线程状态和栈帧结构化。
    5. 增强 root-cause hint detection：更丰富的 `rootCausePatternHints`。
    6. 扩展测试覆盖：`ai_context`、`anrmanager_parser`、source workflow。
    7. 新增 `docs/` 下的设计/开发博客文档。
- **Verification**:
    - `python3 -m unittest discover -s tests -v` → OK
    - `python3 -m compileall -q anr_evidence tests scripts` → OK

## [2026-06-11] - Fix: Analysis Slot Template Detection Enhancement
- **Commit**: `67d225f`
- **Context**: 旧模板检测只检查 `_Pending AI analysis` / `待 AI 分析填写` 两个遗留标记，无法识别新格式模板占位，导致已填写的分析槽被覆盖。
- **Status**: Implemented and verified.
- **Key Changes**:
    1. 新增 `_请用`/`_列出`/`_评估` 标记检测，防止已填写 slot 被覆盖。
    2. 新增 per-slot 结构化输出模板，包含 conclusion、evidence、gaps、confidence 等分段，替代模糊的一行 prompt。
- **Verification**:
    - `python3 -m unittest discover -s tests -v` → OK
    - `python3 -m compileall -q anr_evidence tests scripts` → OK
- **Forward Directive**:
    - 新增分析 slot 格式时，必须同步添加模板检测标记，避免回退覆盖。

## [2026-06-10] - Refactor: Per-ANR Independent Workspace Architecture
- **Commit**: `6b5b9db`
- **Context**: 多 ANR 分析需要每个 ANR group 拥有完全独立的工作空间，避免证据混杂和跨 ANR 状态泄露。
- **Status**: Implemented and verified.
- **Key Changes**:
    1. Agent 文档与 per-ANR 分析工作空间契约对齐。
    2. 新增 CI/打包支持。
    3. LLM API 调用增强错误处理。
    4. 多 ANR 上下文生成复用昂贵的解析（trace/EventLog 扫描），但不共享 per-group 可变证据状态。
    5. AI context 使用 `index.json` + per-ANR `anr_analysis.md`/`logcat.txt` 工作空间，替代旧的 `cache.md`/`ai_prompt.md`/`summary.json`。
- **Decisions**:
    - **Rejected**: 缓存完整 trace 结果但不加 per-group 防御性拷贝 — cross-ANR corroboration bleed 风险。
    - **Rejected**: 将 prepared logcat 行传入 `filter_timestamp_window(content, ...)` — `filter_prepared_timestamp_window` 保持 prepared-source 溯源明确。
- **Verification**:
    - `python3 -m unittest discover -s tests -v` → 285 tests, 1 skipped OK
    - `python3 -m compileall -q anr_evidence tests scripts` → OK
    - `scripts/anr_to_ai.py tests/fixtures/nfw_01.json` smoke → OK
- **Forward Directive**:
    - 新的 evidence source 必须 scoped per ANR group。
    - 永远不要在多 ANR 之间共享可变证据状态。
    - `index.json` + `anr_analysis.md` + `logcat.txt` 是新的标准上下文格式。

## [2026-05-29] - Docs: Development Handoff Guide
- **Commit**: `5b7cea3`
- **Context**: 记录仓库结构和扩展工作流，确保后续 ANR pipeline 变更能保持证据保证和验证预期。
- **Status**: Completed (documentation only).
- **Key Changes**:
    1. 新增 `docs/DEVELOPMENT_GUIDE.md`：包含仓库地图、扩展工作流、证据保证和验证预期。
- **Forward Directive**:
    - 修改 pipeline phases、source filters 或 AI context artifacts 时，同步更新该指南。

## [2026-05-27] - Docs: Update Bilingual README
- **Commit**: `30d5793`
- **Context**: README 需要反映当前 per-ANR workspace 工作流、扩展触发策略、候选根因 hints、分源证据处理和最新测试数量。
- **Status**: Completed (documentation only).
- **Key Changes**:
    1. 更新中英文 README，同步最新 pipeline 能力和工作流。
    2. 确保 `triggerType` 与 `rootCausePatternHints` 语义独立。
- **Forward Directive**:
    - README 必须保持与当前实现一致；新增 ANR 类型或工作流变更后同步更新。

## [2026-05-24] - Feat: Expand ANR Trigger Strategy Coverage
- **Commits**: `d21d430`, `0be9bf4`
- **Context**: 当前 AnrTypeStrategy 主要覆盖 `input_dispatching_timeout` 和 `no_focus_window`，缺少 Broadcast、Service、ContentProvider、JobScheduler、system Watchdog/SWT 等 ANR 触发策略。
- **Status**: Implemented and verified.
- **Key Changes**:
    1. 新增 Broadcast、Service、ContentProvider、JobScheduler、system Watchdog/SWT 触发策略。
    2. 基线证据提取保持 additive，新类型不删除现有证据源。
    3. `triggerType` 与 `rootCausePatternHints[]` 明确分离：hints 为候选且非最终结论。
    4. Deadlock、memory pressure、high-load 证据可丰富分析但不替换 ANR 类型分类。
    5. `0be9bf4` 将扩展计划独立文档化：新增 ANR 触发策略和根因模式 hints 的规划文档。
- **Decisions**:
    - **Rejected**: 把 root-cause patterns 编码为 ANR types — 会混淆触发机制与候选根因，破坏 unknown-trigger fallback 语义。
- **Verification**:
    - `python3 -m unittest discover -s tests -q` → OK
    - `python3 -m compileall -q anr_evidence tests scripts` → OK
- **Forward Directive**:
    - `rootCausePatternHints` 永远是候选非最终；未来类型专用 skill 应挂载到 `AnrTypeStrategy` 而非硬编码 prompt 分支。
    - 新增 ANR 类型时必须同步更新 `AnrTypeStrategy` 注册表，不要直接改主流程。

## [2026-05-16] - Fix: Preserve ANR Time in Unanchored Context IDs
- **Commit**: `235a393`
- **Context**: 缺少 EventLog 锚点的上下文目录使用 `anr-unanchored` 命名，多次 fallback 生成时冲突；ANR1 风格 log 集应匹配对应 `am_anr` 而非过期 archive trace。
- **Status**: Implemented and verified.
- **Key Changes**:
    1. 未锚定 AI 上下文目录使用 trace 中提取的 ANR 时间戳命名（如 `anr-20260422-023434-522`）。
    2. 松散目录证据合并到归档旁，使 ANR1 风格 log 集解析到匹配的 `am_anr`。
    3. 包名过滤 `am_anr` 匹配保持严格：指定 package 时，不匹配的 `am_anr` 不能成为锚点。
- **Verification**:
    - `python3 -m unittest discover -s tests -v` → OK
    - `python3 -m compileall -q anr_evidence tests` → OK
    - Case smoke: `scripts/anr_to_ai.py` 对 ANR1 样本生成 `anr-20260422-023434-522`
- **Forward Directive**:
    - 上下文目录命名必须包含 ANR 时间，禁止使用不含时间的无歧义名称。

## [2026-05-16] - Refactor: Extract Logcat to Separate AI Context Artifact
- **Commit**: `47bb654`
- **Context**: 当前 `anr_analysis.md` 内联大量 logcat 文本，导致分析 prompt 臃肿，浪费上下文窗口。
- **Status**: Implemented and verified.
- **Key Changes**:
    1. logcat 过滤结果写入独立 `logcat.txt` 文件。
    2. `anr_analysis.md` 改为引用 `logcat.txt` 而非内联。
    3. AI context 生成输出结构调整：每 ANR group 目录下包含 `anr_analysis.md` + `logcat.txt`。
- **Verification**:
    - `python3 -m unittest discover -s tests -v` → OK
    - `python3 -m compileall -q anr_evidence tests scripts` → OK
- **Forward Directive**:
    - logcat 文本量极大，必须始终保持为独立文件，不要回到内联模式。

## [2026-05-13] - Skill Optimization: Complete ANR Trace Analysis Skill Consolidation
- **Context**: 用户要求持续优化 Trace 分析 skill，使其达到 `ANR-trace文件分析.md` 同等详细度，并将 `ANR-trace覆盖清单.md`、`ANR基础知识.md`、`ANR-trace文件分析.md` 的 Trace 专项知识合并到 `skills/anr-trace-analysis.md`。
- **Status**: Implemented and verified.
- **Key Changes**:
    1. `skills/anr-trace-analysis.md` 扩展为完整 Trace 专项分析 skill：保留"只基于 Trace 形成专项结论、跨源证据只标缺口"的边界。
    2. 合并 `ANR-trace覆盖清单.md`：新增当前实现落点、可稳定依赖能力、字段覆盖状态、7 类自动分类规则覆盖边界、不能仅凭单份 trace 证明的结论、P1/P2/P3 后续增强 TODO。
    3. 合并 `ANR基础知识.md`：新增 Linux 状态解释、常见 ANR 原因分类、Trace 字段含义、Thread.java ↔ ART Thread.cpp 状态映射、CPU/内存负载旁证解释。
    4. 合并 `ANR-trace文件分析.md`：新增 Trace 文件阅读顺序、进程归属判断、特殊线程说明、mutex 缩写、Java/ART/Linux/Perfetto 根因映射、MONITOR/SUSPENDED 判定边界、典型 Trace 形态速查。
    5. 输出契约增强：Trace 分析输出必须包含 `Coverage boundary`，明确"已覆盖 / 部分覆盖 / 未证明 / 需要跨源补证"。
- **Verification**:
    - `python3 -m unittest tests.test_ai_context -v` → OK
    - `python3 -m compileall -q anr_evidence tests` → OK
    - Manual content check: `skills/anr-trace-analysis.md` contains `ANR 基础知识速查（Trace 分析必用）`, `Trace 文件阅读顺序与归属判断`, `Java / ART / Linux / Perfetto 根因映射`, `Trace 典型形态速查`, and `最可靠结论与禁止过度承诺`.
- **Forward Directive**:
    - Trace skill 的结论必须保持保守：单份 trace 只能证明采样瞬间的阻塞/等待形态；CPU 抢占、STW/GC、Binder 对端归属、Render/GPU 闭环、InputDispatcher timeout 真实触发点必须通过 EventLog/Logcat/AnrManager/Perfetto/CPU/meminfo 等跨源补证。
    - 后续修改 Trace 分析输出时，必须保留 `Coverage boundary` 字段，避免把静态 trace hint 误写成最终根因。

## [2026-05-13] - Output Contract: One Independent AI Context Per ANR
- **Context**: 用户要求一份 log 中可能包含同一包名的多个 ANR，输出的 `anr_ai_context` 必须独立拆分，每个 `anr_ai_context` 只保留一份 ANR 信息，避免多个 ANR 的 Trace/EventLog/logcat/AnrManager/meminfo 证据混杂。
- **Status**: Implemented and verified.
- **Key Changes**:
    1. `build_ai_context_artifacts(...)` 输出策略调整：
        - 单 ANR 场景保持兼容，继续输出顶层 `cache.md`、`ai_prompt.md`、`summary.json`。
        - 多 ANR 场景不再生成混合顶层 `cache.md` / `ai_prompt.md` / `summary.json`。
        - 多 ANR 场景改为输出 `index.json`，并为每个 ANR group 生成独立目录：`<out-dir>/<groupId>/cache.md`、`ai_prompt.md`、`summary.json`。
    2. AnrManager 提取改为 anchor-aware：
        - 新增 `AnrManagerBlock` 与 `extract_anrmanager_blocks(...)`，先提取同包名全部 AnrManager dump flow。
        - `extract_anrmanager_block(..., anchor_dt=...)` 根据当前 ANR anchor 选择最近 block。
        - 找不到匹配 block 时返回 `missing-anrmanager-for-anchor`，禁止复用其它 ANR 的 AnrManager 信息。
    3. ANR anchor 去重规则收紧：
        - 不再仅按时间窗口合并 anchor。
        - 只有近时间且归一化内容一致的重复 `am_anr` 才合并，避免短时间内多个真实 ANR 被误合并。
    4. Meminfo follow-up 改为 anchor-aware：
        - `filter_meminfo_source(...)` 在存在 `anchor_dt` 时选择距离当前 ANR 最近的 snapshot。
        - metadata 增加 `selectedTimestamp`、`selectedSnapshotIndex`、`anchorTimestamp`，便于审计当前 ANR 的内存证据来源。
    5. CLI 提示同步：
        - `scripts/anr_to_ai.py` 在多 ANR 输出时提示逐个分析 `<out-dir>/<anr-id>/ai_prompt.md`。
- **Tests Added/Updated**:
    - `test_multi_anr_builds_independent_contexts_and_index`
    - `test_multi_anr_contexts_keep_distinct_anrmanager_blocks`
    - `test_extract_anchor_aware_block_from_repeated_package_anrs`
    - `test_extract_anchor_aware_block_does_not_reuse_wrong_anr`
    - `test_meminfo_filter_selects_snapshot_nearest_anchor`
- **Verification**:
    - `python3 -m unittest tests.test_anrmanager tests.test_ai_context tests.test_meminfo_filter -v`
    - Result: OK
    - `python3 -m unittest discover -s tests -v`
    - Result: `Ran 270 tests ... OK`
    - `python3 -m compileall -q anr_evidence tests`
    - Result: OK
    - Smoke check: synthetic multi-ANR package outputs only `index.json` plus two independent `anr-*` directories.
- **Forward Directive**:
    - 多 ANR log 不能再生成混合 AI prompt；每个 AI 分析输入必须只包含一个 ANR 的证据。
    - AnrManager、meminfo、trace、EventLog、logcat 后续过滤都必须以当前 group anchor 为边界，禁止跨 ANR 复用证据。

## [2026-05-13] - Fix: Complete Logcat AnrManager Flow Extraction
- **Context**: 用户反馈过滤结果中的 logcat/AnrManager 信息不全面，要求保证完整过滤出 AnrManager dump flow，覆盖 `startAnrDump`、stack dump、`ANR in`、Reason、Load/PSI、CPU windows、`dumpAnrDebugInfo end`、DropBox 等关键行。
- **Status**: Implemented and verified.
- **Key Changes**:
    1. `anr_evidence/log_filter.py`:
        - AnrManager anchor 改为优先选择 `dumpAnrDebugInfo end` / `addErrorToDropBox` / `ANR in` / `Reason` 等更完整的结束侧锚点。
        - AnrManager block 提取不再要求物理连续行；允许普通 logcat 行穿插，按 AnrManager tag 从最近的 `startAnrDump` / `dumpAnrDebugInfo begin` 收集到 `controller = null` / `addErrorToDropBox` / `dumpAnrDebugInfo end`。
        - 支持旧式 `I/AnrManager( 1377):` 与新式 `I AnrManager:` 日志格式，避免普通消息文本中包含 "AnrManager" 时误判为 tag 行。
    2. `anr_evidence/anrmanager_parser.py`:
        - 支持旧式 AnrManager 前缀、`Load: x / y / z`、多段 CPU window（`ago` + `later`）、`softirq/irq/iowait`、`+0%` 进程行、`mTracesFile`。
        - 保留 `cpuWindows[]`，同时保持旧字段 `cpuWindow` / `cpuTotal` 兼容。
        - 区分 `/proc/pressure/memory|cpu|io`，避免 CPU/IO PSI 覆盖 memory PSI。
        - 新增 `SYSTEM_SERVER_CPU_HIGH` 与 `ANR_PROCESS_CPU_HIGH` hints，用于 Total-first 后的高负载进程归因。
    3. Tests:
        - 增加旧式完整 AnrManager flow + interleaved logcat 行提取测试。
        - 增加旧式多 CPU window、Load、PSI、mTracesFile、高负载 hint 解析测试。
- **Verification**:
    - `python3 -m unittest tests.test_anrmanager tests.test_anrmanager_parser -v`
    - `python3 -m unittest tests.test_ai_context tests.test_meminfo_filter tests.test_source_workflow -v`
    - `python3 -m unittest discover -s tests -v` → 266 tests OK
    - `python3 -m compileall -q anr_evidence tests scripts`
    - Case regeneration: `python3 scripts/anr_to_ai.py /Users/yuchuan.gu/Downloads/5LLN85HMOBSK9P65_2026_05_07_12_04_07 --package com.tcl.android.launcher`
    - Auto-check: regenerated `cache.md` AnrManager block contains stack dump begin/end, `ANR in com.tcl.android.launcher`, `Reason`, `Load`, memory/cpu/io PSI, `TOTAL`, `dumpAnrDebugInfo end`, and `addErrorToDropBox`.
- **Forward Directive**:
    - AnrManager is critical evidence; future logcat filtering must preserve the whole dump flow, not only the timestamp-window subset or physically contiguous lines.

## [2026-05-13] - Workflow: Meminfo Follow-up After Logcat/AnrManager
- **Context**: 用户要求把内存过滤分析放到 logcat 中 AnrManager 分析后面的工作流中，而不是只提供独立脚本。
- **Status**: Implemented and verified.
- **Key Changes**:
    1. Directory/package loading now recognizes `System_log/meminfo.txt` as optional `meminfo` source without making it a mandatory baseline source.
    2. Smart Monkey discovery includes `System_log/meminfo.txt` when present.
    3. `run_filter_workflow(...)` now runs optional meminfo filtering immediately after logcat and before kernel evidence; missing meminfo does not emit missing-source warnings.
    4. `build_ai_context(...)` now runs meminfo follow-up after extracting/parsing the AnrManager block, using AnrManager Top CPU processes as high-load process inputs.
    5. `cache.md` now renders `### Meminfo Target/High-Load Follow-up` after `### AnrManager Diagnostic Block`.
    6. Prompt/skill docs now require using the meminfo follow-up section directly after AnrManager load analysis.
- **Verification**:
    - `python3 -m unittest tests.test_meminfo_filter -v`
    - `python3 -m unittest tests.test_ai_context tests.test_meminfo_filter tests.test_source_workflow -v`
    - `python3 -m compileall -q anr_evidence tests scripts`
    - `python3 -m unittest discover -s tests -v`
    - Case regeneration: `python3 scripts/anr_to_ai.py /Users/yuchuan.gu/Downloads/5LLN85HMOBSK9P65_2026_05_07_12_04_07 --package com.tcl.android.launcher --anr-type no_focus_window`
    - Auto-check: generated `cache.md` renders `### Meminfo Target/High-Load Follow-up` immediately after `### AnrManager Diagnostic Block`; workflow source order is `trace,event_log,logcat,meminfo,kernel_log`.
- **Forward Directive**:
    - Keep meminfo optional for baseline completeness, but when present it must be consumed as the immediate memory follow-up for AnrManager Total/Top load attribution.

## [2026-05-13] - Tooling: System_log/meminfo Target and High-Load Process Filter
- **Context**: 用户说明内存信息位于 `System_log/meminfo.txt`，要求基于该文件新增脚本，过滤当前包名内存信息以及高负载进程的内存信息，用于配合 AnrManager Total-first 负载归因。
- **Status**: Implemented and verified.
- **Key Changes**:
    1. Added `anr_evidence/sources/meminfo/`:
        - `parse_meminfo_snapshots(...)` parses repeated dumpsys meminfo snapshots.
        - `filter_meminfo_source(...)` retains target package PSS/RSS history, latest target entries, requested high-load process/pid memory, and latest top PSS/RSS consumers.
        - `MeminfoFilterOptions` controls package, high-load process names/pids, top-N size, and all-snapshot search.
    2. Added standalone CLI:
        - `scripts/anr_meminfo_filter.py <meminfo-or-bugreport-dir> --package <pkg> [--process <name>] [--pid <pid>] [--top N] [--all-snapshots]`
        - Directory input prefers `System_log/meminfo.txt`.
    3. Exported meminfo filter APIs from `anr_evidence` and `anr_evidence.sources`.
    4. Added `tests/test_meminfo_filter.py` for parser/filter and CLI directory smoke coverage.
- **Verification**:
    - `python3 -m unittest tests.test_meminfo_filter -v`
    - `python3 -m compileall -q anr_evidence tests scripts`
    - Case smoke: `python3 scripts/anr_meminfo_filter.py /Users/yuchuan.gu/Downloads/5LLN85HMOBSK9P65_2026_05_07_12_04_07 --package com.tcl.android.launcher --top 5`
- **Forward Directive**:
    - When AnrManager shows target or external high CPU/IO load, use this meminfo filter to verify memory growth/pressure before claiming leak, OOM, or external memory-pressure causality.

## [2026-05-13] - Skill Rule: AnrManager Total-First Load Attribution
- **Context**: 用户要求优化 logcat/AnrManager 分析 skill：先分析 Total 值判断整体负载或 IO，再看目标包负载；若目标包或其它进程负载高，需要继续看内存信息，判断泄漏/OOM 或外部压力。
- **Status**: Implemented and verified.
- **Key Changes**:
    1. `anr_evidence/ai_context.py` 的 prompt 增加 AnrManager 负载归因顺序：
        - `CPU TOTAL` / `iowait` 先判断整体 CPU 或 IO。
        - `Top CPU processes` 再判断目标包是否高负载。
        - 目标包高负载时联动 meminfo/PSI/GC/LMK/OOM 判断内存泄漏、内存抖动或 OOM 放大。
        - 其它进程高负载时检查该进程内存/IO 证据，作为外部系统压力或跨进程影响候选。
        - 缺少内存证据时禁止直接下泄漏/OOM 结论。
    2. `skills/anr-load.md`、`skills/anr-analysis.md`、`skills/anr-root-cause.md` 同步该判断准则。
    3. `tests/test_ai_context.py` 增加生成 prompt 规则校验。
- **Verification**:
    - `python3 -m unittest tests.test_ai_context -v`
    - `python3 -m compileall -q anr_evidence tests`
    - `python3 -m unittest discover -s tests -v`
    - Case regeneration: `python3 scripts/anr_to_ai.py /Users/yuchuan.gu/Downloads/5LLN85HMOBSK9P65_2026_05_07_12_04_07 --package com.tcl.android.launcher --anr-type no_focus_window`
    - Auto-check: generated `anr_ai_context/ai_prompt.md` contains `CPU TOTAL`/`iowait` first, `Top CPU processes`, memory evidence follow-up, and external process pressure guidance.
- **Forward Directive**:
    - 以后分析 AnrManager CPU/Load 时必须遵循 Total-first，再 Top process，再内存证据归因；不要只凭 Top 进程 CPU 高直接定责。

## [2026-05-13] - Prompt Standard: Mandatory Source-by-Source ANR Analysis
- **Context**: 用户反馈 ANR 分析结果未按 skill 标准展开 Trace、EventLog、logcat 的详细分析，只看到综合结论，缺少可审计的分源证据链。
- **Status**: Implemented and verified.
- **Key Changes**:
    1. `anr_evidence/ai_context.py` 的 AI prompt 新增 `Mandatory Source-by-Source Analysis Standard`：
        - Trace 必须展开文件/分组、进程、main thread、栈帧、schedstat、Deadlock/Trace Hints、owner/peer 缺口。
        - EventLog 必须以 `am_anr` 为基准，解释前 12 秒保留 tag 的 ΔT、类别和根因链意义。
        - Logcat/AnrManager 必须解释 Input/WMS/AM/AnrManager 关键行、窗口/focus/surface 顺序、CPU/PSI/Load/dump 字段。
        - 必须做 Trace ↔ EventLog ↔ Logcat 交叉验证。
    2. Required Markdown Output 强制包含：
        - `## Trace evidence analysis`
        - `## EventLog evidence analysis`
        - `## Logcat and AnrManager evidence analysis`
    3. Structured JSON tail 新增 `sourceAnalyses.trace/eventLog/logcat`，便于自动检查分源分析是否缺失。
    4. `skills/anr-analysis.md` 的输出模板同步上述 skill 标准。
- **Verification**:
    - `python3 -m unittest tests.test_ai_context -v`
    - `python3 -m compileall -q anr_evidence tests`
    - `python3 -m unittest discover -s tests -v`
    - Case regeneration: `python3 scripts/anr_to_ai.py /Users/yuchuan.gu/Downloads/5LLN85HMOBSK9P65_2026_05_07_12_04_07 --package com.tcl.android.launcher --anr-type no_focus_window`
    - Auto-check: generated `anr_ai_context/ai_prompt.md` contains `Mandatory Source-by-Source Analysis Standard`, `Trace evidence analysis`, `EventLog evidence analysis`, `Logcat and AnrManager evidence analysis`, and `sourceAnalyses`.
- **Forward Directive**:
    - 后续 ANR 分析报告不能只输出综合根因；必须先给 Trace、EventLog、Logcat/AnrManager 分源详细分析，再汇总候选根因链。

## [2026-05-13] - Fix: EventLog 12s Pre-ANR Cache Filtering and Target Package Shard Selection
- **Context**: 用户在分析 `5LLN85HMOBSK9P65_2026_05_07_12_04_07` / `com.tcl.android.launcher` 时发现生成的 EventLog 缓存错误 fallback 到无关 ANR，且 EventLog 过滤漏掉 `am_anr` 前 12 秒内的关键 AM/WM/Input tag 上下文。
- **Status**: Implemented and verified.
- **Key Changes**:
    1. EventLog cache filtering now follows `docs/算法设计.md`:
        - Use target-package `am_anr` as the anchor.
        - Retain documented EventLog tags in the 12s pre-ANR window.
        - Apply package filtering to anchor discovery only, not contextual pre-window lines.
    2. `docs/event_log_tags_master.md` was expanded from placeholder/truncated examples to concrete tags needed by focus/window/lifecycle analysis, including `wm_task_created`, `wm_create_activity`, `wm_pause_activity`, `wm_on_paused_called`, `wm_add_to_stopping`, `am_proc_start`, `am_proc_bound`, `am_mem_factor`, `am_freeze`, and `am_unfreeze`.
    3. Directory/package loading now uses the target package's EventLog `am_anr` timestamp to select the matching trace, EventLog shard, and logcat shard in multi-ANR Monkey result directories.
    4. `scripts/anr_to_ai.py` passes `--package` into package loading, so AI context generation can choose target-aligned shards before grouping.
- **Case Verification**:
    - Command: `python3 scripts/anr_to_ai.py /Users/yuchuan.gu/Downloads/5LLN85HMOBSK9P65_2026_05_07_12_04_07 --package com.tcl.android.launcher --anr-type no_focus_window`
    - Result: group anchor corrected to `05-03 10:00:57.460 am_anr ... com.tcl.android.launcher`.
    - EventLog cache retained 32 target-window lines, including `input_focus`, `wm_task_created`, `wm_create_activity`, `wm_pause_activity`, `wm_on_paused_called`, `wm_add_to_stopping`, `am_proc_start`, `am_proc_bound`, `am_mem_factor`, and `am_anr`.
- **Tests Added/Updated**:
    - `test_event_log_package_filter_applies_to_anchor_only_when_requested`
    - `test_default_event_log_tags_cover_docs_master`
    - `test_event_log_cache_keeps_documented_tags_without_package_filtering_context`
    - `test_package_name_prefers_matching_trace_for_sharded_event_log_selection`
- **Verification**:
    - `python3 -m unittest discover -s tests -v`
    - Result: `Ran 261 tests ... OK`
    - `python3 -m compileall -q anr_evidence tests`
    - Result: OK
- **Forward Directive**:
    - Do not require package-name matches on EventLog pre-window context lines; lifecycle/focus evidence often belongs to system_server, the next app, or other processes.
    - Keep `docs/event_log_tags_master.md` as the source of truth for EventLog cache tag coverage.

## [2026-05-12] - Review Fix: Smart Discovery Trace Preservation
- **Context**: Code review identified two smart-discovery regressions after source-specific workflow refactor.
- **Status**: Fixed and verified before remote submission.
- **Fixes**:
    1. Smart directory discovery no longer short-circuits unless all baseline source kinds are present, preventing trace loss when traces live outside `System_log/anr` (for example `data/anr/`).
    2. `System_log/anr/anr_YYYY-MM-DD-...` trace candidates are now filtered with trace-specific filename/content timestamps instead of log shard timestamp parsing.
- **Tests Added**:
    - `test_smart_discovery_falls_back_when_trace_is_outside_system_log`
    - `test_smart_discovery_filters_system_log_trace_candidates_by_trace_timestamp`
- **Verification**:
    - `python3 -m unittest tests.test_directory_loading -v`
    - Result: OK

## [2026-05-12] - Refactor: Source-specific ANR Filter Entrypoints + Workflow
- **Context**: 用户要求重构 agent 过滤工具，使 trace、EventLog、logcat 分别独立，启用独立入口，将依赖放入独立文件夹，并通过 Workflow 串联完整过滤过程。
- **Status**: Implemented; review identified follow-up fixes required.
- **Key Changes**:
    1. Added source-specific filtering packages:
        - `anr_evidence/sources/trace/` for trace timestamp parsing and trace evidence filtering.
        - `anr_evidence/sources/event_log/` for EventLog filtering.
        - `anr_evidence/sources/logcat/` for logcat filtering and AnrManager block extraction.
        - `anr_evidence/sources/shared/` for shared result types, evidence building, timestamp parsing, and predecessor shard selection.
    2. Added workflow orchestration:
        - `anr_evidence/workflow.py` exposes `run_filter_workflow(...)` and keeps Phase 1 evidence ordering compatible.
        - `extract_baseline_evidence(...)` now delegates baseline filtering to the workflow.
    3. Added standalone CLI entrypoints:
        - `scripts/anr_trace_filter.py`
        - `scripts/anr_event_log_filter.py`
        - `scripts/anr_logcat_filter.py`
        - `scripts/anr_filter_workflow.py`
    4. Preserved compatibility:
        - Existing Phase 1-8 CLI behavior remains compatible.
        - `ai_context.py` now reuses the logcat source entrypoint for AnrManager extraction.
    5. Added tests:
        - `tests/test_source_workflow.py` covers source entrypoints, shared predecessor selection, workflow evidence order, and CLI smoke checks.
        - `tests/test_directory_loading.py` updated for timestamped shard predecessor selection.
- **Verification**:
    - `python3 -m unittest discover -s tests -v`
    - Result: `Ran 255 tests ... OK`
    - `python3 -m compileall -q anr_evidence tests scripts`
    - Result: OK
- **Review Follow-ups**:
    1. **P1**: Smart directory discovery currently short-circuits full recursive loading when `System_log/` shards exist; it can drop trace files located in normal bugreport paths such as `data/anr/`. Fix by merging smart entries with full traversal or only short-circuiting when all baseline sources are found.
    2. **P2**: Trace candidates under `System_log/anr/anr_YYYY-MM-DD-...` are filtered with shard filename parsing intended for `_MM_DD_HH_MM_SS.txt`; multi-ANR runs can retain unrelated traces. Fix by using `parse_trace_filename_timestamp` / trace content timestamp filtering for trace candidates.
- **Forward Directive**:
    - Baseline trace evidence must never be dropped during smart discovery.
    - Source-specific entrypoints should remain independently callable, while workflow owns orchestration and compatibility with existing Phase outputs.

## [2026-05-05] - Commit Record
- **Commit**: `ddc5cf9f870755c3c90bd61cf4f83b94f3150e5b`
- **Note**: The current ANR analysis refactor and web workbench changes have been committed to the repository. This record preserves the final commit hash for traceability.

## [2026-05-05] - Implementation: Type-aware ANR AI Context Pipeline
- **Context**: 当前分析流程最初偏向 input timeout；后续会扩展更多 ANR 类型，因此过滤窗口、过滤关键词、分组规则、AI 分析关注点需要按类型独立分支，避免把 input timeout 逻辑硬编码到主流程。
- **Status**: Implemented and verified.
- **Key Changes**:
    1. Added reusable log filtering foundation:
        - `anr_evidence/log_filter.py` provides shared timestamp parsing, source-specific pattern filtering, EventLog pre-anchor filtering, and chunked large-file scanning.
        - `anr_log_pattern_filter.py` now uses the shared filter implementation instead of loading the whole file.
    2. Added AI context artifact builder:
        - `anr_evidence/ai_context.py` builds grouped ANR context from trace → EventLog → logcat.
        - Outputs `cache.md`, `ai_prompt.md`, and `summary.json` under a run-scoped output directory.
        - Groups records primarily by EventLog `am_anr` timestamp, with fallback anchors when EventLog is missing.
    3. Added type-aware strategy layer:
        - `anr_evidence/anr_strategy.py` defines `AnrTypeStrategy` and `ANR_TYPE_STRATEGIES`.
        - Current strategies: `input_dispatching_timeout`, `no_focus_window`, and `unknown`.
        - Strategy controls EventLog window, logcat window, group tolerance, EventLog tags, logcat patterns, fallback anchors, and AI prompt focus.
        - Future ANR types should be added by registering a new strategy instead of changing the orchestration flow.
    4. Added CLI support:
        - `--build-ai-context` generates AI context artifacts.
        - `--out-dir` selects artifact directory.
        - `--anr-type` optionally overrides inferred type strategy.
        - Window options default to the selected type strategy when omitted.
    5. Added tests:
        - `tests/test_log_filter.py` covers shared filtering and chunked EventLog scanning.
        - `tests/test_ai_context.py` covers grouped cache/prompt generation and unknown/future type compatibility.
- **Verification**:
    - `python3 -m unittest discover -q`
    - Result: `Ran 124 tests ... OK`
- **Forward Directive**:
    - Do not add future ANR-type-specific filtering directly into `ai_context.py` or CLI branching.
    - Add or update `AnrTypeStrategy` entries in `anr_evidence/anr_strategy.py` so each ANR type owns its own filtering and analysis defaults.

## [2026-05-01 09:05] - Implementation: Phase 1 (Filtering Layer)
- **Task**: Redesign `anr_log_pattern_filter.py` to support weighted importance and adaptive time windows.
- **Action**: 
    - Created `docs/filtering_config_spec.md` to define the new JSON-based configuration schema.
    - Preparing to refactor the Python filtering script.

## [2026-05-01 09:00] - Initialization
- **Context**: The user requested to implement the optimization suggestions discussed in the architectural review.
- **Status**: Initializing the optimization phase.
- **Plan**:
    1.  **Phase 1**: Implement "Semantic Sensitivity Weighting" in the Filtering Layer (Highest ROI).
    2.  **Phase 2**: Implement "Contextual Sniffing" in the Collection Layer.
    3.  **Phase 3**: Implement "Evidence Graph" logic in the Analysis Layer.
