# 当前未完成任务与验收清单

> **复核日期：2026-09-09。** 本文件是当前可版本管理的 backlog；它记录当前代码、测试与外部证据的边界，不以历史提交、局部 fixture 或单项规则回归替代完整验收。
>
> 状态约定：**本地已验证**指当前仓库的代码和自动化证据通过；**blocked** 指需要真实设备、受许可语料、组织审批、上游字节或部署信任根，不能由合成 fixture 或 mock 替代。没有已核验 profile 的输入必须保持 `unsupported`，缺失事件必须保持 `unknown`。

## 1. 当前结论

**尚未全部完成。** 已有若干本地闭环，但完整路线图和外部/设备/部署验收仍未达标。

- 不把已实现的局部 adapter、fail-closed 回归或 synthetic fixture 当作真实设备兼容、真实私有数据安全或端到端评测正确率。
- `synthetic_only` 是调用方声明，不是数据自动认证；敏感标记检测或 fake verifier 不能放行真实私有日志。
- 本文件不修改 `.omx` 运行账本；该账本可能保留旧目标状态，不能代替当前源码和验证证据。

## 2. 已完成的本地基础（不重复实施）

| 范围 | 当前本地状态 | 仍不代表的结论 |
| --- | --- | --- |
| G001 / R1 / R2 | 已提交：结构化 identity、跨源 scope/conflict 门禁、保守 hint 与按组 Agent 隔离。 | 不证明脱敏、真实设备或最终根因。 |
| B1 / Android 17 | 已有单 profile、本地 ontology 回归与 legacy 兼容。 | 不证明 Android 14–16、OEM 或设备兼容。 |
| C1 / C2 / G003 | EvidenceStore、不可变引用、rollback 与 directory/ZIP/TAR 默认 store-backed 路径已完成本地验证；100 MiB、1 GiB 本地基准已记录。 | 不替代 S1 出站安全、跨设备资源硬保证或真实设备证据。 |
| V1 | manager 入口级 identity/time/blocker/citation 资格门禁已验证，并对未满足条件的 high 结论 fail closed。 | 不等同最终根因证明；现有 auditor 不会伪造高因果资格。 |
| DQ1 / D1 | 共享 dump identity/quality/provenance auditor 与保守 Input lifecycle 闭环已验证。 | 不包含 D2 的 production qualified blame 或完整 queue/dump lifecycle。 |
| P2 / P3 CLI helper | Agent 指引生成合同、真实 Phase 2–8 CLI 子进程入口和 lazy-execution 回归已完成本地验证。 | 不替代下列扩展任务或外部验收。 |

## 3. 未完成任务与关闭条件

### A. 外部证据或组织/设备门禁

- [ ] **S1：真实私有日志外发安全验收（P0，blocked）**
  - 已有：默认外发拒绝、archive accepted-bytes/entries 账本、local/mock 脱敏/permit/verifier 拒绝路径、HTTP 次数/字节预算和保守输出校验。
  - 关闭条件：真实 deployment 的不可伪造且可撤销 verifier；脱敏映射与出站契约审计；跨 archive 与 loose 输入的真实 RSS、壁钟、解压 CPU、ZIP 中央目录资源边界；自由文本/恶意 payload 对抗语料；组织 DLP/隐私审查、来源许可和真实受许可语料。
  - 约束：当前每归档账本不是端到端资源保证；V1 不替代外发安全结论。

- [ ] **B2 / G002：Android 14、15、16 独立 profile 与 OEM overlay**
  - 已有：缺 profile 时 `unsupported_android_release`、overlay source/kind 冲突时 `vendor_overlay_unsupported` 的 fail-closed 回归；官方 Gitiles source-byte 捕获器。
  - 关闭条件：Android 14、15、16 **各自**的 source bytes、manifest、SHA-256、规则正反例和设备证据。任一 release 的通过不得启用、覆盖或计入其他 release。
  - OEM 约束：AOSP profile 不默认加载 OEM 规则；OEM story 必须具有厂商/产品/版本和可核验 source provenance，缺失或冲突即 fail closed。
  - 当前缺口：2026-09-09 的 Gitiles 获取超时，尚无三版可比较的版本语义、raw bytes/SHA-256 或设备证据；合成 fixture 只证明规则行为。

- [ ] **E1 / Q1：正式评测、机制语料与真实 corpus admission**
  - 已有：评测 schema 已覆盖 trigger、blocker、evidence status、引用、矛盾和高置信违规；real-corpus admission、case SHA-256、来源/脱敏/人工标注记录、事故/设备 source split、support/missing/unsupported 报告和 fail-closed release gate 已冻结。
  - 关闭条件：外部核验的许可且脱敏 Android 14–16 真实语料；每版本/类别实际 support；冻结 corpus revision；macro-F1/top-3 metric engine 与外部工具证据。
  - 约束：没有样本的类别报告 `N/A`；只有合成 Android 17 case 时必须继续 `blocked`，不得声称端到端正确率或发布评测达标。

### B. 仍需真实输入消费者的产品扩展

- [ ] **D2 / G004：production token/display/qualified blame 与 queue/dump lifecycle**
  - 已有：严格 `dumpsys input/window` raw snapshot adapter 只作为 DQ1 的 source-local `dumpLifecycle` 投影；D1/F2 复用 DQ1，不另建 stale/wrong-PID 判定器。
  - 关闭条件：可信 capture timestamp、同组 provenance 和实际消费者驱动的 token/display/**qualified blame** transfer；独立 queue/dump lifecycle adapter；真实设备/OEM 验证。
  - 约束：不得先建无消费者的通用因果图；pending-focus、embedded PID、重复/抑制/恢复、Recents cancel/finish 与缺失事件负例必须覆盖；缺失事件保持 `unknown`。

- [ ] **F1 / G005：逐版本 framework timeout packs 与 lifecycle**
  - 已有：Android 14–17 分别固定 recipe；只有 tag → commit → Gitiles blob → SHA-256 → symbol 全部通过的 pack 才可用；未验证 kind 保持 `unverified`。
  - 关闭条件：每个 release 独立的真实 source bytes/verified pack、规则正反例、OEM profile 与设备证据。一个 release 的验收不得外推至其他 release。

- [ ] **F2 / G006：Binder peer / Watchdog 因果链**
  - 已有：精确 transaction key 与 caller/peer PID+进程的 observation edge、DQ1 trace admission、ordered Watchdog 事件和 cause/amplifier/context 分层。
  - 关闭条件：lock-owner 事务关联、生产 Binder/Watchdog dump adapter、真实设备验证和完整因果图。
  - 约束：继续复用 DQ1 lifecycle/identity/provenance；不得另建冲突的 stale/wrong-PID 判定器，也不得把 observation 提升为根因。

- [ ] **P1 / G007：Perfetto / WinScope capability、查询与 adapter**
  - 已有：allowlisted Trace Processor capability、hash-pinned WinScope ZIP、固定 PID scheduling query 与 metadata-only WinScope 路径。
  - 关闭条件：图形/trace data-source/capture-manifest adapter、设备兼容与跨时钟关联证据。
  - 约束：本地工具可用或 archive 有效不等同于 DQ1 归属、lifecycle 证据或根因信号。

- [ ] **A1 / G008：能力驱动 Agent router 与 re-probe 运营闭环**
  - 已有：按 source capability 与 focused evidence 调度、实际执行 source/window/tag 收窄、次数/结果/墙钟/token 预算和 audit 回归。
  - 关闭条件：部署遥测、真实语料、独立 SOP，以及由证据需求证明的新角色或外部 search/expand 能力。
  - 约束：不因当前 local router 存在就宣称完整 Agent 能力或真实运行效果。

### C. 文档与维护

- [ ] **DOC / G010：持续维护完整 v1 DoD 与最终验收矩阵**
  - 已有：[v1 DoD 与最终验收](v1-dod-and-final-acceptance.md) 已区分 `core-v1-minimum-reliable-input` 与 `full-roadmap-v1`，并提供 evidence bundle 与签核模板。
  - 关闭条件：每个后续故事完成其独立证据后同步矩阵；不得把 B2、D2、F1、F2、P1、A1、Q1 或 S1 的局部实现写成 full-roadmap 已验收。

- [ ] **P3：大文件拆分与 `dedupe_dicts` 性能（条件性维护）**
  - 当前决策：不实施，不新增抽象或依赖。
  - 观察：`ai_context.py` 有近期变更热点，但尚无 ownership、测试隔离收益或 split-specific profile；`dedupe_dicts` 只用于小规模 warning 去重，现有 fixture/微型 profile 不代表真实大输入。
  - 重新排期条件：明确的 profile 瓶颈、可测的测试隔离/维护收益、ownership/merge friction，或允许改变 canonical-key 语义的正式需求。

## 4. 持续验收规则

后续任一代码故事在标记完成前均必须：

1. 先补目标行为的正例与反例回归，再实施；
2. 执行专项测试、全量 `unittest`、`compileall` 和 `git diff --check`；
3. 适用时附上 formal eval、资源/磁盘 benchmark、真实样本或设备证据，并明确 `skip`、`unsupported` 与外部门禁；
4. 保持 baseline、CLI/产物兼容、AI 分析四槽位与三个保守字段；没有证据和复核不得更新任务为完成。

## 5. 证据与合同索引

- [AOSP ANR source map](aosp-anr-source-map.md)：profile/source provenance 与 release 边界。
- [EvidenceStore 与外发边界](evidence-store.md)：immutable references、redacted view 与 outbound admission。
- [Formal eval v2](formal-eval-v2.md) / [real-corpus admission](formal-eval-v3-real-corpus-admission.md)：评测字段、coverage 和发布门禁。
- [D2 dumpsys adapter contract](dumpsys-input-window-adapter-contract.md)：source-local snapshot、DQ1 共享边界和 fail-closed 条件。
- [Performance adapter contract](performance-adapter-contract.md)：Perfetto/WinScope 能力范围。
- [C2 本地基准记录](benchmarks/)：100 MiB、1 GiB store-backed/default-routing 的本地测量；不构成跨设备资源硬保证。
- [Agent 指引生成合同](agent-guidance.md)：P2 生成范围、include、marker preservation 与 fail-closed 条件。
