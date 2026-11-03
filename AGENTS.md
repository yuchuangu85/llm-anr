<a id="agent-anr--anr-evidence-extraction-and-analysis"></a>

# agent-anr：ANR 证据提取与分析

由智能体驱动的 Android ANR（应用无响应）分析流水线：证据提取 → 规范化 → 辅助分析 → 假设 → 根因报告 → 修复草案 → 最终交付。

<a id="anr-analysis-workflow"></a>

## ANR 分析工作流

当用户请求分析 ANR 错误报告（提供目录、ZIP、TAR 或 JSON 测试样例）时，遵循以下工作流：

<a id="step-1-generate-ai-context"></a>

### 第 1 步：生成 AI 上下文

```bash
python3 scripts/anr_to_ai.py <path_to_bugreport> [--package <pkg>] [--anr-type <type>]
# 等价入口：
python3 -m anr_evidence --build-ai-context <path_to_bugreport> [--package <pkg>] [--anr-type <type>]
```

这会生成 `anr_ai_context/`，其中包含：
- `index.json`：链接到所有 ANR 分组的目录索引
- `<group-id>/anr_analysis.md`：AI 指令 + 筛选后的证据 + 文内分析槽位（每个 ANR 一份文件）
- `<group-id>/logcat.txt`：由 `anr_analysis.md` 引用的完整筛选后 logcat 日志
- `<group-id>/anr_reconstruction.md` 和 `anr_reconstruction.json`：由证据驱动的独立过程重建，默认从日志生成，即使尚未填写 AI 槽位也会生成

`index.json` 通过 `artifactPaths.reconstruction` 和 `artifactPaths.reconstructionJson` 提供各分组的重建文件路径。每个 ANR 保留一份独立的 `anr_reconstruction.md`，内容仅限关键时间线里程碑、简短证据摘录、未经验证的候选解释摘要，以及重要缺口或警告；不得合并分组，也不得在此文档中重复完整日志和 AI 槽位内容。JSON 保留所有已观测事件、每个事件的时间及时间差（ΔT）、身份标识、证据引用、关联关系和 AI 原文。`anr_analysis.md` 的四个槽位及 `logcat.txt` 保持不变。重建内容区分 `observed`（日志观测）、`inferred`（机制推断或 AI 假设）和 `unknown`（证据缺口）。时间顺序不等于因果关系；不得虚构未观测到的 5 秒区间，也不得将转储快照视为持续阻塞的证明。这是证据重建，不是真实执行回放；现有回放命令仍用于回归重跑。详情参见 `docs/anr-ai-analysis-guide.md`。

（旧版运行会生成顶层 `cache.md`/`ai_prompt.md`/`summary.json`；这些产物已废弃，重新生成时会自动删除。）

<a id="step-2-read-and-analyze-the-evidence"></a>

### 第 2 步：阅读并分析证据

阅读 `anr_ai_context/<group-id>/anr_analysis.md`，并按其中的指令进行分析；文件会明确说明输出结构（时间线、阻塞点、候选根因链、证据质量评估、修复建议）。
将各证据来源的结论直接写入同一文件的 `#### AI Analysis — <source>` 槽位。
最终综合报告必须放在 `#### AI Analysis — 最终 ANR 综合分析` 槽位下（例如以 `## 综合分析结论` 开始，依次包含时间线、直接阻塞点、候选根因链、证据质量、修复建议和末尾的 JSON）；不得仅将综合结论留在聊天中。

填写全部四个 AI 槽位后，交付前须刷新各分组的独立重建：

```bash
python3 scripts/reconstruct_anr.py <context-root|group-directory|anr_analysis.md>
```

传入上下文根目录会处理其中的所有分组；传入分组目录或分析文件则只处理一个分组。此刷新不会重新提取证据或改写 AI 槽位。可选参数 `--ai-report <JSON>` 仅将外部 AI 报告作为未经验证的候选解释加入：须核验其引用，不得将报告本身提升为已观测事实。

<a id="step-3-output-the-analysis-report"></a>

### 第 3 步：输出分析报告

输出结构化 Markdown 报告，涵盖：
1. 来自堆栈转储、EventLog、AnrManager 和 logcat 的关键事件时间线
2. 直接阻塞点及其支撑证据
3. 按置信度排序的候选根因链
4. 证据质量评估（缺口、矛盾）
5. 修复建议

最终交付时，除 `anr_analysis.md` 外，还须提供各分组刷新后的 `anr_reconstruction.md` 和 `anr_reconstruction.json` 路径。

<a id="input-dispatch-timeout-anr-specialization"></a>

## 输入分发超时 ANR 专项分析

当用户请求输入分发超时分析或指定 `input_dispatching_timeout`，或同一 ANR 出现已发布但仍在等待 FINISHED 的事件、连接中尚未清空的 waitQueue，或通道的原因为 `is not responding. Waited ...ms for ...` 时，加载并遵循[输入分发超时技能](skills/input-dispatch-timeout-anr-analysis/SKILL.md)。对于泛化的 `Input dispatching timed out`，须先分类；纯粹的无焦点窗口等待属于下方的独立技能，不属于此 ACK 超时机制。

在基线提取后以增量方式使用该技能：关联连接 token 与原生 seq、每个条目的投递与截止时间、finish 与 FINISHED 及清理过程、实际到期条目与原因中所指条目的区别，以及剩余队列的恢复情况。Java finish 返回、handled=false 或单个 ACK 均不能证明连接已恢复。填写现有四个分析槽位，保留保守标志，并在交付前刷新独立重建。只有满足手势技能自身的 Launcher/Recents 触发条件时才加载该技能。

<a id="no-focused-window-anr-specialization"></a>

## 无焦点窗口 ANR 专项分析

当用户明确请求无焦点窗口分析或指定 `no_focus_window`，或同一 ANR 的原因或日志中出现 `does not have a focused window`、`no focused window`、`no focus window`、`no window has focus`，或已有焦点应用但依赖焦点的事件仍在等待窗口时，须在解释证据前加载并遵循[无焦点窗口技能](skills/no-focused-window-anr-analysis/SKILL.md)。

在基线提取后以增量方式使用该技能：区分焦点应用与实际原生窗口焦点，重建目标查找、配置的超时、到期复查和重置过程，并核验 WMS 归因及恢复情况。仅有泛化的输入超时、焦点变化或已投递事件的 ACK 超时，均不足以确定属于此分支。保留全部基线证据并填写现有四个来源分析及最终分析槽位，将综合结论写在 `#### AI Analysis — 最终 ANR 综合分析` 下。

如果同一 ANR 也满足下方的 Launcher/Quickstep/Recents 触发条件，则须同时加载两个技能：用无焦点窗口分析处理焦点等待机制，用手势分析处理上游 Recents 或转场链路。任一触发条件本身都不能证明根因。

<a id="launcher--quickstep-recents-animation-anr-specialization"></a>

## Launcher / Quickstep 最近任务动画 ANR 专项分析

满足以下任一条件时，须在解释证据前完整加载并遵循 [`skills/gesture-anr-analysis/SKILL.md`](skills/gesture-anr-analysis/SKILL.md)：

1. 用户明确请求分析 Launcher/Quickstep 手势导航或最近任务动画 ANR。
2. 证据同时包含：
   - 输入或焦点锚点，例如 `Input dispatching timed out`、`does not have a focused window`，或目标窗口虽已绘制但仍处于 `NOT_VISIBLE` 状态；以及
   - Launcher/Quickstep Recents 信号，例如 `recents_animation_input_consumer`、`OtherActivityInputConsumer`、`AbsSwipeUpHandler`、`startRecentsAnimation`、`onTasksAppeared`、`finishRecentsAnimation` 或 `mRecentsAnimationStartPending`。

对于没有手势或 Recents 信号的泛化输入超时或无焦点 ANR，不要加载此专项技能。触发时，应在基线提取工作流之后将其作为增量专项分析使用：保留全部基线证据，将 `am_anr` 与手势身份标识、Recents 生命周期、结束状态及窗口或焦点证据关联，再将各来源分析和最终结论写回生成的 `anr_analysis.md` 槽位。

<a id="quick-reference-anr-type-strategies"></a>

### 速查：ANR 类型策略

| 类型 | 关键信号 |
|------|-------------|
| `input_dispatching_timeout` | InputDispatcher 超时、无焦点窗口、主线程阻塞于 Binder/同步栅栏/锁 |
| `no_focus_window` | 窗口失去焦点、Activity 尚未恢复、表面尚未就绪 |
| `unknown` | 更宽的时间窗口（30 秒）、通用信号模式 |

<a id="if-the-user-provides-no-package-name"></a>

### 用户未提供包名时

运行时不传入 `--package`，并从证据中推断包名（`AnrManager` 块或 `am_anr` 行中会包含包名）。

<a id="project-layout"></a>

## 项目结构

```
anr_evidence/          # Python 核心库（命令行入口：anr_evidence.cli / -m anr_evidence）
scripts/               # 独立入口脚本
tests/                 # 单元测试 + 集成测试（可通过 unittest 发现）
docs/                  # 算法设计文档 + 差距分析
wiki/                  # ANR 领域参考资料
```

<a id="key-commands"></a>

## 常用命令

```bash
# === 主要工作流：生成 AI 上下文 ===
python3 scripts/anr_to_ai.py <input> [--package <pkg>] [--anr-type <type>]

# === 确定性流水线（第 1 至 8 阶段） ===
python3 -m anr_evidence tests/fixtures/nfw_01.json
python3 -m anr_evidence --analyze tests/fixtures/nfw_01.json
python3 -m anr_evidence --report tests/fixtures/nfw_01.json
python3 -m anr_evidence --deliver tests/fixtures/nfw_01.json

# === 多智能体 AI 分析（需要 API 密钥） ===
python3 -c "
from anr_evidence import run_ai_agent_analysis, ProviderConfig, ProviderKind, AgentConfig
from anr_evidence import load_package_from_fixture
package = load_package_from_fixture('tests/fixtures/nfw_01.json')
provider_config = ProviderConfig(kind=ProviderKind.ANTHROPIC, model='claude-sonnet-4-20250514')
result = run_ai_agent_analysis(
    package,
    provider_config=provider_config,
    agent_config=AgentConfig(provider=provider_config, max_iterations=3, verbose=True),
)
# result.integrated_report 包含 candidateChains/candidateConclusions/remediationDrafts
"

# === 独立脚本 ===
python3 scripts/anr_preprocessor.py tests/fixtures/nfw_01.json
python3 scripts/anr_log_pattern_filter.py path/to/events.txt --tags docs/event-log-tags-reference.md
python3 scripts/extract_bugreport.py path/to/bugreport.zip -o output_dir/
python3 scripts/compare_replays.py path/to/run_a path/to/run_b
python3 scripts/web_server.py --port 8080

# === 回放 ===
python3 -m anr_evidence --replay samples/replay/manifest.json --replay-out /tmp/anr-replay
python3 scripts/run_replay.py samples/replay/manifest.json --out-root samples/replay/runs --label nightly
```

<a id="api--use-from-python-within-agent-session"></a>

## API：在 Python 中使用（智能体会话内）

```python
from anr_evidence import (
    # AI 上下文
    build_ai_context, AiContextOptions, AiContextResult,
    # 数据包加载
    load_package_from_archive, load_package_from_directory, load_package_from_fixture,
    # 多智能体 AI
    run_ai_agent_analysis, ProviderConfig, ProviderKind, AgentConfig,
    # 证据切片模式
    EvidenceSlice, build_evidence_slices, annotate_slices_with_tags,
    # 实体关联
    EntityMap, build_entity_map, entity_summary_for_ai,
    # 权重
    ImportanceLevel, EVENT_LOG_TAG_WEIGHTS, filter_by_importance,
    # 防止上下文过载
    TruncationConfig, truncate_evidence,
    # 时间规范化
    TimeNormalizedLine, compute_delta_t,
)
```

<a id="design-rules"></a>

## 设计规则

- 基线提取是硬性保障：不得移除基线证据
- 类型模板仅作增量补充：不得删除证据来源
- ANR 类型未知或不明确时，安全回退到基线提取
- 保留关键证据的优先级高于减少噪声
- 所有分析输出均须保持保守：`finalJudgment = false`、`notRootCauseYet = true`、`requiresHumanConfirmation = true`
- AnrManager 行始终为 CRITICAL 级别，因为其中包含 ANR 诊断摘要
- 将 AnrManager 块用作 logcat 的次级锚点，并与 event_log 的 am_anr 时间戳交叉核对

<a id="test"></a>

## 测试

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q anr_evidence tests
```
