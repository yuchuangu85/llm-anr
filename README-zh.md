# agent-anr

> [English](README.md)

`agent-anr` 是一个 Agent 驱动的 Android ANR（Application Not Responding）证据抽取与 AI 辅助分析工具链。
它的核心目标不是直接“武断定责”，而是把 trace、EventLog、logcat、AnrManager、meminfo、kernel 等原始材料转成可审计的证据包，再通过固定步骤生成保守的候选根因报告。

## 核心能力

- 从 fixture JSON、bugreport 目录、ZIP/TAR 归档中自动发现 ANR 证据源。
- 按 ANR anchor 独立分组；同一日志中多个 ANR 会生成多个独立上下文，避免证据串台。
- 高召回过滤：trace、EventLog `am_anr` 前窗口、logcat 触发窗口、AnrManager dump flow、meminfo follow-up、kernel log。
- Trace 结构化解析：main thread、线程状态、schedstat、锁等待、Binder wait、Render/GPU wait、Deadlock Detection、Trace Hints。
- 保守分析流水线：normalize → analyze → hypothesize → root-cause → remediate → deliver。
- AI-ready `anr_analysis.md`：固定四段专项分析槽位，便于人工/LLM 按步骤填写。

## 通过 AI Agent 交互式使用

cd 到 agent-anr 目录下，启动你的 AI coding agent（Claude Code / Codex CLI / Hermes / 等），然后输入自然语言指令：

```text
分析 <log目录路径> 目录下包名为 com.example.app 的 ANR 原因
```

Agent 会自动完成以下步骤：

1. 运行 `python3 scripts/anr_to_ai.py <路径> --package com.example.app` 生成 AI 分析上下文
2. 读取并按四阶段分析所有证据（Trace → EventLog → Logcat/AnrManager → Final ANR）
3. 将综合分析写回 `anr_ai_context/<anr-id>/anr_analysis.md`
4. 输出最终结构化报告：时间线、直接阻塞点、候选根因链、证据质量评估、修复建议

## 快速开始：生成 AI 分析上下文

推荐入口：

```bash
python3 scripts/anr_to_ai.py <bugreport_dir_or_archive_or_fixture> \
  [--package <package.name>] \
  [--anr-type no_focus_window|input_dispatching_timeout]
```

输出目录默认是 `anr_ai_context/`：

- 顶层 `index.json` — 目录索引，链接到所有 ANR 分组
- 每个 ANR 独立目录：`anr_ai_context/<anr-id>/anr_analysis.md`

`anr_analysis.md` 是唯一的人工/AI 工作区文件：AI 指令、过滤后的证据、内联分析槽位全在一个文件中，方便回源核对。

## 四阶段 AI 分析工作流

`anr_analysis.md` 固定包含四个分析槽位，必须按顺序填写：

1. `anr-trace-analysis` → `#### AI Analysis — Trace`
2. `anr-eventlog-analysis` → `#### AI Analysis — EventLog`
3. `anr-logcat-analysis` → `#### AI Analysis — Logcat/AnrManager`
4. `anr-analysis` → `#### AI Analysis — Final ANR`

约束：

- 前三段只做 source-specific analysis，不提前下最终根因。
- Final ANR 只能在前三段完成后做跨源整合。
- 综合分析必须写回同一个 `anr_analysis.md` 的 `#### AI Analysis — Final ANR` 槽位；建议先写 `## 综合分析结论`，再写 Timeline、Direct blocking point、Candidate root-cause chains、Evidence quality、Remediation suggestions 和 JSON tail。不要只在聊天回复中输出最终综合结论。
- 所有最终输出保持保守字段：
  - `finalJudgment = false`
  - `notRootCauseYet = true`
  - `requiresHumanConfirmation = true`

## 支持的 ANR 类型策略

| ANR type | 重点信号 |
|---|---|
| `input_dispatching_timeout` | InputDispatcher timeout、Slow dispatch、main thread Binder/lock/IO/render wait、CPU/IO pressure |
| `no_focus_window` | focus/window/surface 生命周期、Activity resume、relayout、finishDrawing、no focused window |
| `unknown` | 使用 baseline 安全回退，保留关键证据，不删除来源 |

> 类型模板是 additive-only：未知或不确定时只增加保守 baseline，不会删除关键证据。

## Deterministic pipeline

除 AI context 外，也可以运行 deterministic Phase 1-8 流水线：

```bash
# Phase 1: evidence extraction
python3 -m anr_evidence tests/fixtures/nfw_01.json

# Phase 2: normalization
python3 -m anr_evidence --normalize tests/fixtures/nfw_01.json

# Phase 3: assisted analysis, still non-final
python3 -m anr_evidence --analyze tests/fixtures/nfw_01.json

# Candidate causal chains
python3 -m anr_evidence --hypothesize tests/fixtures/nfw_01.json

# Conservative root-cause report v1
python3 -m anr_evidence --root-cause tests/fixtures/nfw_01.json

# Gated remediation drafts
python3 -m anr_evidence --remediate tests/fixtures/nfw_01.json

# Final delivery markdown template
python3 -m anr_evidence --deliver tests/fixtures/nfw_01.json
```

这些阶段都遵循保守原则：输出候选链、支持证据、缺口和人工确认要求，不把单一快照当最终定责。

## Source-specific filters

可单独运行某类日志过滤器，便于调试或复用：

```bash
python3 scripts/anr_trace_filter.py <trace_file_or_package>
python3 scripts/anr_event_log_filter.py <event_log_file>
python3 scripts/anr_logcat_filter.py <logcat_file>
python3 scripts/anr_meminfo_filter.py <meminfo_file>
python3 scripts/anr_filter_workflow.py <bugreport_dir_or_archive_or_fixture>
```

Trace 预处理入口：

```bash
python3 scripts/anr_preprocessor.py tests/fixtures/nfw_01.json
```

## 输入格式

CLI 支持：

- fixture JSON：`tests/fixtures/*.json`
- bugreport 目录
- 已解压日志目录
- `.zip`
- `.tar`
- `.tar.gz` / `.tgz`
- `.tar.bz2`
- `.tar.xz`

目录加载器会识别常见厂商/系统路径，例如：

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
print(result.ai_prompt_markdown[:1000])
```

Multi-agent AI 分析入口（需要配置 provider/API key）：

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
tests/          单元测试、集成测试、回归测试
docs/           设计笔记、操作日志与规划文档
skills/         ANR 领域知识与 AI 分析技能文档
```

## 设计原则

- Baseline extraction 是硬保证：关键证据优先保留。
- Type templates 只做加法：不同 ANR 类型只能增加关注点，不能删除 baseline 来源。
- Unknown / ambiguous ANR type 安全回退到 baseline。
- AnrManager lines 永远是 CRITICAL：它包含系统 ANR diagnostic summary。
- 多 ANR 输入必须按 anchor 独立拆分，禁止跨 ANR 复用 AnrManager/meminfo/trace/logcat 证据。
- 直接阻塞点不等于最终根因；没有上游证据时只输出候选链。
- 所有自动/AI 分析输出默认需要人工确认。

## 验证

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q anr_evidence tests scripts
```

当前主测试规模约 270+ tests。

## 常见用法

### 只想快速生成可给 AI 的分析材料

```bash
python3 scripts/anr_to_ai.py bugreport.zip --package com.example.app
```

然后打开：

```text
anr_ai_context/<anr-id>/anr_analysis.md
```

### 没有 package name

可以省略 `--package`：工具会优先从 `am_anr`、AnrManager block、trace `Cmd line` 中推断。

```bash
python3 scripts/anr_to_ai.py bugreport.zip
```

### 多个 ANR 混在一份日志里

读取 `anr_ai_context/index.json`，逐个分析每个 `<anr-id>/anr_analysis.md`。

### 只做确定性报告，不使用 AI

```bash
python3 -m anr_evidence --deliver tests/fixtures/nfw_01.json > delivery.md
```


