<a id="完整流程分析设计架构-vs-实现现状"></a>
# 完整流程分析：设计架构与实现现状对比

> **⚠️ 本文档已过时（历史快照）**：文中标记为"缺失"的能力多数已实现 —
> 多智能体推理（`ai_agent.py`）、语义权重（`weighting.py`）、证据切片模式（`evidence_slice.py`）、
> 跨源实体关联（`entity_linker.py`）、ΔT 时间归一化（`time_norm.py`）、上下文泛滥防护（`context_flooding.py`）均已落地。
> AI 上下文产物也已从 `cache.md`/`ai_prompt.md`/`summary.json` 迁移为 `index.json` + 每 ANR 一个 `<group-id>/anr_analysis.md` 工作区。
> 当前状态请以 `README.md` 和 `docs/development-guide.md` 为准；本文仅保留作为早期差距分析的存档。

## 一、设计架构回顾

根据 `docs/anr-intelligent-analysis-system-architecture.md` 定义的四层架构：

```
原始数据 → 预处理层 → 过滤层 → 推理层 → 输出层
(原始) → (预处理) → (过滤) → (推理) → (输出)
```

期望的完整数据流：

```
bugreport 归档
  ↓ 解压
Trace / EventLog / Logcat / 内核日志原始文本
  ↓ 预处理：时间对齐(ΔT) + 实体关联(PID/TID/UID) + 汇总摘要
结构化证据切片 (JSONL, 带 importance 权重)
  ↓ 过滤：自适应窗口 + 语义加权(级别 1/2/3)
高密度证据切片
  ↓ AI 推理：管理智能体 → 子智能体(CPU/内存) + 子智能体(堆栈/锁) + 子智能体(IO/Binder)
  ↓ 迭代重新探查
根因证据图 → Markdown 报告
```

---

## 二、实现现状逐环节对照

### 环节 1：解压 ✅ 完整

| 能力 | 文件 | 状态 |
|------|------|------|
| 从目录加载 bugreport | `extractor.py:load_package_from_directory()` | ✅ |
| 从 ZIP 归档加载 | `extractor.py:load_package_from_archive()` (zip) | ✅ |
| 从 TAR 归档加载 | `extractor.py:load_package_from_archive()` (tar/gz/bz2/xz) | ✅ |
| 从 JSON 测试夹具加载 | `extractor.py:load_package_from_fixture()` | ✅ |
| 独立解压脚本 | `scripts/extract_bugreport.py` | ✅ |
| 自动识别源文件命名 | `extractor.py:KNOWN_SOURCE_FILENAMES` + `SOURCE_PATH_PATTERNS` | ✅ |

**结论：解压环节完整，支持目录、ZIP、TAR 等多种输入格式。**

---

### 环节 2：过滤（时间窗口 + 标签匹配）✅ 核心完整，⚠️ 缺语义权重

| 能力 | 文件 | 状态 |
|------|------|------|
| 锚点定位 (am_anr) | `log_filter.py:filter_file_preceding_anchor_window()` | ✅ |
| 大文件双阶段扫描 | `log_filter.py` — 正向流式 + 倒序块扫描 | ✅ |
| 时间窗口过滤 | `log_filter.py:filter_timestamp_window()` | ✅ |
| 标签匹配过滤 | `log_filter.py` — `DEFAULT_EVENT_LOG_TAGS` + 策略定制 | ✅ |
| 自适应窗口（按 ANR 类型） | `anr_strategy.py:AnrTypeStrategy` — 不同类型不同窗口 | ✅ |
| **语义权重分级** | — | ❌ **缺失** |
| **结构化证据切片** | — | ❌ **缺失** |

**详细说明：**

设计文档要求的三级权重系统：
- 级别 1（关键）: `am_anr`, `am_crash` — 直接触发
- 级别 2（警告）: `am_kill`, `binder_transaction_timeout` — 潜在原因
- 级别 3（上下文）: `battery_level`, `wm_task_moved` — 环境背景

当前实现：所有标签平权，`DEFAULT_EVENT_LOG_TAGS` 是一个扁平的 `frozenset`，没有优先级信息。

```python
# 当前实现（log_filter.py）
DEFAULT_EVENT_LOG_TAGS = frozenset({
    "am_anr", "am_proc_died", "am_proc_bad", ...
    "battery_level", "battery_status", ...
    "wm_task_to_front", "wm_task_moved", ...
})  # ← 所有标签权重相同

# 设计期望
# tag → importance: "CRITICAL" | "WARNING" | "CONTEXTUAL"
```

---

### 环节 3：预处理（结构化解析）⚠️ 部分完整

| 能力 | 文件 | 状态 |
|------|------|------|
| Trace 结构化解析 | `trace_preprocessor.py` — PID、主线程、线程状态、阻塞提示 | ✅ |
| Binder 等待链分析 | `trace_preprocessor.py:_build_binder_summary()` | ✅ |
| 渲染等待链分析 | `trace_preprocessor.py:_build_render_summary()` | ✅ |
| 挂起分析 | `trace_preprocessor.py:_build_suspend_summary()` | ✅ |
| CPU 调度分析 | `trace_preprocessor.py:_build_cpu_summary()` | ✅ |
| 可疑线程筛选 | `trace_preprocessor.py:_select_suspicious_threads()` | ✅ |
| **时间对齐（ΔT 归一化）** | `trace_preprocessor.py` — 仅 trace 部分 | ⚠️ **部分** |
| **跨源实体关联（PID/TID/UID 映射）** | — | ❌ **缺失** |
| **汇总摘要（搜索统计 + 实体映射）** | `analyzer.py:_build_signal_summary()` | ⚠️ **部分** |

**详细说明：**

**ΔT 归一化缺失**：设计文档要求所有日志行带上 `delta_t_seconds` 字段（相对于 T_anr 的秒偏移）。当前 EventLog 和 Logcat 输出的仍然是原始行，没有逐行计算时间偏移：

```python
# 设计期望的 Evidence Slice 格式
{"source": "eventlog", "delta_t_seconds": -12.0, "tag": "am_proc_died", "importance": "CRITICAL"}

# 当前实际输出（ai_context.py:_event_window）
# 返回的是原始文本行列表，无结构
["04-12 10:00:03.100 I am_proc_died: [0,1234,com.example.app,...]", ...]
```

**跨源实体关联缺失**：设计文档原文要求 "A TID found in trace is explicitly mapped to its parent PID and Package Name"（即“将 trace 中发现的 TID 显式映射到其所属 PID 和包名”）。当前 trace 解析在 trace 内部完成了 PID/进程名关联，但没有跨源（如把 trace 中的 TID 与 logcat 中的 TID 行关联）。

---

<a id="环节-4确定性分析phase-2-8-管道-完整"></a>
### 环节 4：确定性分析（阶段 2-8 管道）✅ 完整

| 阶段 | 功能 | 文件 | 状态 |
|-------|------|------|------|
| 阶段 1 | 证据提取 | `extractor.py` | ✅ |
| 阶段 2 | 证据标准化 | `normalizer.py` | ✅ |
| 阶段 3 | 辅助分析（时间线 + 信号摘要 + 发现） | `analyzer.py` | ✅ |
| 阶段 4 | Markdown 报告草稿 | `reporter.py` | ✅ |
| 阶段 5 | 候选因果链草稿 | `hypothesis.py` | ✅ |
| 阶段 6 | 保守版根因报告 | `root_cause.py` | ✅ |
| 阶段 7 | 修复建议草稿 | `remediation.py` | ✅ |
| 阶段 8 | 最终交付模板 | `delivery.py` | ✅ |

**注意**：以上所有阶段都是**确定性规则引擎**（基于确定性规则），不是 LLM 驱动的。它们基于模板匹配、模式识别、硬编码的分类逻辑。

---

<a id="环节-5ai-上下文构建-️-输出给外部-llm但-agent-loop-未自动化"></a>
### 环节 5：AI 上下文构建 ⚠️ 输出给外部 LLM，但智能体循环未自动化

| 能力 | 文件 | 状态 |
|------|------|------|
| ANR 分组（按时间窗口聚合锚点） | `ai_context.py:_build_groups()` | ✅ |
| cache.md 生成（结构化证据缓存） | `ai_context.py:_render_cache_markdown()` | ✅ |
| ai_prompt.md 生成（给 LLM 的分析提示词） | `ai_context.py:_render_ai_prompt()` | ✅ |
| 网页界面（手动输入/上传 → 查看产物） | `scripts/web_server.py` | ✅ |
| **管理智能体调度** | — | ❌ **缺失** |
| **子智能体：CPU/内存** | — | ❌ **缺失** |
| **子智能体：堆栈/锁** | — | ❌ **缺失** |
| **子智能体：I/O/Binder** | — | ❌ **缺失** |
| **假设验证器（重新探查迭代）** | — | ❌ **缺失** |
| **LLM API 调用集成** | — | ❌ **缺失** |

**详细说明：**

设计架构中的推理层是一个完整的**多智能体系统**：

```
管理智能体（编排器）
  ├── 子智能体：CPU/内存     ← 未实现
  ├── 子智能体：堆栈/锁      ← 未实现
  ├── 子智能体：I/O/Binder   ← 未实现
  └── 假设验证器            ← 未实现
       └── 触发重新探查 → 重新以更窄窗口过滤
```

当前系统的"AI 推理"完全依赖**外部人工操作**：
1. 运行 `python3 -m anr_evidence --build-ai-context <input>` 生成 `ai_prompt.md`
2. 手动把 `ai_prompt.md` 的内容复制到 ChatGPT/Claude 等外部 LLM
3. 人工阅读 LLM 回复

没有任何自动化智能体调度、子智能体分配、迭代验证或 LLM API 调用。

---

### 环节 6：最终输出 ✅ 完整

| 能力 | 文件 | 状态 |
|------|------|------|
| JSON 结构化输出 | 所有阶段产物 | ✅ |
| Markdown 报告 | `reporter.py` + `delivery.py` | ✅ |
| 回放仪表盘 | `dashboard.py` + `scripts/render_replay_dashboard.py` | ✅ |
| 基准回放 | `replay.py` | ✅ |
| 网页界面查看 | `scripts/web_server.py` | ✅ |

---

## 三、缺失环节总览

按严重程度排序：

| # | 缺失项 | 严重程度 | 对应设计文档章节 | 原因 |
|---|--------|----------|-----------------|------|
| 1 | **AI 智能体循环未自动化** | 🔴 严重 | 3.3 推理引擎 | 多智能体推理完全是外部手动操作 |
| 2 | **语义权重系统（标签优先级）** | 🔴 严重 | 3.2 3.2 语义敏感度加权 | 设计明确要求级别 1/2/3，当前平权 |
| 3 | **证据切片模式（ESS）** | 🟡 中等 | 4. 数据模式 | 输出仍是原始文本行，非结构化 JSONL |
| 4 | **跨源实体关联** | 🟡 中等 | 3.1 实体关联 | PID/TID/UID 未跨源映射 |
| 5 | **ΔT 时间归一化** | 🟡 中等 | 3.1 时间对齐 | EventLog/Logcat 行无 `delta_t_seconds` |
| 6 | **迭代重新探查能力** | 🟡 中等 | 3.3 迭代重新采样 | 管理智能体无法触发重新扫描 |
| 7 | **上下文泛滥防护** | 🟢 低 | 3.2 | 无按 `importance` 截断逻辑 |

---

## 四、结论

当前系统实现了 **完整的"确定性数据准备管道"**：

```
解压 → 时间窗口过滤 → 标签匹配 → Trace结构化解析 → 规则引擎分析(阶段 2-8) → AI上下文缓存
```

缺失的是 **"AI 推理自动化"** 部分：

```
AI 提示词生成 ✅ → [此处断链] → 多智能体推理调度 ❌ → 迭代验证 ❌ → 自动根因报告 ❌
```

具体说：
- **完整闭环** 到 `ai_prompt.md` 的生成止步 — 系统能很好地准备数据给 AI 看
- **断链处** 在 AI 推理的自动化执行 — 设计中的管理智能体 + 子智能体多角色协作、迭代重新探查、假设验证全部是手动步骤
- **语义权重** 是设计文档路线图标为待办的阶段 2，是下一个应补充的环节
