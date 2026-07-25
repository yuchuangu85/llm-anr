# 设计回顾修订版 — llm-anr (2026-07-26)

> 原 2026-07-25 版本已逐项通过调用路径、测试和实际复杂度复核。本文件记录已完成优化与仍然有效的 backlog，删除未经 profile 或被当前实现反证的结论。

## 一、当前基线

- 303 个测试通过，1 个可选真实 trace asset 测试跳过。
- `python3 -m compileall -q anr_evidence tests` 通过。
- Phase 1-8、AI context workspace、Multi-Agent AI、Replay/Eval 均可用。
- 新增 archive → `scripts/anr_to_ai.py` → `index.json` / `anr_analysis.md` / `logcat.txt` 全链路测试。

## 二、本轮已完成优化

### 1. Meminfo anchor window 真正生效

此前 `window_before_seconds` / `window_after_seconds` 只写入 metadata，snapshot 选择并未使用窗口。

现在：

- 只选择 `[-before, +after]` 内的 snapshot；
- 窗口内优先选择 anchor 时刻或之前的 snapshot；
- 没有 before snapshot 时允许选择窗口内 after snapshot；
- 窗口外证据不会静默作为当前 ANR snapshot；
- 缺失时输出 `missing-meminfo-in-window`。

### 2. 时间戳解析统一

`trace_preprocessor` 已复用 `log_filter.parse_log_timestamp`，删除独立固定年份解析。规范解析器现在支持：

- `MM-DD HH:MM:SS[.fraction]`；
- `YYYY-MM-DD HH:MM:SS[.fraction]`；
- 1-9 位小数精度；
- 输入显式年份优先于 fallback 年份。

无年份日志仍使用当前运行年份作为 fallback；若后续需要分析跨年拼接日志，应进一步从 bugreport 元数据传入 year context。

### 3. EventLog 过滤路径安全收敛

新增 `filter_known_anchor_window`：

- AI context 传入已经解析好的 group anchor 与 line index；
- 不会退回第一个 `am_anr`；
- 与 standalone first-anchor filter 共享底层窗口/标签匹配逻辑；
- 保持多 ANR EventLog 窗口隔离。

原建议“让 `_event_window` 直接调用 `filter_preceding_anchor_window`”已废弃，因为后者的合同是发现第一个 anchor，会破坏 multi-ANR。

### 4. Phase pipeline 集中

新增 `anr_evidence.pipeline`：

- `payload_phase(payload)` 统一 phase 识别；
- `run_until(payload, target_phase)` 统一 forward-only phase 推进；
- CLI 与 Replay 复用同一实现；
- CLI 请求 Phase 2/3 时不再提前计算 Phase 5/6/7；
- Reporter 继续保留接受 Phase 3/5/6/7 的公开适配合同。

### 5. Context flooding 合同修正

- `preserve_anchor_lines` 现在实际生效；
- protected critical/anchor 超预算时在 `_global.budgetOverflow` 中显式报告；
- 全局裁剪后重新计算 per-source stats，避免统计与结果不一致；
- Trace 已是预处理后的紧凑直接阻塞证据，在 ESS 中标记为 critical，不会因没有 EventLog tag 被错误删除；
- `AgentConfig.truncation` 允许调用方配置 Multi-Agent evidence budget。

Artifact 路径继续把过滤后的 logcat 放在独立 `logcat.txt`，而不是重新内联进 `anr_analysis.md`。不默认裁剪 baseline artifact；若未来需要硬 token 上限，应采用“完整外部证据 + compact LLM view”双层合同。

### 6. 无效依赖与测试缺口

- 删除 `ai_agent.py` 中未使用的 `_build_groups` / `_strategy_summary` / `_resolve_options` 私有导入；
- 新增显式年份/精度、known-anchor multi-ANR、Meminfo window、anchor preservation、phase lazy execution 回归测试；
- 新增合成 archive 到 AI artifact 的端到端测试。

## 三、仍然有效的 Backlog

### P1

#### 1. 脱敏 real-world golden bugreport

当前已有合成 ZIP/TAR/目录和全链路测试，但仓库仍缺少可稳定签入 CI 的脱敏真实 bugreport corpus。应覆盖：

- 多 ANR；
- vendor timestamp/log shard 格式；
- 完整 AnrManager dump；
- meminfo；
- archive/directory → loader → grouping/filtering → artifact golden output。

#### 2. Cross-source fusion entity scope

现有 fusion 已有单元、完整 pipeline 和多 group cache-isolation 测试。真正剩余风险是 corroboration regex 可能命中同一窗口内无关进程的信号。后续应把 package/PID/TID/entity 关联纳入 promotion 条件。

### P2

#### 3. Agent 指引文档生成

`CLAUDE.md`、`GEMINI.md`、`CODEBUDDY.md`、`HERMES.md` 存在共享内容和漂移。优先采用生成脚本或工具原生 include；不默认使用符号链接，以避免客户端和 Windows checkout 兼容问题。

#### 4. CLI 测试 helper 抽取

Phase 2-8 CLI 测试有重复 subprocess / 临时文件脚手架。优先抽取 helper，而不是强行把所有测试合并为一个参数化基类。

### P3 / 仅在有证据时处理

#### 5. 大文件拆分

`ai_context.py` 与 `trace_preprocessor.py` 可按稳定边界拆分，但只有出现变更热点、ownership 冲突或测试隔离收益时再做，不以行数作为唯一依据。

#### 6. `dedupe_dicts` 性能

当前 `json.dumps(sort_keys=True)` 主要用于小规模 warning 去重，没有百万级热路径证据。保留实现，只有 profile 显示为瓶颈时再优化。

## 四、已删除的错误结论

- `filter_preceding_anchor_window` 当前为 O(n)，不是 O(n²)。
- Trace section 时间戳失败会进入最差时间桶并稳定排序，不是 rank=0 或随机选择。
- AI context 的 AnrManager 抽取没有文档所称的两条冲突路径。
- Cross-source fusion 已有完整测试闭环；trace-only 不融合跨源证据是职责边界。
- `ai_agent` 对三个私有函数只是 stale import，不是运行时依赖。
- Meminfo CLI/API 默认值原本就是一致的；真正问题是窗口参数未生效，现已修复。

## 五、结论

当前下一步不应再优先做原文的“EventLog first-anchor 直接替换”或不存在的 O(n²) 优化。有效优先级是：

1. 脱敏 real-world golden E2E；
2. Fusion entity-aware promotion；
3. Agent 文档生成与 CLI test helper；
4. 基于 profile/变更数据决定模块拆分和去重性能优化。
