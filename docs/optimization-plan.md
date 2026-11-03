# 项目优化方案与修复记录

> 生成日期：2026-06-10
> 背景：对全项目（约 19,000 行 Python，281 个测试）做了一次架构 / 性能 / 文档一致性评审，
> 按 P0/P1/P2 分级后直接落地修复。本文记录已完成的修改、未完成项的设计方案与后续待办。

---

## 一、已完成的修复

### P0-1 文档一致性（已完成）

**问题**：`AGENTS.md` 等多份智能体指引文档仍描述旧版产物（顶层 `cache.md`/`ai_prompt.md`/`summary.json`），
而代码实际产出 `index.json` + 每 ANR 一个 `<group-id>/anr_analysis.md`；最终综合分析槽位标题文档写
`#### AI Analysis — Final ANR`，代码实际是 `#### AI Analysis — 最终 ANR 综合分析`（`ai_context.py:1360/1412`）。
按错误文档执行的智能体会读不存在的文件、把综合结论写到不存在的槽位。

**修改**：

| 文件 | 修改 |
|------|------|
| `AGENTS.md` | 步骤 1 产物清单改为 `index.json` + `<group-id>/anr_analysis.md` + `logcat.txt`；步骤 2 槽位标题改为 `#### AI Analysis — 最终 ANR 综合分析`；删除硬编码测试数（171）；修正多智能体示例中 `provider_config` 未定义的代码错误 |
| `CLAUDE.md` / `GEMINI.md` / `CODEBUDDY.md` / `HERMES.md` | 同步槽位标题、测试数、示例代码三处修复 |
| `anr_evidence/cli.py` | `--build-ai-context` 帮助文案从“cache.md 和 ai_prompt.md”改为“每个 ANR 的 anr_analysis.md 工作区以及 index.json” |
| `docs/directory-structure.md` | `ai_context.py`/`anr_to_ai.py` 条目改为新产物描述；删除硬编码测试数（165）；删除不存在的测试文件条目（`test_cli_dashboard.py`、`test_cli_replay*.py`、`test_dashboard.py`、`test_replay.py`、`test_replay_compare.py`、`test_replay_index.py`），补充实际存在的 `test_trace_deadlock.py`、`test_eval_groundtruth.py` |
| `docs/anr-analysis-flow.md` | 0.3 节产物清单改为新格式 |
| `docs/architecture-implementation-gap-analysis.md` | 顶部加"已过时（历史快照）"声明 — 文中标记"缺失"的多智能体、语义权重、ESS、实体关联、ΔT、上下文防溢出均已在 `ai_agent.py`/`weighting.py`/`evidence_slice.py`/`entity_linker.py`/`time_norm.py`/`context_flooding.py` 落地 |
| `scripts/web_server.py` | 界面中 `cache.md`/`ai_prompt.md` 标签改为"证据缓存"/"分析指令"（实际渲染的是内存中的 Markdown，不落盘） |

### P0-2 合并 build_evidence/window_summary 双份实现（已完成）

**问题**：`anr_evidence/extraction/common.py` 与 `anr_evidence/sources/shared/evidence.py` 存在逐字重复的
`build_evidence()` 和 `window_summary()`，证据字典的模式有两个事实来源，改其一不改其二会静默分叉。

**修改**：`extraction/common.py` 删除本地实现，改为从 `..sources.shared.evidence` 重新导出（保留 `__all__`
兼容既有 `from .common import build_evidence` 调用）。`sources/shared/evidence.py` 成为唯一实现。
依赖方向安全：`sources/` 不依赖 `extraction/`，无循环导入。

### P1-1 LlmClient 生产可用性（已完成）

**问题**：`anr_evidence/ai_agent.py` 的 `LlmClient` 直接 `urllib.request.urlopen(req)`，无超时（网络挂起则
整个智能体循环永久阻塞）、无重试（429/5xx 直接失败）、无结构化错误。

**修改**：
- `ProviderConfig` 新增 `timeout_seconds: float = 120.0` 与 `max_retries: int = 2` 字段；
- 新增 `LlmRequestError(RuntimeError)` 异常；
- 新增 `LlmClient._post_json()`：带 `timeout`，对 HTTP 429/500/502/503/504 与 `URLError`/`TimeoutError`
  做指数退避重试（1s/2s/4s，封顶 8s），重试耗尽后抛 `LlmRequestError` 并携带原始异常链；
- `_complete_anthropic` / `_complete_openai` 统一走 `_post_json`；
- `tests/test_ai_agent.py` 的模拟 `urlopen` 增加 `timeout=None` 形参以匹配新调用。

### P1-2 `_EST_CHARS_PER_TOKEN` 注释矛盾（已完成）

**问题**：`anr_evidence/context_flooding.py:15` 注释写 "~8 chars per token"（每令牌约 8 个字符）但常量值是 4，且未解释
`max_tokens * 4 // 80` 的换算逻辑。

**修改**：注释改为说明真实换算：每令牌约 4 字符、每行约 80 字符，令牌预算 → 行预算。

### P2-1 打包与 CI（已完成）

**问题**：项目无 `pyproject.toml`、无 CI，测试只能依赖开发者自觉手动跑。

**修改**：
- 新增 `pyproject.toml`：仅依赖标准库的零依赖包声明、`anr-evidence` 控制台脚本指向 `anr_evidence.cli:main`；
- 新增 `.github/workflows/ci.yml`：推送/拉取请求触发，Python 3.12/3.13 矩阵，跑 `compileall` + `unittest discover`。

---

## 二、已完成：多 ANR 场景性能优化（P0-3）

> 状态：已落地。此前计划中记录的本地编辑器钩子阻塞已解除，本轮已按方案修改
> `ai_context.py`、`trace_preprocessor.py` 与 `log_filter.py`，并保持既有调用兼容。

**问题**：一个错误报告含 N 个 ANR 锚点时，`ai_context._build_groups()`（`ai_context.py:242-401`）
每组都从头做一遍：

1. `_trace_context()` → `preprocess_trace_content()` 全量堆栈解析（分片段、提线程、建锁图、压缩）；
2. `_timestamped_logcat_context_before()`（`ai_context.py:452-470`）对完整 logcat 逐行 `splitlines()` + `parse_log_timestamp()`；
3. `sources["kernel_log"].splitlines()`（`ai_context.py:365`）每组重复分行。

堆栈/logcat 在 N 组之间完全相同，复杂度 O(N × 全文件)。

**修改**：

1. **堆栈预处理缓存**（`trace_preprocessor.py`）：
   - `preprocess_trace_content()` 增加可选形参 `cache: dict | None = None`（默认 None，所有既有调用零影响）；
   - 锚点只影响“选哪个片段”（`_trace_section_rank`），主要处理与锚点无关，所以缓存结构为：
     - `cache["content"]`：身份校验，内容变更即 `cache.clear()`；
     - `cache["prepared"] = (lines, sections, stats)`：行拆分、`split_trace_sections` 和每片段静态排序统计只做一次；
     - `cache[("result", selected_index, max_lines)]`：完整预处理结果，按选中片段复用；
   - 新增 `_section_static_stats()` / `_rank_from_stats()`，避免每个锚点对每个片段重复扫描信号、主线程与时间戳；
   - 安全性：`fuse_cross_source_evidence` 是只读的（返回新列表，`cross_source_fusion.py` 模块文档明确承诺），
     `_build_groups` 对 `trace["traceHints"]` 是赋值而非原地改写，缓存结果跨组共享安全；
     文档字符串标注“调用者必须将缓存结果视为只读”。
   - `_trace_context()` 对缓存结果中的 `traceHints` / `deadlockHints` / `lockGraph` / `compactedLines`
     做每组独立拷贝，避免后续每 ANR 融合逻辑误把组内证据写回共享缓存。

2. **`_build_groups` 持有缓存**（`ai_context.py`）：
   - 循环外创建 `trace_preprocess_cache: dict = {}`，传给 `_trace_context(source, anchor_dt, cache=...)`；
   - 循环外预计算 `kernel_log_lines = sources.get("kernel_log", {}).get("content", "").splitlines()`；
   - 循环外通过 `prepare_timestamped_lines()` 预解析 logcat，并传给
     `filter_prepared_timestamp_window()`；普通 `filter_timestamp_window(content, ...)` 仍自行从内容
     解析，避免原内容与预解析行两个来源静默不一致；
   - 循环外生成 `parsed_logcat = [(ts, l.strip()) for ts, l in prepared_logcat if ts is not None]`，
     `_timestamped_logcat_context_before` 改为接收 `parsed_logcat` 直接按时间窗筛选。

3. **时间戳解析热路径优化**（`log_filter.py`）：
   - `parse_log_timestamp()` 从 `datetime.strptime()` 改为基于已匹配正则的手动字段解析；
   - 新增 `prepare_timestamped_lines()` + `filter_prepared_timestamp_window()`，供多 ANR 场景一次性预解析日志；
   - `filter_timestamp_window()` 保持仅接收原内容的调用契约，不接受外部预解析行。

**预期收益**：N 个 ANR 的错误报告中，堆栈解析从 N 次降为“不同片段数”次（通常 1），
logcat 时间戳解析从 N 次全量降为 1 次。大型错误报告（堆栈数 MB、logcat 数十 MB）下 `anr_to_ai.py`
耗时近似除以 N。

**验证**：全量 `unittest`（重点 `test_ai_context`、`test_trace_*`）+ 多 ANR 测试样本产物一致性检查。

---

## 三、后续待办（2026-07-26 复核后）

原列表中的阶段运行器、已知锚点 EventLog、时间戳统一、过期私有导入、Meminfo 时间窗口和
上下文溢出统计问题已经完成。原 O(n²)、随机堆栈片段和 AnrManager 双路径判断被代码反证，已删除。
详细证据见 `docs/design-review-2026-07-25.md` 修订版。

### P1

1. **脱敏真实基准错误报告**：当前已有合成 ZIP/TAR/目录与归档 → `anr_to_ai.py` → 产物
   全链路测试，但仍需可签入 CI 的脱敏真实多 ANR 语料。
2. **跨源融合的实体作用域**：置信度提升正则应绑定包名/PID/TID/实体，避免同窗口无关进程
   信号提升目标提示的置信度。

### P2

3. **智能体指引文档生成**：优先生成脚本或工具原生包含机制，避免符号链接/Windows 检出兼容风险。
4. **命令行测试辅助函数**：先提取子进程、临时阶段数据包辅助函数，不强制合并所有阶段测试文件。

### P3 / 有性能剖析或变更热点证据时处理

5. **大文件拆分**：依据变更热点、所有权边界和测试隔离收益决定，不按行数直接拆分。
6. **`dedupe_dicts` 性能**：当前仅用于小规模警告去重；只有性能剖析证明为瓶颈时再改变键语义。

---

## 四、验证结果

- `python3 -m unittest discover -s tests`：**303 个测试全部通过（1 项跳过）**；
- `python3 -m compileall -q anr_evidence tests scripts`：通过；
- 多 ANR 测试样本在堆栈/logcat 缓存开启与显式禁用缓存两种路径下，`groups`、`cache_markdown`、
  `ai_prompt_markdown` 对比一致；
- 新增回归测试覆盖：两个 ANR 复用同一个缓存堆栈片段时，只有命中 logcat 旁证的 ANR 会把
  `MAIN_BINDER_WAIT_REPLY` 从 `strong` 提升到 `critical`，旁证不会串到另一组；
- 新增 LLM 客户端单元测试覆盖：HTTP 5xx/429 重试、HTTP 4xx 不重试、URLError 重试耗尽，以及
  `urlopen(..., timeout=...)` 传参；
- 本次未修改任何证据模式字段、未删除任何基线证据来源，符合
  “基线提取是硬性保证”的设计规则。
