# 项目优化方案与修复记录

> 生成日期：2026-06-10
> 背景：对全项目（约 19,000 行 Python，281 个测试）做了一次架构 / 性能 / 文档一致性评审，
> 按 P0/P1/P2 分级后直接落地修复。本文记录已完成的修改、未完成项的设计方案与后续 backlog。

---

## 一、已完成的修复

### P0-1 文档一致性（已完成）

**问题**：`AGENTS.md` 等多份 agent 指引文档仍描述旧版产物（顶层 `cache.md`/`ai_prompt.md`/`summary.json`），
而代码实际产出 `index.json` + 每 ANR 一个 `<group-id>/anr_analysis.md`；最终综合分析槽位标题文档写
`#### AI Analysis — Final ANR`，代码实际是 `#### AI Analysis — 最终 ANR 综合分析`（`ai_context.py:1360/1412`）。
按错误文档执行的 agent 会读不存在的文件、把综合结论写到不存在的槽位。

**修改**：

| 文件 | 修改 |
|------|------|
| `AGENTS.md` | Step 1 产物清单改为 `index.json` + `<group-id>/anr_analysis.md` + `logcat.txt`；Step 2 槽位标题改为 `#### AI Analysis — 最终 ANR 综合分析`；删除硬编码测试数（171）；修正 Multi-Agent 示例中 `provider_config` 未定义的代码错误 |
| `CLAUDE.md` / `GEMINI.md` / `CODEBUDDY.md` / `HERMES.md` | 同步槽位标题、测试数、示例代码三处修复 |
| `anr_evidence/cli.py` | `--build-ai-context` help 文案从 "cache.md and ai_prompt.md" 改为 "per-ANR anr_analysis.md workspaces plus an index.json" |
| `docs/directory-structure.md` | `ai_context.py`/`anr_to_ai.py` 条目改为新产物描述；删除硬编码测试数（165）；删除不存在的测试文件条目（`test_cli_dashboard.py`、`test_cli_replay*.py`、`test_dashboard.py`、`test_replay.py`、`test_replay_compare.py`、`test_replay_index.py`），补充实际存在的 `test_trace_deadlock.py`、`test_eval_groundtruth.py` |
| `docs/anr-analysis-flow.md` | 0.3 节产物清单改为新格式 |
| `docs/architecture-implementation-gap-analysis.md` | 顶部加"已过时（历史快照）"声明 — 文中标记"缺失"的 Multi-Agent、语义权重、ESS、实体关联、ΔT、Context Flooding 均已在 `ai_agent.py`/`weighting.py`/`evidence_slice.py`/`entity_linker.py`/`time_norm.py`/`context_flooding.py` 落地 |
| `scripts/web_server.py` | UI 中 `cache.md`/`ai_prompt.md` 标签改为"证据缓存"/"分析指令"（实际渲染的是内存中的 markdown，不落盘） |

### P0-2 合并 build_evidence/window_summary 双份实现（已完成）

**问题**：`anr_evidence/extraction/common.py` 与 `anr_evidence/sources/shared/evidence.py` 存在逐字重复的
`build_evidence()` 和 `window_summary()`，证据 dict 的 schema 有两个事实来源，改其一不改其二会静默分叉。

**修改**：`extraction/common.py` 删除本地实现，改为从 `..sources.shared.evidence` re-export（保留 `__all__`
兼容既有 `from .common import build_evidence` 调用）。`sources/shared/evidence.py` 成为唯一实现。
依赖方向安全：`sources/` 不依赖 `extraction/`，无循环导入。

### P1-1 LlmClient 生产可用性（已完成）

**问题**：`anr_evidence/ai_agent.py` 的 `LlmClient` 直接 `urllib.request.urlopen(req)`，无超时（网络挂起则
整个 agent loop 永久阻塞）、无重试（429/5xx 直接炸）、无结构化错误。

**修改**：
- `ProviderConfig` 新增 `timeout_seconds: float = 120.0` 与 `max_retries: int = 2` 字段；
- 新增 `LlmRequestError(RuntimeError)` 异常；
- 新增 `LlmClient._post_json()`：带 `timeout`，对 HTTP 429/500/502/503/504 与 `URLError`/`TimeoutError`
  做指数退避重试（1s/2s/4s，封顶 8s），重试耗尽后抛 `LlmRequestError` 并携带原始异常链；
- `_complete_anthropic` / `_complete_openai` 统一走 `_post_json`；
- `tests/test_ai_agent.py` 的 mock `urlopen` 增加 `timeout=None` 形参以匹配新调用。

### P1-2 `_EST_CHARS_PER_TOKEN` 注释矛盾（已完成）

**问题**：`anr_evidence/context_flooding.py:15` 注释写 "~8 chars per token" 但常量值是 4，且未解释
`max_tokens * 4 // 80` 的换算逻辑。

**修改**：注释改为说明真实换算：每 token 约 4 字符、每行约 80 字符，token 预算 → 行预算。

### P2-1 打包与 CI（已完成）

**问题**：项目无 `pyproject.toml`、无 CI，测试只能依赖开发者自觉手动跑。

**修改**：
- 新增 `pyproject.toml`：stdlib-only 零依赖包声明、`anr-evidence` console script 指向 `anr_evidence.cli:main`；
- 新增 `.github/workflows/ci.yml`：push/PR 触发，Python 3.12/3.13 矩阵，跑 `compileall` + `unittest discover`。

---

## 二、已完成：多 ANR 场景性能优化（P0-3）

> 状态：已落地。此前计划中记录的本地编辑器 hook 阻塞已解除，本轮已按方案修改
> `ai_context.py`、`trace_preprocessor.py` 与 `log_filter.py`，并保持既有调用兼容。

**问题**：一个 bugreport 含 N 个 ANR 锚点时，`ai_context._build_groups()`（`ai_context.py:242-401`）
每组都从头做一遍：

1. `_trace_context()` → `preprocess_trace_content()` 全量 trace 解析（分 section、提线程、建锁图、压缩）；
2. `_timestamped_logcat_context_before()`（`ai_context.py:452-470`）对完整 logcat 逐行 `splitlines()` + `parse_log_timestamp()`；
3. `sources["kernel_log"].splitlines()`（`ai_context.py:365`）每组重复分行。

trace/logcat 在 N 组之间完全相同，复杂度 O(N × 全文件)。

**修改**：

1. **trace 预处理缓存**（`trace_preprocessor.py`）：
   - `preprocess_trace_content()` 增加可选形参 `cache: dict | None = None`（默认 None，所有既有调用零影响）；
   - anchor 只影响"选哪个 section"（`_trace_section_rank`），重活与 anchor 无关，所以缓存结构为：
     - `cache["content"]`：身份校验，content 变更即 `cache.clear()`；
     - `cache["prepared"] = (lines, sections, stats)`：splitlines + `split_trace_sections` + 每 section 静态 ranking stats 只做一次；
     - `cache[("result", selected_index, max_lines)]`：完整预处理结果，按选中 section 复用；
   - 新增 `_section_static_stats()` / `_rank_from_stats()`，避免每个 anchor 对每个 section 重复扫描 signal/main/timestamp；
   - 安全性：`fuse_cross_source_evidence` 是只读的（返回新列表，`cross_source_fusion.py` 模块文档明确承诺），
     `_build_groups` 对 `trace["traceHints"]` 是赋值而非原地改写，缓存结果跨组共享安全；
     docstring 标注 "callers must treat cached results as read-only"。
   - `_trace_context()` 对 cached result 中的 `traceHints` / `deadlockHints` / `lockGraph` / `compactedLines`
     做每组独立拷贝，避免后续每 ANR 融合逻辑误把组内证据写回共享缓存。

2. **`_build_groups` 持有缓存**（`ai_context.py`）：
   - 循环外创建 `trace_preprocess_cache: dict = {}`，传给 `_trace_context(source, anchor_dt, cache=...)`；
   - 循环外预计算 `kernel_log_lines = sources.get("kernel_log", {}).get("content", "").splitlines()`；
   - 循环外通过 `prepare_timestamped_lines()` 预解析 logcat，并传给
     `filter_prepared_timestamp_window()`；普通 `filter_timestamp_window(content, ...)` 仍自行从 content
     解析，避免 content 与 prepared lines 两个来源静默不一致；
   - 循环外生成 `parsed_logcat = [(ts, l.strip()) for ts, l in prepared_logcat if ts is not None]`，
     `_timestamped_logcat_context_before` 改为接收 `parsed_logcat` 直接按时间窗筛选。

3. **timestamp 解析热路径优化**（`log_filter.py`）：
   - `parse_log_timestamp()` 从 `datetime.strptime()` 改为基于已匹配正则的手动字段解析；
   - 新增 `prepare_timestamped_lines()` + `filter_prepared_timestamp_window()`，供多 ANR 场景一次性预解析日志；
   - `filter_timestamp_window()` 保持 content-only 调用契约，不接受外部 prepared lines。

**预期收益**：N 个 ANR 的 bugreport，trace 解析从 N 次降为"distinct section 数"次（通常 1），
logcat 时间戳解析从 N 次全量降为 1 次。大 bugreport（trace 数 MB、logcat 数十 MB）下 `anr_to_ai.py`
耗时近似除以 N。

**验证**：全量 `unittest`（重点 `test_ai_context`、`test_trace_*`）+ 多 ANR fixture 产物一致性检查。

---

## 三、后续 backlog（按优先级）

### P1（建议下一轮处理）

1. **主流程接入 token 防洪**：`scripts/anr_to_ai.py` → `build_ai_context_artifacts` 不经过
   `context_flooding.truncate_evidence`（目前只有 `run_ai_agent_analysis` 用它）。超大 bugreport 会生成
   超出 LLM 上下文的 `anr_analysis.md`。方案：`AiContextOptions` 增加可选 `truncation: TruncationConfig`，
   渲染前按 importance 截断各证据段并在文档中标注 "N 行因预算被截断"。
2. **过滤路径收敛**：`ai_context._event_window`（`ai_context.py:589-612`）与 `log_filter` 的窗口过滤逻辑
   平行实现，EventLog 过滤规则改动需要改两处。方案：`_event_window` 改为调用
   `log_filter.filter_preceding_anchor_window` 后做 anchor 行补插。
3. **管道编排去重**：`cli._transform_payload`（`cli.py:91-201`）、`replay._payload_phase`（`replay.py:433-444`）、
   `reporter._coerce_report_input`（`reporter.py:278-286`）各自维护 "phase 级联补跑" 逻辑。方案：抽一个
   `pipeline.py::run_until(payload, target_phase)`，三处共用；同时把 `reporter` 中隐式补跑上游 phase 的行为
   显式化（Phase 4 渲染器不应静默执行 Phase 5/6/7）。
4. **agent 指引文档去重**：`CLAUDE.md`/`GEMINI.md`/`CODEBUDDY.md`/`HERMES.md` 内容几乎相同（本次已同步修复，
   但长期会再次漂移）。方案：保留 `AGENTS.md` 为唯一事实源，其余改为符号链接或一行引用。

### P2（机会性处理）

5. **大文件拆分**：`ai_context.py`（~1500 行）按 分组/渲染/选项 拆为子模块；`trace_preprocessor.py`
   （~1671 行）按 解析/锁图/摘要 拆分。注意：拆分也受上述大文件编辑 hook 故障影响。
6. **`ai_agent` 不再依赖 `ai_context` 私有函数**：`ai_agent.py:23` 导入 `_build_groups`/`_strategy_summary`/
   `_resolve_options`，应将这三个提升为公开 API 或提供公共封装。
7. **测试模板参数化**：`tests/test_cli_phase2..8.py` 七个文件结构高度重复（共 533 行），可用
   `unittest` 参数化基类合并为一个文件。
8. **时间戳解析统一**：`log_filter.parse_log_timestamp` 是规范实现，但 `normalizer._extract_timestamp`、
   `trace_preprocessor._parse_timestamp`（硬编码年份）各有一份，应统一到 `log_filter`。
9. **`extraction/common.dedupe_dicts` 性能**：用 `json.dumps(sort_keys=True)` 做 dict 去重键，证据量大时低效，
   可改为基于稳定字段（id/sourceKind/content hash）的元组键。
10. **`filter_preceding_anchor_window` O(n²)**：`log_filter.py:285-316` 对每个锚点重扫全量 EventLog，
    超大 EventLog + 多锚点时退化，可先按时间排序后用二分定位窗口边界。

---

## 四、验证结果

- `python3 -m unittest discover -s tests`：**281 个测试全部通过（1 skipped）**；
- `python3 -m compileall -q anr_evidence tests scripts`：通过；
- 多 ANR fixture 在 trace/logcat 缓存开启与显式禁用缓存两种路径下，`groups`、`cache_markdown`、
  `ai_prompt_markdown` 对比一致；
- 新增回归测试覆盖：两个 ANR 复用同一个 cached trace section 时，只有命中 logcat 旁证的 ANR 会把
  `MAIN_BINDER_WAIT_REPLY` 从 `strong` 提升到 `critical`，旁证不会串到另一组；
- 新增 LLM client 单元测试覆盖：HTTP 5xx/429 retry、HTTP 4xx 不重试、URLError 重试耗尽，以及
  `urlopen(..., timeout=...)` 传参；
- 本次未修改任何 evidence schema 字段、未删除任何 baseline 证据来源，符合
  "Baseline extraction is the hard guarantee" 设计规则。
