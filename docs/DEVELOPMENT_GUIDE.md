# agent-anr 项目结构与后续开发说明

## 1. 项目定位

`agent-anr` 是一个 Android ANR 证据抽取与 AI 辅助分析工具链。

核心目标：

- 从 bugreport / ZIP / TAR / fixture JSON 中抽取 ANR 相关证据；
- 按 ANR anchor 拆分成独立分析工作区；
- 保留 trace、EventLog、logcat、AnrManager、meminfo、kernel log 等证据；
- 引导 AI / 人工按来源逐步分析；
- 输出保守、可审计、需要人工确认的候选根因报告。

项目原则：

- **高召回优先**
- **证据可追溯**
- **不提前最终定责**
- **baseline evidence 不可被类型策略删除**
- **所有自动分析结论默认需要人工确认**

---

## 2. 项目结构图

```text
llm-anr/
├── AGENTS.md                  # Codex / Agent 项目指令
├── CLAUDE.md                  # Claude Code 项目指令
├── README.md                  # 英文项目说明
├── README-zh.md               # 中文项目说明
│
├── anr_evidence/              # 核心 Python 包
│   ├── __main__.py            # python3 -m anr_evidence 入口
│   ├── cli.py                 # CLI 参数解析与 phase 调度
│   │
│   ├── loaders/               # 输入加载层
│   │   ├── core.py            # fixture / 目录 / ZIP / TAR 加载
│   │   └── package.py         # 构造标准 source package
│   │
│   ├── discovery/             # 特殊目录发现逻辑
│   │   └── monkey.py          # Monkey / System_log 智能发现
│   │
│   ├── extraction/            # Phase 1 证据抽取辅助模块
│   │   ├── anchors.py         # ANR anchor 收集与选择
│   │   ├── classification.py  # ANR 类型识别
│   │   ├── evidence.py        # baseline + 类型模板证据组装
│   │   ├── summary.py         # source / tier / status 汇总
│   │   └── common.py          # 通用工具
│   │
│   ├── sources/               # 按证据源独立过滤
│   │   ├── trace/             # trace 过滤与时间解析
│   │   ├── event_log/         # EventLog 过滤
│   │   ├── logcat/            # logcat / AnrManager 过滤
│   │   ├── meminfo/           # meminfo 快照解析
│   │   └── shared/            # source filter 公共类型与工具
│   │
│   ├── extractor.py           # Phase 1: evidence extraction
│   ├── normalizer.py          # Phase 2: normalization
│   ├── analyzer.py            # Phase 3: assisted analysis
│   ├── reporter.py            # Phase 4: report draft
│   ├── hypothesis.py          # Phase 5: candidate causal chains
│   ├── root_cause.py          # Phase 6: conservative root-cause candidates
│   ├── remediation.py         # Phase 7: remediation drafts
│   ├── delivery.py            # Phase 8: final delivery template
│   │
│   ├── ai_context.py          # 生成 anr_ai_context 工作区
│   ├── ai_agent.py            # 多 Agent AI 分析入口
│   ├── trace_preprocessor.py  # trace 结构化解析、锁链、死锁、阻塞点识别
│   ├── anr_strategy.py        # ANR 类型策略
│   ├── anrmanager_parser.py   # AnrManager dump flow 解析
│   ├── log_filter.py          # 日志窗口过滤与时间解析
│   ├── pattern_catalog.py     # 主线程阻塞模式库
│   ├── root_cause_hints.py    # 根因模式 hint 推断
│   │
│   ├── weighting.py           # 证据重要性分级
│   ├── evidence_slice.py      # Evidence Slice Schema
│   ├── entity_linker.py       # 跨源 PID/TID/进程实体关联
│   ├── context_flooding.py    # LLM 上下文防溢出
│   ├── time_norm.py           # ΔT 时间归一化
│   ├── cross_source_fusion.py # 跨源证据融合
│   │
│   ├── replay.py              # replay 基准回放
│   ├── dashboard.py           # replay dashboard 渲染
│   ├── eval.py                # 评估相关逻辑
│   └── workflow.py            # source filter workflow
│
├── scripts/                   # 独立命令行工具
│   ├── anr_to_ai.py           # 推荐 Agent 入口：生成 anr_ai_context/
│   ├── anr_preprocessor.py    # trace 预处理 CLI
│   ├── anr_trace_filter.py    # trace 单源过滤
│   ├── anr_event_log_filter.py# EventLog 单源过滤
│   ├── anr_logcat_filter.py   # logcat 单源过滤
│   ├── anr_meminfo_filter.py  # meminfo 单源过滤
│   ├── anr_filter_workflow.py # 多源过滤 workflow
│   ├── extract_bugreport.py   # bugreport 解压
│   ├── run_replay.py          # replay 执行
│   ├── compare_replays.py     # replay 对比
│   ├── render_replay_dashboard.py
│   └── web_server.py          # 本地 Web UI
│
├── tests/                     # unittest 测试
│   ├── fixtures/              # 基础 fixture
│   │   ├── nfw_01.json
│   │   ├── idt_01.json
│   │   ├── amb_01.json
│   │   ├── unk_01.json
│   │   └── ...
│   ├── fixtures/eval/         # eval fixture
│   ├── test_*                 # phase、CLI、source、AI context 等测试
│   └── helpers.py
│
├── docs/                      # 设计文档
│   ├── DIRECTORY.md
│   ├── ANR-analysis-flow.md
│   ├── ANR智能分析系统.md
│   ├── GAP_ANALYSIS.md
│   ├── algorithm_design_log_filter.md
│   ├── event_log_tags_master.md
│   └── ...
│
├── skills/                    # ANR 分来源分析技能说明
│   ├── anr-analysis.md
│   ├── anr-trace-analysis.md
│   ├── anr-eventlog-analysis.md
│   ├── anr-logcat-analysis.md
│   ├── anr-root-cause.md
│   └── ...
│
└── anr_ai_context/            # 生成的 AI 分析工作区，通常不作为源码维护
    ├── index.json
    └── anr-<timestamp-or-anchor>/
        ├── anr_analysis.md
        └── logcat.txt
```

---

## 3. 核心处理流程

```text
输入
bugreport / ZIP / TAR / fixture JSON
        │
        ▼
loaders/
加载输入，识别 trace / event_log / logcat / meminfo / kernel log
        │
        ▼
extractor.py + extraction/ + sources/
Phase 1: 抽取 baseline evidence，识别 ANR 类型、anchor、证据窗口
        │
        ▼
normalizer.py
Phase 2: 标准化时间、来源、provenance、degraded 状态
        │
        ▼
analyzer.py
Phase 3: 生成 timeline、signal summary、非最终 finding
        │
        ▼
hypothesis.py
Phase 5: 生成候选因果链
        │
        ▼
root_cause.py
Phase 6: 生成保守根因候选
        │
        ▼
remediation.py
Phase 7: 生成修复建议草案
        │
        ▼
delivery.py
Phase 8: 输出最终交付 Markdown 模板
```

AI 分析工作区流程：

```text
scripts/anr_to_ai.py
        │
        ▼
ai_context.py
        │
        ▼
anr_ai_context/index.json
anr_ai_context/<anr-id>/anr_analysis.md
anr_ai_context/<anr-id>/logcat.txt
        │
        ▼
AI / 人工按槽位分析：
Trace → EventLog → Logcat/AnrManager → Final ANR
```

---

## 4. 常用开发命令

### 4.1 生成 AI 分析工作区

```bash
python3 scripts/anr_to_ai.py <bugreport_dir_or_archive_or_fixture> \
  --package <package.name>
```

不指定包名时：

```bash
python3 scripts/anr_to_ai.py <bugreport_dir_or_archive_or_fixture>
```

### 4.2 确定性流水线

```bash
python3 -m anr_evidence tests/fixtures/nfw_01.json
python3 -m anr_evidence --normalize tests/fixtures/nfw_01.json
python3 -m anr_evidence --analyze tests/fixtures/nfw_01.json
python3 -m anr_evidence --hypothesize tests/fixtures/nfw_01.json
python3 -m anr_evidence --root-cause tests/fixtures/nfw_01.json
python3 -m anr_evidence --remediate tests/fixtures/nfw_01.json
python3 -m anr_evidence --deliver tests/fixtures/nfw_01.json
```

### 4.3 单源调试

```bash
python3 scripts/anr_trace_filter.py <trace_file_or_package>
python3 scripts/anr_event_log_filter.py <event_log_file>
python3 scripts/anr_logcat_filter.py <logcat_file>
python3 scripts/anr_meminfo_filter.py <meminfo_or_bugreport_dir> --package <package.name>
python3 scripts/anr_filter_workflow.py <bugreport_dir_or_archive_or_fixture>
```

### 4.4 测试与静态检查

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q anr_evidence tests scripts
```

---

## 5. 后续开发建议

### 5.1 新增 ANR 类型

优先修改：

```text
anr_evidence/constants.py
anr_evidence/anr_strategy.py
anr_evidence/extraction/classification.py
tests/test_anr_type_expansion.py
```

开发原则：

1. 新类型只能增加分析重点、关键词、窗口和 hint；
2. 不能删除 baseline evidence；
3. unknown / ambiguous 必须安全 fallback；
4. 新类型必须补 fixture 或测试用例；
5. `triggerType` 不等于最终根因。

---

### 5.2 新增证据源

优先放在：

```text
anr_evidence/sources/<source_name>/
```

建议结构：

```text
anr_evidence/sources/new_source/
├── __init__.py
└── filter.py
```

同时更新：

```text
anr_evidence/constants.py
anr_evidence/loaders/package.py
anr_evidence/workflow.py
tests/test_source_workflow.py
```

要求：

- 输出统一的 `SourceFilterResult`；
- evidence 必须带 provenance；
- 缺失或不可读时返回 degraded / warning，不应直接中断整体流程；
- 不能污染其他 ANR group 的证据。

---

### 5.3 修改 trace 分析能力

核心文件：

```text
anr_evidence/trace_preprocessor.py
anr_evidence/pattern_catalog.py
tests/test_trace_cleaning.py
tests/test_trace_deadlock.py
tests/test_main_thread_patterns.py
tests/test_native_poll.py
```

注意：

- 主线程阻塞点与最终根因要分开；
- `nativePollOnce` 不一定是根因；
- 锁等待要追 owner thread；
- deadlock / self-lock / lock owner chain 都应保守输出；
- 新增 pattern 必须加测试。

---

### 5.4 修改 EventLog / logcat 过滤

相关文件：

```text
anr_evidence/log_filter.py
anr_evidence/sources/event_log/filter.py
anr_evidence/sources/logcat/filter.py
anr_evidence/anrmanager_parser.py
docs/event_log_tags_master.md
```

测试：

```text
tests/test_log_filter.py
tests/test_anrmanager.py
tests/test_anrmanager_parser.py
```

注意：

- `am_anr` 是主 anchor；
- 有 package 时只严格过滤 anchor，不要误删系统上下文；
- AnrManager dump flow 是 CRITICAL，应完整保留；
- 不要只按窄窗口截断 AnrManager 关键行。

---

### 5.5 修改 AI context 输出

核心文件：

```text
anr_evidence/ai_context.py
scripts/anr_to_ai.py
tests/test_ai_context.py
```

必须保持：

```text
anr_ai_context/
  index.json
  anr-<id>/
    anr_analysis.md
    logcat.txt
```

分析槽位顺序固定：

```text
Trace → EventLog → Logcat/AnrManager → Final ANR
```

Final ANR 必须写回 `anr_analysis.md`，不能只留在聊天输出中。

---

## 6. 代码修改守则

后续开发请遵守：

1. **先读 README-zh.md 与 AGENTS.md**
2. **先加或更新测试，再改核心逻辑**
3. **不要引入新依赖，除非明确需要**
4. **优先复用现有 source filter / workflow / evidence schema**
5. **不要把直接阻塞点写成最终根因**
6. **不要跨 ANR anchor 混合证据**
7. **不要删除 baseline evidence**
8. **所有 AI / 自动分析输出保持保守字段：**

```json
{
  "finalJudgment": false,
  "notRootCauseYet": true,
  "requiresHumanConfirmation": true
}
```

---

## 7. 推荐开发流程

```text
1. 明确修改目标
   │
   ▼
2. 找到对应模块
   - 输入加载：loaders/
   - 证据过滤：sources/
   - 类型识别：anr_strategy.py / constants.py
   - trace 解析：trace_preprocessor.py
   - AI context：ai_context.py
   - CLI：cli.py / scripts/
   │
   ▼
3. 添加 fixture 或单元测试
   │
   ▼
4. 修改实现
   │
   ▼
5. 运行目标测试
   │
   ▼
6. 运行全量测试与 compileall
   │
   ▼
7. 更新 README / docs / skills 中对应说明
```

---

## 8. 最小验收标准

每次后续开发完成前，至少确认：

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q anr_evidence tests scripts
```

并检查：

- 是否保持 baseline evidence；
- 是否避免跨 ANR group 污染；
- 是否保留 provenance；
- 是否保持 conservative output；
- 是否更新相关文档。
