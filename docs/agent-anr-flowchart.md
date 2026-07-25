# agent-anr 完整流程架构图

> 自动生成日期: 2026-07-24
> 基于 `anr_evidence/` 核心模块 + `scripts/` 入口脚本 + `docs/anr-analysis-flow.md`

---

## 一、总览：从输入到交付

```mermaid
flowchart TD
    subgraph INPUT["📥 输入源"]
        A1["bugreport 目录"]
        A2["ZIP / TAR 压缩包"]
        A3["fixture JSON"]
    end

    subgraph PHASE0["🔧 Phase 0: 预处理"]
        B1["extract_bugreport.py\n解压/提取"]
        B2["anr_strategy.py\n推断 ANR 类型 + 包名"]
        B3["anr_to_ai.py\n生成 AI Context"]
    end

    subgraph PIPELINE["⚙️ Phase 1-8 确定性流水线"]
        direction LR
        C1["Phase 1\n📄 extractor.py\n证据提取"] --> C2["Phase 2\n🔬 normalizer.py\n证据标准化"]
        C2 --> C3["Phase 3\n📊 analyzer.py\n辅助分析"]
        C3 --> C4["Phase 5\n🔗 hypothesis.py\n候选因果链"]
        C4 --> C5["Phase 6\n🎯 root_cause.py\n保守根因报告"]
        C5 --> C6["Phase 7\n💊 remediation.py\n修复建议"]
        C6 --> C7["Phase 8\n📦 delivery.py\n最终交付"]
    end

    subgraph AI_PATH["🤖 AI 分析路径"]
        D1["anr_ai_context/\nindex.json + 每 ANR 目录"]
        D2["AI 读取 anr_analysis.md\n填写分析槽位"]
        D3["综合分析结论\n## 综合分析结论"]
    end

    subgraph MULTI_AGENT["🧠 多 Agent AI 推理"]
        E1["ai_agent.py\nManager Agent 调度"]
        E2["Sub-Agent 1\nCPU / Memory"]
        E3["Sub-Agent 2\nStack / Lock"]
        E4["Sub-Agent 3\nI/O / Binder"]
        E5["迭代 Re-probe 循环\n→ integrated_report"]
    end

    subgraph OUTPUT["📤 输出产物"]
        F1["Markdown 报告\n(Timeline / Blocking Point /\nRoot-Cause Chains /\nRemediation)"]
        F2["anr_analysis.md\n(含 AI 分析填充)"]
        F3["delivery 最终交付包"]
    end

    A1 --> B1
    A2 --> B1
    A3 --> B2
    B1 --> B2
    B2 --> B3
    B2 --> C1
    B3 --> D1
    D1 --> D2
    D2 --> D3
    D3 --> F2

    C7 --> F3
    F3 --> F1

    B2 -.-> E1
    E1 --> E2
    E1 --> E3
    E1 --> E4
    E2 & E3 & E4 --> E5
    E5 --> F1

    style PHASE0 fill:#e1f5fe,stroke:#0288d1
    style PIPELINE fill:#f3e5f5,stroke:#7b1fa2
    style AI_PATH fill:#e8f5e9,stroke:#388e3c
    style MULTI_AGENT fill:#fff3e0,stroke:#f57c00
    style OUTPUT fill:#fce4ec,stroke:#c62828
```

---

## 二、Phase 1-8 详细流水线

```mermaid
flowchart TD
    subgraph P1["Phase 1 — 证据提取 (extractor.py)"]
        P1A["extract_evidence_package()\n解析 trace / event_log / logcat / kernel_log"]
        P1B["extract_baseline_package()\n基线证据（不可删除）"]
        P1C["trace_preprocessor.py\nTrace 确定性结构化解析\n→ PID / 主线程 / 线程摘要 / block hint"]
        P1D["log_filter.py\nEventLog 两阶段过滤\n(ANR 前 12s 窗口 × 标签 × 包名)"]
        P1E["anrmanager_parser.py\n解析 AnrManager 诊断块"]
    end

    subgraph P2["Phase 2 — 标准化 (normalizer.py)"]
        P2A["normalize_evidence_package()\nprovenance 追踪 / 锚点对齐 / 降级标记"]
        P2B["time_norm.py\nΔT 时间归一化\n(每条记录相对 ANR 锚点的时间偏移)"]
        P2C["evidence_slice.py\nEvidenceSlice 结构化数据模型\n→ JSONL 读写 / 按 importance/ΔT/source 过滤"]
        P2D["weighting.py\n60 个标签 × 3 级重要性\n(Critical / Warning / Contextual)"]
        P2E["entity_linker.py\n跨源实体关联\n(trace PID/TID ↔ EventLog ↔ logcat ↔ kernel)"]
        P2F["context_flooding.py\n基于 importance 的截断策略\n→ 保留 Critical/Anchor 并显式报告预算溢出"]
    end

    subgraph P3["Phase 3 — 辅助分析 (analyzer.py)"]
        P3A["analyze_normalized_package()\n非根因裁决"]
        P3B["_build_timeline()\n关键事件时间线"]
        P3C["_build_signal_summary()\n信号汇总 (CPU/内存/IO/锁)"]
        P3D["_build_findings()\n可疑发现列表"]
        P3E["pattern_catalog.py\n已知 ANR 模式匹配"]
        P3F["cross_source_fusion.py\n跨源证据融合"]
    end

    subgraph P5["Phase 5 — 候选因果链 (hypothesis.py)"]
        P5A["generate_causal_draft()"]
        P5B["_build_candidate_chains()\n候选根因链路"]
        P5C["_confidence_level()\n置信度评估"]
        P5D["标记: notRootCauseYet = true\nrequiresHumanConfirmation = true"]
    end

    subgraph P6["Phase 6 — 保守根因 (root_cause.py)"]
        P6A["generate_root_cause_report()"]
        P6B["_candidate_conclusions()\n候选结论 + 支持证据"]
        P6C["_unresolved_questions()\n未确认项"]
        P6D["_global_limitations()\n证据局限性"]
    end

    subgraph P7["Phase 7 — 修复建议 (remediation.py)"]
        P7A["generate_remediation_drafts()"]
        P7B["_build_remediation_drafts()\n分级修复草案"]
        P7C["_priority() + _why_gated()\n优先级 + 门控理由"]
    end

    subgraph P8["Phase 8 — 交付 (delivery.py + reporter.py)"]
        P8A["render_final_delivery()\n汇总候选结论 + 因果链 + 修复建议"]
        P8B["render_analysis_report()\nMarkdown 报告生成"]
    end

    P1A --> P1B
    P1A --> P1C & P1D & P1E
    P1A --> P2A
    P2A --> P2B & P2C & P2D & P2E & P2F
    P2A --> P3A
    P3A --> P3B & P3C & P3D
    P3A --> P3E & P3F
    P3A --> P5A
    P5A --> P5B & P5C
    P5B --> P5D
    P5A --> P6A
    P6A --> P6B & P6C & P6D
    P6A --> P7A
    P7A --> P7B & P7C
    P7A --> P8A
    P8A --> P8B

    style P1 fill:#e3f2fd,stroke:#1565c0
    style P2 fill:#fce4ec,stroke:#c62828
    style P3 fill:#f3e5f5,stroke:#6a1b9a
    style P5 fill:#fff8e1,stroke:#f9a825
    style P6 fill:#e8f5e9,stroke:#2e7d32
    style P7 fill:#fff3e0,stroke:#e65100
    style P8 fill:#e0f2f1,stroke:#00695c
```

---

## 三、AI Context 生成与 AI 分析路径

```mermaid
flowchart TD
    subgraph INPUT2["输入"]
        I1["bugreport 目录 / ZIP / TAR / fixture"]
        I2["用户参数: --package / --anr-type"]
    end

    subgraph AI_CTX["ai_context.py — build_ai_context()"]
        A1["_build_groups()\n按 ANR 分组"]
        A2["_render_cache_markdown()\n生成 anr_ai_context/ 目录"]
        A3["index.json\n(目录索引)"]
        A4["anr_analysis.md\n(AI 指令 + 过滤证据 + 内联分析槽位)"]
        A5["logcat.txt\n(完整过滤后 logcat)"]
        A6["_root_cause_pattern_hints_for_group()\n根因模式提示注入"]
        A7["_inject_hint_markers()\nTrace 提示标记注入"]
    end

    subgraph AI_READ["AI 分析阶段"]
        R1["读取 anr_analysis.md"]
        R2["填写 3 个分源分析槽位:\n- AI Analysis — Trace\n- AI Analysis — EventLog\n- AI Analysis — Logcat / AnrManager"]
        R3["填写第 4 个综合槽位:\n#### AI Analysis — 最终 ANR 综合分析"]
    end

    subgraph OUTPUT2["输出"]
        O1["完整填充的 anr_analysis.md"]
        O2["结构化 Markdown 报告"]
    end

    I1 & I2 --> A1
    A1 --> A2
    A2 --> A3 & A4 & A5
    A2 --> A6 & A7
    A4 --> R1
    R1 --> R2
    R2 --> R3
    R3 --> O1
    O1 --> O2

    style AI_CTX fill:#e8f5e9,stroke:#2e7d32
    style AI_READ fill:#e3f2fd,stroke:#1565c0
```

---

## 四、多 Agent AI 推理架构

```mermaid
flowchart TD
    subgraph PROVIDER["Provider 抽象层"]
        PR1["Anthropic (Claude)"]
        PR2["OpenAI (GPT)"]
    end

    subgraph MANAGER["Manager Agent"]
        M1["调度 + 编排"]
        M2["读取初始证据包"]
        M3["分发任务到 Sub-Agent"]
        M4["汇总 + 迭代 Re-probe"]
    end

    subgraph SUB1["Sub-Agent 1: CPU/Memory"]
        S1A["分析 CPU 负载"]
        S1B["分析内存压力"]
        S1C["交叉验证系统资源"]
    end

    subgraph SUB2["Sub-Agent 2: Stack/Lock"]
        S2A["分析调用栈阻塞点"]
        S2B["锁等待分析"]
        S2C["Binder 事务分析"]
    end

    subgraph SUB3["Sub-Agent 3: I/O/Binder"]
        S3A["I/O 阻塞分析"]
        S3B["Binder 对端追溯"]
        S3C["跨进程依赖分析"]
    end

    subgraph RESULT["集成输出"]
        RS1["integrated_report\n- candidateChains\n- candidateConclusions\n- remediationDrafts"]
    end

    PR1 & PR2 --> M1
    M1 --> M2
    M2 --> M3
    M3 --> S1A & S2A & S3A
    S1A --> S1B --> S1C
    S2A --> S2B --> S2C
    S3A --> S3B --> S3C
    S1C & S2C & S3C --> M4
    M4 -->|需要更多证据| M3
    M4 -->|充分| RS1

    style PROVIDER fill:#f3e5f5,stroke:#7b1fa2
    style MANAGER fill:#fff3e0,stroke:#e65100
    style SUB1 fill:#e3f2fd,stroke:#1565c0
    style SUB2 fill:#e8f5e9,stroke:#2e7d32
    style SUB3 fill:#fce4ec,stroke:#c62828
    style RESULT fill:#fff8e1,stroke:#f9a825
```

---

## 五、Replay & Eval 旁路

```mermaid
flowchart LR
    subgraph REPLAY["Replay 基准回放"]
        RP1["run_replay.py\n批量回放 manifest"]
        RP2["replay.py\n归档 session / 对比 diff"]
        RP3["compare_replays.py\n两次回放对比"]
        RP4["dashboard.py\n可视化对比 HTML 仪表盘"]
    end

    subgraph EVAL["评估体系"]
        EV1["run_eval.py\n批量评估"]
        EV2["eval.py\nGround Truth 对比"]
        EV3["test_eval_groundtruth.py\nGround Truth 验证"]
    end

    RP1 --> RP2
    RP2 --> RP3
    RP2 --> RP4
    EV1 --> EV2
    EV2 --> EV3

    style REPLAY fill:#e0f7fa,stroke:#006064
    style EVAL fill:#f9fbe7,stroke:#827717
```

---

## 六、完整模块依赖图

```mermaid
flowchart TD
    CLI["cli.py\n命令行入口"] --> PIPE["pipeline.py\nphase 识别 + run_until"]
    PIPE --> EXT["extractor.py\nPhase 1"]
    CLI --> AI_CTX["ai_context.py\nAI 上下文"]
    CLI --> AGENT["ai_agent.py\n多 Agent AI"]
    EXT --> NORM["normalizer.py\nPhase 2"]
    NORM --> TIME["time_norm.py"]
    NORM --> SLICE["evidence_slice.py"]
    NORM --> WEIGHT["weighting.py"]
    NORM --> ENTITY["entity_linker.py"]
    NORM --> FLOOD["context_flooding.py"]
    NORM --> ANALYZE["analyzer.py\nPhase 3"]
    ANALYZE --> FUSION["cross_source_fusion.py"]
    ANALYZE --> PATTERN["pattern_catalog.py"]
    ANALYZE --> HYP["hypothesis.py\nPhase 5"]
    HYP --> RC["root_cause.py\nPhase 6"]
    RC --> REM["remediation.py\nPhase 7"]
    REM --> DELIV["delivery.py\nPhase 8"]
    DELIV --> REP["reporter.py\nPhase 4 报告"]

    EXT --> STRAT["anr_strategy.py\n类型策略"]
    EXT --> ANRMGR["anrmanager_parser.py"]
    EXT --> TRACE["trace_preprocessor.py"]
    EXT --> LOGFILT["log_filter.py"]

    AI_CTX --> STRAT
    AI_CTX --> ANRMGR

    AGENT --> WEIGHT
    AGENT --> SLICE
    AGENT --> ENTITY
    AGENT --> FLOOD
    AGENT --> FUSION

    style CLI fill:#ffcc80,stroke:#e65100
    style EXT fill:#90caf9,stroke:#1565c0
    style NORM fill:#f48fb1,stroke:#c62828
    style ANALYZE fill:#ce93d8,stroke:#6a1b9a
    style HYP fill:#fff59d,stroke:#f9a825
    style RC fill:#a5d6a7,stroke:#2e7d32
    style REM fill:#ffab91,stroke:#d84315
    style DELIV fill:#80cbc4,stroke:#00695c
```

---

## 七、ANR 类型策略分支

```mermaid
flowchart TD
    ANR_TYPE["anr_strategy.py\nstrategy_for_package()"]

    ANR_TYPE --> INPUT_DISP["Input Dispatching Timeout\n(5s 超时)"]
    ANR_TYPE --> NO_FOCUS["No Focus Window\n(失去焦点)"]
    ANR_TYPE --> BROADCAST["Broadcast Timeout\n(广播超时)"]
    ANR_TYPE --> SERVICE["Service Timeout\n(服务超时)"]
    ANR_TYPE --> PROVIDER["Content Provider Timeout\n(CP 超时)"]
    ANR_TYPE --> UNKNOWN["Unknown / 其他\n(30s 窗口, 通用信号)"]

    INPUT_DISP --> SIG1["关键信号:\n- InputDispatcher 超时\n- 无焦点窗口\n- 主线程阻塞在 binder/fence/lock\n- 输入事件积压"]
    NO_FOCUS --> SIG2["关键信号:\n- Window 焦点丢失\n- Activity 未 resume\n- Surface 未就绪\n- 窗口动画未完成"]
    BROADCAST --> SIG3["关键信号:\n- BroadcastReceiver.onReceive() 超时\n- FG 广播 10s / BG 广播 60s"]
    SERVICE --> SIG4["关键信号:\n- Service.onCreate/onStartCommand 超时\n- FG 服务 20s / BG 服务 200s"]
    PROVIDER --> SIG5["关键信号:\n- ContentProvider.onCreate() 超时\n- 10s 限制"]
    UNKNOWN --> SIG6["通用信号:\n- 30s 窗口\n- 通用阻塞模式\n- CPU/内存/I/O 压力"]

    style INPUT_DISP fill:#ffcdd2,stroke:#c62828
    style NO_FOCUS fill:#c8e6c9,stroke:#2e7d32
    style BROADCAST fill:#bbdefb,stroke:#1565c0
    style SERVICE fill:#fff9c4,stroke:#f9a825
    style PROVIDER fill:#e1bee7,stroke:#6a1b9a
    style UNKNOWN fill:#d7ccc8,stroke:#4e342e
```

---

## 八、证据源与跨源融合

```mermaid
flowchart LR
    subgraph SOURCES["主要证据源"]
        S1["🧵 Trace\n(调用栈 / 线程状态 / 锁)"]
        S2["📋 EventLog\n(am_anr / 系统事件)"]
        S3["📱 Logcat\n(AnrManager / 进程诊断)"]
        S4["💻 Kernel Log\n(内核 OOM / lowmemorykiller)"]
        S5["🧠 Meminfo\n(目标/高负载进程内存快照)"]
    end

    subgraph FUSION["跨源融合 (cross_source_fusion.py)"]
        F1["fuse_cross_source_evidence()\n跨源模式旁证 + hint 置信度提升"]
        F2["entity_linker.py\nPID/TID/进程名 跨源关联"]
        F3["time_norm.py\n统一 ΔT 时间轴"]
    end

    subgraph ANCHOR["锚定"]
        AN1["am_anr 精确时间戳\n(主锚点)"]
        AN2["AnrManager 诊断块\n(辅助锚点)"]
    end

    S1 & S2 & S3 & S4 & S5 --> F1
    F1 --> F2
    F2 --> F3
    AN1 & AN2 --> F1

    style SOURCES fill:#e8eaf6,stroke:#283593
    style FUSION fill:#fce4ec,stroke:#c62828
    style ANCHOR fill:#fff3e0,stroke:#e65100
```

---

## 附：CLI 命令速查

| 命令 | 说明 |
|------|------|
| `python3 scripts/anr_to_ai.py <input>` | 生成 AI Context (`anr_ai_context/`) |
| `python3 -m anr_evidence <fixture>` | 运行 Phase 1 证据提取 |
| `python3 -m anr_evidence --analyze <fixture>` | 仅分析 (Phase 1-3) |
| `python3 -m anr_evidence --report <fixture>` | 推进至 Phase 7 并渲染 Phase 4 Markdown 报告 |
| `python3 -m anr_evidence --deliver <fixture>` | 最终交付 (Phase 1-8) |
| `python3 scripts/extract_bugreport.py <zip> -o <dir>` | 解压 bugreport |
| `python3 scripts/run_replay.py <manifest>` | 运行 Replay |
| `python3 -m unittest discover -s tests -v` | 运行所有测试 |
