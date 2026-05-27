# agent-anr

> [English](README.md)

`agent-anr` 是一个 Agent 驱动的 Android ANR（Application Not Responding）证据抽取与 AI 辅助分析工具链。它会把 trace、EventLog、logcat、AnrManager dump、meminfo、kernel log 等原始材料转成按 ANR 独立隔离、可审计的证据工作区，再引导 AI/人工分析者按来源逐段完成保守分析。

项目目标是 **高召回、可审计、不过早定责**：触发类型、根因模式提示和修复建议都只是有证据支撑的候选结论，最终仍需要人工确认。

## 当前能力

### 证据抽取与路由

- 支持加载 fixture JSON、已解压日志目录、bugreport 目录、ZIP 归档和各种 TAR 归档。
- 自动发现常见厂商/系统目录，包括 `data/anr/`、`FS/data/anr/`、`System_log/`、`event-log/`、`logs/`、`android-logs/`、`dropbox/`、`meminfo.txt`、`last_kmsg`、`lastkmsg`、`console_ramoops`。
- 有目标包名时优先使用包名匹配的 `am_anr` 作为主 anchor；严格包名匹配，避免无关 ANR 被当成当前分析锚点。
- EventLog anchor 缺失时，会保留 trace 推断出的 ANR 时间，避免生成无时间信息的 fallback 目录。
- 同一份输入中存在多个 ANR 时，会拆成多个独立 `anr-*` 工作区；AnrManager、meminfo、trace、EventLog、logcat、kernel 证据禁止跨 anchor 串用。
- Baseline evidence 是硬保证：类型策略和根因 hints 只能增加关注点、窗口和关键词，不能删除关键证据来源。

### 分来源分析支持

- **Trace**：main thread 状态、schedstat、Java/ART/Linux 状态映射、锁等待、owner/peer 线程、Binder wait、render/fence wait、native poll 解释、死锁检测、Trace hints。
- **EventLog**：以 `am_anr` 为 anchor 的 ANR 前窗口、AM/WM/Input/进程/内存 tag 保留、ΔT 解读，以及“只对 anchor 做包名过滤、不误删系统上下文行”的过滤策略。
- **Logcat / AnrManager**：完整提取 AnrManager dump flow，包括 `startAnrDump`、stack dump、`ANR in`、Reason、Load、PSI、CPU windows、`TOTAL`、Top CPU processes、DropBox、dump completion 等关键行。
- **Meminfo**：作为 AnrManager 后置跟进证据，按 ANR anchor 选择最近 snapshot，关联目标包和高负载进程的 PSS/RSS。
- **Kernel log**：作为 baseline 上下文保留 scheduler、Binder、hung task、low memory、OOM/LMK、pressure 等信号。
- **上下文控制**：过滤后的 logcat 单独写入每个 ANR 目录的 `logcat.txt`，不再全部内联进主分析文件，降低上下文膨胀，同时保留可审计证据。

### 分类与候选根因提示

`triggerType` 和 `rootCausePatternHints[]` 是两个独立层次：

- `triggerType` 表示 ANR 触发机制。
- `rootCausePatternHints[]` 表示候选根因模式，只用于丰富分析，不覆盖触发类型，也不等于最终根因。

已支持的触发类型策略：

| Trigger type | 分析重点 |
|---|---|
| `input_dispatching_timeout` | InputDispatcher timeout、Slow dispatch、焦点窗口状态、main thread Binder/lock/IO/render wait、CPU/IO pressure |
| `no_focus_window` | focus/window/surface 生命周期、Activity resume、relayout、finishDrawing、no focused window |
| `broadcast_timeout` | BroadcastQueue timeout、`Broadcast of Intent`、receiver `onReceive`、`goAsync`、`finish()` |
| `service_timeout` | `Timeout executing service`、foreground service start、service lifecycle、冷启动 |
| `content_provider_timeout` | provider publish timeout、provider not responding、provider 冷启动、query/Binder wait |
| `job_scheduler_timeout` | JobService `onStartJob` / `onStopJob`、JobScheduler 调度、service lifecycle |
| `system_watchdog_swt` | system_server Watchdog/SWT、被监控 Handler、system lock、Binder thread |
| `unknown` | 未知/未来 ANR 类型的安全 fallback；保留 baseline 证据和已命中的 hints |

已支持的根因模式 hints：

| Hint | 含义 |
|---|---|
| `deadlock` | 死锁、自锁、锁 owner 阻塞链、long monitor/mutex contention |
| `memory_leak_oom_pressure` | 内存泄漏/内存膨胀/OOM/LMK/PSI memory pressure 候选 |
| `high_load_anr` | 高 CPU、IO wait、Load、调度压力、目标进程或系统进程过载候选 |

## 通过 AI Agent 交互式使用

在仓库根目录启动你的 AI coding agent（Claude Code / Codex CLI / Hermes / 等），然后输入自然语言指令：

```text
分析 <log目录路径> 目录下包名为 com.example.app 的 ANR 原因
```

Agent 应完成以下步骤：

1. 运行 `python3 scripts/anr_to_ai.py <路径> --package com.example.app` 生成 `anr_ai_context/`。
2. 打开 `anr_ai_context/index.json`，按索引逐个分析每个 ANR 工作区。
3. 按顺序填写 `anr_ai_context/<anr-id>/anr_analysis.md` 中的四个分析槽位：Trace → EventLog → Logcat/AnrManager → Final ANR。
4. Logcat/AnrManager 槽位引用 `logcat.txt` 时，必须读取同目录下的 `logcat.txt`。
5. 回复前必须把综合结论写回 `Final ANR` 槽位。
6. 最终输出结构化报告：时间线、直接阻塞点、候选根因链排序、证据质量、修复建议。

## 快速开始：生成 AI 分析工作区

推荐入口：

```bash
python3 scripts/anr_to_ai.py <bugreport_dir_or_archive_or_fixture> \
  [--package <package.name>] \
  [--anr-type input_dispatching_timeout|no_focus_window|broadcast_timeout|service_timeout|content_provider_timeout|job_scheduler_timeout|system_watchdog_swt]
```

默认输出目录：

```text
anr_ai_context/
  index.json
  anr-<timestamp-or-anchor>/
    anr_analysis.md
    logcat.txt
```

说明：

- `index.json` 是所有 ANR group 的权威索引。
- `anr_analysis.md` 是人工/AI 工作区：分析指令、过滤证据摘要、分来源分析槽位和最终综合槽位都在同一个文件中。
- `logcat.txt` 保存当前单个 ANR group 的过滤后 logcat 行。
- 顶层旧式 `cache.md`、`ai_prompt.md`、`summary.json` 不再是主流程产物；生成 artifact 时会清理陈旧旧文件。

## 四阶段 AI 分析契约

每个 `anr_analysis.md` 固定包含四个分析槽位，必须按顺序填写：

1. `anr-trace-analysis` → Trace-only analysis。
2. `anr-eventlog-analysis` → EventLog / anchor-only analysis。
3. `anr-logcat-analysis` → Logcat + AnrManager + meminfo follow-up analysis。
4. `anr-analysis` → Final cross-source ANR synthesis。

约束：

- 前三段只做 source-specific analysis，不能提前下最终根因。
- Final ANR 只能在 Trace、EventLog、Logcat/AnrManager 三段完成后整合。
- 综合结论必须写回同一个 `anr_analysis.md`，不要只在聊天中输出。
- Final Markdown 应包含：综合分析结论、时间线、Trace 证据分析、EventLog 证据分析、Logcat/AnrManager 证据分析、直接阻塞点、候选根因链、证据质量、修复建议，以及 fenced JSON tail。
- JSON 尾部默认保留保守字段：
  - `finalJudgment = false`
  - `notRootCauseYet = true`
  - `requiresHumanConfirmation = true`

## Deterministic Phase 1-8 流水线

不使用 AI Agent 时，也可以运行确定性证据流水线：

```bash
# Phase 1: evidence extraction
python3 -m anr_evidence tests/fixtures/nfw_01.json

# Phase 2: normalization
python3 -m anr_evidence --normalize tests/fixtures/nfw_01.json

# Phase 3: assisted non-final analysis
python3 -m anr_evidence --analyze tests/fixtures/nfw_01.json

# Phase 4/5: candidate causal chains
python3 -m anr_evidence --hypothesize tests/fixtures/nfw_01.json

# Phase 6: conservative root-cause candidate report
python3 -m anr_evidence --root-cause tests/fixtures/nfw_01.json

# Phase 7: gated remediation drafts
python3 -m anr_evidence --remediate tests/fixtures/nfw_01.json

# Phase 8: final delivery markdown template
python3 -m anr_evidence --deliver tests/fixtures/nfw_01.json
```

所有确定性阶段都保持候选语义：输出证据、缺口和 hypotheses，不做不可逆最终定责。

## 分来源 CLI 工具

单独过滤某类日志时，可使用以下工具做调试、复用或校验：

```bash
python3 scripts/anr_trace_filter.py <trace_file_or_package>
python3 scripts/anr_event_log_filter.py <event_log_file>
python3 scripts/anr_logcat_filter.py <logcat_file>
python3 scripts/anr_meminfo_filter.py <meminfo_or_bugreport_dir> --package <package.name>
python3 scripts/anr_filter_workflow.py <bugreport_dir_or_archive_or_fixture>
```

Trace 预处理：

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

内存中构建 AI context：

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
tests/          单元测试、集成测试、回归测试、fixture 与 eval 测试
docs/           设计笔记、操作日志与规划文档
skills/         ANR 领域知识与分来源 AI 分析技能
```

## 设计原则

- **Baseline extraction 是硬保证**：关键证据始终优先保留。
- **Type strategies 只做加法**：ANR 类型只能增加窗口、关键词、anchor 和分析重点，不能删除 baseline 来源。
- **Trigger type 与 root-cause hints 分层**：hints 只能丰富分析，不能单独成为最终根因。
- **Unknown / ambiguous ANR type** 安全回退到 baseline，同时保留已命中的候选 hints。
- **EventLog `am_anr` 是主 anchor**；严格包名匹配用于避免跨 ANR 污染。
- **AnrManager lines 永远是 CRITICAL**：必须保留完整 dump flow，不能只保留连续行或窄窗口行。
- **Meminfo 可选但必须 anchor-aware**；存在时应紧跟 AnrManager 负载归因使用。
- **直接阻塞点不等于最终根因**；上游诱因必须有跨源证据支持。
- **所有自动/AI 分析输出默认需要人工确认。**

## 验证

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q anr_evidence tests scripts
```

当前测试规模：281 tests（最近一次本地运行 skipped=1）。

## 常见用法

### 快速生成 AI-ready 材料

```bash
python3 scripts/anr_to_ai.py bugreport.zip --package com.example.app
```

然后打开：

```text
anr_ai_context/index.json
anr_ai_context/<anr-id>/anr_analysis.md
anr_ai_context/<anr-id>/logcat.txt
```

### 没有 package name

可以省略 `--package`：工具会从 `am_anr`、AnrManager block、trace `Cmd line` 或安全 fallback 证据中推断 anchor。

```bash
python3 scripts/anr_to_ai.py bugreport.zip
```

### 一份日志里有多个 ANR

先读取 `anr_ai_context/index.json`，再逐个分析每个 `<anr-id>/anr_analysis.md`。除非证据明确互相关联，否则不要跨 ANR group 合并结论。

### 只做确定性报告，不使用 AI

```bash
python3 -m anr_evidence --deliver tests/fixtures/nfw_01.json > delivery.md
```
