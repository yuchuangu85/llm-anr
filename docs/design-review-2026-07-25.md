# 设计回顾修订版 — llm-anr (2026-07-26)

> 原 2026-07-25 版本已逐项通过调用路径、测试和实际复杂度复核。本文件记录已完成优化与仍然有效的待办事项，删除未经性能剖析或被当前实现反证的结论。

## 一、当前基线

- 303 个测试通过，1 个可选真实 trace 资源测试跳过。
- `python3 -m compileall -q anr_evidence tests` 通过。
- 阶段 1-8、AI 上下文工作区、多智能体 AI、回放/评测均可用。
- 新增归档 → `scripts/anr_to_ai.py` → `index.json` / `anr_analysis.md` / `logcat.txt` 全链路测试。

## 二、本轮已完成优化

<a id="1-meminfo-anchor-window-真正生效"></a>
### 1. Meminfo 锚点窗口真正生效

此前 `window_before_seconds` / `window_after_seconds` 只写入元数据，快照选择并未使用窗口。

现在：

- 只选择 `[-before, +after]` 内的快照；
- 窗口内优先选择锚点时刻或之前的快照；
- 没有锚点之前的快照时允许选择窗口内锚点之后的快照；
- 窗口外证据不会静默作为当前 ANR 快照；
- 缺失时输出 `missing-meminfo-in-window`。

### 2. 时间戳解析统一

`trace_preprocessor` 已复用 `log_filter.parse_log_timestamp`，删除独立固定年份解析。规范解析器现在支持：

- `MM-DD HH:MM:SS[.fraction]`；
- `YYYY-MM-DD HH:MM:SS[.fraction]`；
- 1-9 位小数精度；
- 输入显式年份优先于回退年份。

无年份日志仍使用当前运行年份作为回退值；若后续需要分析跨年拼接日志，应进一步从 bugreport 元数据传入年份上下文。

### 3. EventLog 过滤路径安全收敛

新增 `filter_known_anchor_window`：

- AI 上下文传入已经解析好的分组锚点与行索引；
- 不会退回第一个 `am_anr`；
- 与独立的首锚点过滤器共享底层窗口/标签匹配逻辑；
- 保持多 ANR EventLog 窗口隔离。

原建议“让 `_event_window` 直接调用 `filter_preceding_anchor_window`”已废弃，因为后者的合同是发现第一个锚点，会破坏多 ANR 隔离。

<a id="4-phase-pipeline-集中"></a>
### 4. 阶段流水线集中

新增 `anr_evidence.pipeline`：

- `payload_phase(payload)` 统一阶段识别；
- `run_until(payload, target_phase)` 统一仅向前的阶段推进；
- CLI 与回放复用同一实现；
- CLI 请求阶段 2/3 时不再提前计算阶段 5/6/7；
- 报告生成器继续保留接受阶段 3/5/6/7 的公开适配合同。

<a id="5-context-flooding-合同修正"></a>
### 5. 上下文泛滥防护合同修正

- `preserve_anchor_lines` 现在实际生效；
- 受保护的关键证据/锚点超预算时在 `_global.budgetOverflow` 中显式报告；
- 全局裁剪后重新计算各证据源统计，避免统计与结果不一致；
- Trace 已是预处理后的紧凑直接阻塞证据，在 ESS 中标记为 `critical`，不会因没有 EventLog 标签被错误删除；
- `AgentConfig.truncation` 允许调用方配置多智能体证据预算。

产物生成路径继续把过滤后的 logcat 放在独立 `logcat.txt`，而不是重新内联进 `anr_analysis.md`。不默认裁剪基线产物；若未来需要硬 token 上限，应采用“完整外部证据 + 紧凑 LLM 视图”双层合同。

### 6. 无效依赖与测试缺口

- 删除 `ai_agent.py` 中未使用的 `_build_groups` / `_strategy_summary` / `_resolve_options` 私有导入；
- 新增显式年份/精度、已知锚点的多 ANR 隔离、Meminfo 窗口、锚点保留、阶段惰性执行回归测试；
- 新增合成归档到 AI 产物的端到端测试。

<a id="三仍然有效的-backlog"></a>
## 三、仍然有效的待办事项

### P1

<a id="1-脱敏-real-world-golden-bugreport"></a>
#### 1. 脱敏的真实 bugreport 黄金样本

当前已有合成 ZIP/TAR/目录和全链路测试，但仓库仍缺少可稳定签入 CI 的脱敏真实 bugreport 语料。应覆盖：

- 多 ANR；
- 厂商时间戳/日志分片格式；
- 完整 AnrManager 转储；
- meminfo；
- 归档/目录 → 加载器 → 分组/过滤 → 产物黄金基准输出。

<a id="2-cross-source-fusion-entity-scope"></a>
#### 2. 跨源融合的实体范围

现有融合已有单元、完整流水线和多分组缓存隔离测试。真正剩余风险是旁证正则表达式可能命中同一窗口内无关进程的信号。后续应把包名/PID/TID/实体关联纳入置信度提升条件。

### P2

<a id="3-agent-指引文档生成"></a>
#### 3. 智能体指引文档生成

`CLAUDE.md`、`GEMINI.md`、`CODEBUDDY.md`、`HERMES.md` 存在共享内容和漂移。优先采用生成脚本或工具原生包含机制；不默认使用符号链接，以避免客户端和 Windows 检出兼容问题。

<a id="4-cli-测试-helper-抽取"></a>
#### 4. CLI 测试辅助函数抽取

阶段 2-8 CLI 测试有重复的子进程/临时文件脚手架。优先抽取辅助函数，而不是强行把所有测试合并为一个参数化基类。

### P3 / 仅在有证据时处理

#### 5. 大文件拆分

`ai_context.py` 与 `trace_preprocessor.py` 可按稳定边界拆分，但只有出现变更热点、归属冲突或测试隔离收益时再做，不以行数作为唯一依据。

#### 6. `dedupe_dicts` 性能

当前 `json.dumps(sort_keys=True)` 主要用于小规模警告去重，没有百万级热路径证据。保留实现，只有性能剖析显示为瓶颈时再优化。

## 四、已删除的错误结论

- `filter_preceding_anchor_window` 当前为 O(n)，不是 O(n²)。
- Trace 分段时间戳失败会进入最差时间桶并稳定排序，不是 `rank=0` 或随机选择。
- AI 上下文的 AnrManager 抽取没有文档所称的两条冲突路径。
- 跨源融合已有完整测试闭环；仅分析 trace 时不融合跨源证据是职责边界。
- `ai_agent` 对三个私有函数只是过时导入，不是运行时依赖。
- Meminfo CLI/API 默认值原本就是一致的；真正问题是窗口参数未生效，现已修复。

## 五、结论

当前下一步不应再优先做原文的“EventLog 首锚点过滤器直接替换”或不存在的 O(n²) 优化。有效优先级是：

1. 脱敏真实黄金样本的端到端测试；
2. 融合时基于实体关联提升置信度；
3. 智能体文档生成与 CLI 测试辅助函数；
4. 基于性能剖析/变更数据决定模块拆分和去重性能优化。
