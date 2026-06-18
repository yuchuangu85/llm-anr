# agent-anr 目录结构

## 根目录

| 文件 | 说明 |
|------|------|
| `CLAUDE.md` | Claude Code 项目指令文件，定义 skill 用途、命令入口、设计规则 |
| `AGENTS.md` | Codex CLI / 多 Agent 工具通用项目指令文件，与 CLAUDE.md 内容对齐 |
| `README.md` | 项目说明文档，Phase 1-8 流水线介绍、使用示例 |
| `.gitignore` | Git 忽略规则（`.omx/`, `__pycache__/`, `.DS_Store` 等） |

---

## `anr_evidence/` — 核心 Python 库

ANR 证据提取 → 标准化 → 分析 → 推理 → 修复建议 → 最终交付的完整流水线。

| 文件 | 说明 |
|------|------|
| `__init__.py` | 包初始化，统一导出所有公开 API |
| `__main__.py` | `python3 -m anr_evidence` 入口，委托 `cli.py` |
| `cli.py` | CLI 主入口，解析命令行参数，调度各 phase |
| `extractor.py` | **Phase 1** — 证据提取：从 fixture/目录/归档中解析 trace、event log、logcat、kernel log |
| `normalizer.py` | **Phase 2** — 证据标准化：对 Phase 1 产物做 provenance 追踪、锚点对齐、降级标记 |
| `analyzer.py` | **Phase 3** — 辅助分析：生成 timeline、signal summary、可疑发现（非根因裁决） |
| `reporter.py` | **Phase 4** — Markdown 报告草稿：集成 evidence summary、timeline、候选因果链 |
| `hypothesis.py` | **Phase 5** — 候选因果链草稿：输出候选链路，显式标记 `notRootCauseYet = true` |
| `root_cause.py` | **Phase 6** — 保守版根因报告：输出候选结论、支持证据、未确认项 |
| `remediation.py` | **Phase 7** — 修复建议草稿：基于候选结论生成 gated remediation drafts |
| `delivery.py` | **Phase 8** — 最终交付模板：汇总候选结论 + 因果链 + 修复建议 |
| `trace_preprocessor.py` | Trace 文本确定性结构化解析：PID、主线程、线程摘要、可疑线程、block hint |
| `log_filter.py` | EventLog 两阶段过滤算法：12s ANR 前窗口内按标签 + 包名过滤 |
| `ai_context.py` | AI 上下文构建：生成 `index.json` + 每 ANR 一个 `<group-id>/anr_analysis.md`/`logcat.txt` 工作区 |
| `anr_strategy.py` | ANR 类型策略：`No focus window`、`Input dispatching timeout` 的模板和证据要求 |
| `constants.py` | 常量定义：支持的 ANR 类型、source kind、类型模式匹配 |
| `dashboard.py` | Replay 仪表盘渲染：将 replay summary 生成可视化对比 HTML |
| `replay.py` | Replay 基准回放：批量回放 manifest、归档 session、对比 diff、构建 index |
| **AI Agent / 新增模块** | |
| `ai_agent.py` | **多 Agent AI 推理** — Provider 抽象层（Anthropic+OpenAI，零 SDK）、3 个 Sub-Agent（CPU/Memory、Stack/Lock、I/O/Binder）+ Manager Agent、迭代 Re-probe 循环 |
| `weighting.py` | **语义权重系统** — 60 个标签按重要性分 3 级（Critical/Warning/Contextual）、加权过滤 |
| `evidence_slice.py` | **证据切片 Schema (ESS)** — EvidenceSlice 结构化数据模型、JSONL 读写、按 importance/delta_t/source 过滤 |
| `entity_linker.py` | **跨源实体关联** — 提取 trace 中 PID/TID/进程名，跨 EventLog/logcat/kernel 四源关联 |
| `context_flooding.py` | **上下文防溢出** — 基于 importance 的截断策略，保证 LLM token 预算内保留最关键证据 |
| `time_norm.py` | **ΔT 时间归一化** — 逐行计算 delta_t_seconds，相对于 ANR 锚点的时间偏移 |

---

## `scripts/` — 独立入口脚本

不属于 `python3 -m anr_evidence` 覆盖范围的独立工具。

| 文件 | 说明 |
|------|------|
| `anr_preprocessor.py` | Trace 预处理器 CLI：输入 trace.txt / fixture / phase 包，输出结构化 JSON |
| `anr_log_pattern_filter.py` | EventLog 过滤器 CLI：按标签列表过滤 ANR 前 12s 窗口内的关键日志 |
| `run_replay.py` | Replay 归档执行器：运行 replay manifest 并归档结果到时间戳 session 目录 |
| `compare_replays.py` | Replay 对比工具：比较两次 replay session 的 artifact 差异 |
| `render_replay_dashboard.py` | Replay 仪表盘渲染器：将 replay summary JSON 渲染为 HTML 仪表盘 |
| `extract_bugreport.py` | Bugreport 归档解压工具：解压 .zip/.tar/.tar.gz 等格式到按文件名命名的目录 |
| `web_server.py` | 本地 Web UI：无需第三方框架，支持输入 fixture/目录/归档，查看 pipeline 产物和 AI prompt |
| `anr_to_ai.py` | **Agent 接入入口** — 一键处理 bugreport（目录/ZIP/TAR/JSON）输出 `anr_ai_context/`（index.json + 每 ANR 一个 anr_analysis.md），供 Claude Code / Codex CLI 直接读取分析 |

---

## `tests/` — 测试套件

使用 Python `unittest`。入口：

```bash
python3 -m unittest discover -s tests -v
```

### Fixture 数据

| 文件 | 说明 |
|------|------|
| `fixtures/nfw_01.json` | No Focus Window 类型 ANR 原始包（基础用例） |
| `fixtures/idt_01.json` | Input Dispatching Timeout 类型 ANR 原始包 |
| `fixtures/amb_01.json` | 类型模糊（ambiguous）的 ANR 原始包 |
| `fixtures/unk_01.json` | 未知类型的 ANR 原始包 |
| `fixtures/clock_skew_01.json` | 时钟偏移场景 |
| `fixtures/miss_kernel_01.json` | 缺少 kernel log 的场景 |
| `fixtures/miss_trace_01.json` | 缺少 trace 的场景 |
| `fixtures/noisy_01.json` | 高噪声日志场景 |

### 测试文件

| 文件 | 说明 |
|------|------|
| `__init__.py` | 测试包初始化 |
| `helpers.py` | 测试辅助函数（如 `make_temp_dir`、`make_fixture_path` 等） |
| **Phase 管道测试** | |
| `test_archive_loading.py` | Phase 1 — 归档加载（zip、tar 等） |
| `test_directory_loading.py` | Phase 1 — 目录结构加载 |
| `test_anchor_resolution.py` | Phase 1 — 锚点（am_anr）定位与解析 |
| `test_log_filter.py` | Phase 1 — EventLog 过滤器算法 |
| `test_package_status.py` | Phase 1 — 包状态（fallback、degraded） |
| `test_phase2_normalization.py` | Phase 2 — 证据标准化 |
| `test_phase3_analysis.py` | Phase 3 — 辅助分析 |
| `test_phase4_report.py` | Phase 4 — Markdown 报告草稿 |
| `test_phase5_hypothesis.py` | Phase 5 — 候选因果链 |
| `test_phase6_root_cause.py` | Phase 6 — 根因报告 |
| `test_phase7_remediation.py` | Phase 7 — 修复建议 |
| `test_phase8_delivery.py` | Phase 8 — 最终交付模板 |
| **CLI 测试** | |
| `test_cli_phase2.py` | CLI — Phase 2 参数 |
| `test_cli_phase3.py` | CLI — Phase 3 参数 |
| `test_cli_phase4.py` | CLI — Phase 4 参数 |
| `test_cli_phase5.py` | CLI — Phase 5 参数 |
| `test_cli_phase6.py` | CLI — Phase 6 参数 |
| `test_cli_phase7.py` | CLI — Phase 7 参数 |
| `test_cli_phase8.py` | CLI — Phase 8 参数 |
| **功能测试** | |
| `test_template_additive.py` | 模板可加性验证（基线模板不可被删除） |
| `test_trace_cleaning.py` | Trace 预处理正确性（block hint、线程摘要等） |
| `test_trace_deadlock.py` | Trace 锁图 / 死锁检测测试 |
| `test_trace_preprocessor_script.py` | `scripts/anr_preprocessor.py` 脚本端到端测试 |
| `test_ai_context.py` | AI 上下文构建产物测试 |
| `test_integration_fixtures.py` | 全 fixture 集成测试 |
| `test_replay_session.py` | Replay session 测试 |
| `test_replay_thresholds.py` | Replay 阈值评估测试 |
| `test_eval_groundtruth.py` | Eval ground-truth 回归（`tests/fixtures/eval/`） |
| **新增模块测试** | |
| `test_time_norm.py` | ΔT 时间归一化测试（4 项） |
| `test_weighting.py` | 语义权重测试（9 项）：标签分级、计数、过滤 |
| `test_evidence_slice.py` | 证据切片 Schema 测试（7 项）：构建、标注、JSONL 读写、过滤 |
| `test_entity_linker.py` | 跨源实体关联测试（4 项）：实体提取、EntityMap 构建、摘要 |
| `test_context_flooding.py` | 上下文防溢出测试（4 项）：截断策略、importance 过滤 |
| `test_ai_agent.py` | AI Agent 测试（12 项）：JSON 解析、Re-probe、Sub-Agent 分发、集成 mock |

---

## `docs/` — 设计文档

| 文件 | 说明 |
|------|------|
| `hermes-gemma-algorithm-design.md` | 整体算法设计文档 |
| `eventlog-trace-filter-algorithm-design.md` | EventLog 过滤算法详细设计（两阶段扫描、标签权重等） |
| `anr-intelligent-analysis-system-architecture.md` | ANR 智能分析系统总体设计、各阶段规划 |
| `event-log-tags-reference.md` | EventLog 关键标签主列表，供 `anr_log_pattern_filter.py` 使用 |
| `optimization-operation-log.md` | 操作日志：记录开发过程中的关键决策和变更 |
| `directory-structure.md` | 本文件，项目目录结构说明 |
| `architecture-implementation-gap-analysis.md` | 设计架构 vs 实现现状的逐环节差距分析 |

---

## `wiki/` — ANR 领域知识库

Android ANR 相关的参考资料、案例分析、机制原理，按主题组织。

### 根级别

| 文件 | 说明 |
|------|------|
| `README.md` | Wiki 索引 |
| `ANR基础知识.md` | ANR 基础概念：什么是 ANR、超时阈值、触发条件 |
| `ANR-分类.md` | ANR 分类体系（按触发组件、根因维度） |
| `ANR-规范.md` | ANR 分析规范与标准流程 |
| `ANR-分析流程.md` | ANR 分析的标准操作流程（SOP） |
| `ANR分析.md` | ANR 分析方法论总览 |
| `ANR监控.md` | ANR 监控方案（线上监控、数据采集） |
| `ANR-trace文件分析.md` | Trace 文件的格式解读和分析方法 |
| `ANR-trace覆盖清单.md` | Trace 覆盖项清单，对应 `anr_preprocessor.py` 的解析能力 |
| `ANR关键字.md` | ANR 相关关键字/术语表 |
| `ANR时间问题.md` | ANR 中的时间相关问题（时钟偏移、时间窗口等） |
| `ANR原理代码分析.md` | 基于 AOSP 源码的 ANR 触发机制代码分析 |
| `ANR详细对比13&10.md` | Android 13 vs Android 10 的 ANR 机制差异对比 |
| `Android ANR 系列 1 ：理解 Android ANR 设计思想.md` | ANR 设计思想系列 1 |
| `Android ANR 系列 2 ：ANR 分析套路和关键 Log 介绍.md` | ANR 分析套路系列 2 |
| `Android ANR 系列 3 ：ANR 案例分享.md` | ANR 案例分享系列 3 |
| `Find the unresponsive thread    App quality.md` | 官方文档：定位无响应线程 |
| `Diagnose and fix ANRs    App quality.md` | 官方文档：诊断和修复 ANR |

### `wiki/实例/` — ANR 案例

| 文件 | 说明 |
|------|------|
| `ANR-死锁.md` | 案例：死锁导致的 ANR |
| `ANR-内存.md` | 案例：内存问题导致的 ANR |
| `ANR-内存泄漏.md` | 案例：内存泄漏导致的 ANR |
| `ANR-应用被杀.md` | 案例：应用被 kill 导致的 ANR |
| `ANR-负载过高.md` | 案例：CPU 负载过高导致的 ANR |
| `ANR-应用超时.md` | 案例：应用组件超时 |
| `ANR-主线程超时.md` | 案例：主线程消息处理超时 |
| `ANR-Binder.md` | 案例：Binder 通信异常导致的 ANR |
| `ANR-CPU.md` | 案例：CPU 资源争抢导致的 ANR |
| `ANR-Input.md` | 案例：Input 事件处理超时 |
| `ANR-Input dispatching.md` | 案例：Input 分发超时 |
| `ANR-Locked.md` | 案例：锁竞争导致的 ANR |
| `ANR-SurfaceSyncer.md` | 案例：SurfaceSyncer 导致的 ANR |
| `ANR-Sync group timeout，failed to waitNextVsync.md` | 案例：Vsync 同步超时 |
| `ANR-Waiting for Available buffer.md` | 案例：Buffer 耗尽等待 |

### `wiki/机制/` — ANR 机制分析

| 文件 | 说明 |
|------|------|
| `README.md` | 机制目录索引 |
| `Trace产生过程.md` | ANR Trace 文件的生成过程分析 |
| `ANR-Broadcast.md` | Broadcast 超时机制分析 |
| `ANR-Broadcast2.md` | Broadcast 超时机制补充分析 |
| `ANR-ContentProvider.md` | ContentProvider 超时机制分析 |
| `ANR-HasNoFocusWindow.md` | No Focus Window 类型 ANR 机制分析 |
| `ANR-Service.md` | Service 超时机制分析 |

### `wiki/DouYin/` — 抖音 ANR 优化实践（外部参考）

| 文件 | 说明 |
|------|------|
| `抖音 ANR 自动归因平台建设实践.md` | 抖音 ANR 自动归因平台总览 |
| `1.ANR 优化实践系列 - 设计原理及影响因素.md` | 系列 1：设计原理与影响因素 |
| `2.ANR 优化实践系列 - 监控工具与分析思路.md` | 系列 2：监控工具与分析思路 |
| `3.ANR 优化实践系列 - 实例剖析集锦.md` | 系列 3：实例剖析集锦 |
| `4.ANR 优化实践系列 - Barrier 导致主线程假死.md` | 系列 4：Barrier 机制分析 |
| `5.ANR 优化实践系列 - 告别 SharedPreference 等待.md` | 系列 5：SharedPreference 优化 |

### `wiki/MTK/` — MTK 平台 SWT 分析（外部参考）

| 目录 | 说明 |
|------|------|
| `swt/` | 27 篇 MTK SWT（System Watchdog Timer）分析文档，涵盖机制介绍、分析流程、常见问题类型、Binder Stuck、Deadlock、IO Check 等 |

---

## `samples/` — 样例数据与 Replay 基准

### `samples/replay/`

| 文件 | 说明 |
|------|------|
| `manifest.json` | Replay 基准清单：定义需要批量回放的 case 列表 |
| `manifest-rule-eval.json` | Replay 规则评估清单 |

### `samples/replay/assets/` — Replay 测试素材

| 目录 | 说明 |
|------|------|
| `anr001/` | ANR 案例 001：trace 文件 + logcat 主日志 + event log |
| `bugreport_archive.zip` | Bugreport ZIP 归档样例 |
| `bugreport_dir/` | Bugreport 目录结构样例（data/anr/traces.txt + events + kernel + logcat） |
| `VINCAOSUPGRADE-5561/` | 实际 Case：12 个 ANR trace 快照 + 10 个 logcat 日志文件 |

### `samples/replay/runs/` — Replay 运行结果归档

4 个历史 replay session，每个 session 包含：
- `session.json` — 会话元数据
- `summary.json` — 回放汇总指标
- `manifest.json` — 使用的 manifest 快照
- `artifacts/` — 每个 case 的产出 JSON
- `rule-coverage.json` / `rule-coverage.md`（部分 session）— 规则覆盖报告

---

## 其他目录

| 目录 | 说明 |
|------|------|
| `filtered_anr_traces_launcher3/` | 临时输出目录：launcher3 相关的过滤后 ANR trace |
| `preprocessed_anr_results/` | 临时输出目录：预处理后的 ANR 结果 |
