# 未完成任务与验收清单

> 复核日期：2026-09-08；本轮实施前代码基线：`37cd8d1`（P0 首批）；摘要基线：`4d266e2`。Android 17 B1 与 C1 最小证据引用契约均已实现并完成本地验证；其余增量仍按各节状态管理。本文件是当前可版本管理的 backlog。
> G001、R2、R1 已完成并提交；Android 17 B1 与 C1 最小证据引用契约已完成本地验证。G002/B2 与 G003 整体仍未完成；其余任务按下表区分“未实现”和“已有基础但未完成”，不把局部能力当作整项验收。
> `存在` 表示已有能力，`优化` 表示补强，`新增` 表示新增能力，`验收` 表示检查；`[x]` 只用于已有或已验证完成的条目。

## 1. 当前状态总览

**仍有 16 个编号任务组未完成，另有 3 个维护条目。** 当前共有 24 个已勾选、28 个未勾选条目；复选框同时包含实现与验收，不能把 28 个复选框当成 28 个独立功能。

| 任务 | 当前状态 | 本次核对的剩余缺口 / 证据 |
| --- | --- | --- |
| G001 / R2 / R1 | 已完成、已提交 | 摘要已存在；`a7a1347` 包含结构化实体匹配、真实 Agent 按组隔离、完整 selected-trace 库存、scope/conflict 门禁、保守 hint 与回归 |
| S1 | 首批门禁已实现，整体未完成（P0） | 已有归档预算、默认外发拒绝、合成测试路径敏感标记检查、HTTP 字节/次数预算及 manager 结构/保守字段校验；引用保真脱敏和完整安全验收仍待完成 |
| B1 / B2 | B1 Android 17 已实现并本地验证；B2 未完成 | B1 已增加双仓库固定 revision manifest、两类 Input reason resolver 和 Phase 1 additive 字段，其余八类明确 unsupported；14 项 ontology 专项、18 项 ontology+legacy 专项、全量 unittest discovery 运行 418 项并整体通过（1 项跳过），compileall/diff 检查均通过。B2 的 Android 14–16 profiles 与 OEM overlays 仍未完成；B1 provenance 校验仅覆盖 manifest 自洽性与 canonical source URL，不会重新下载并重算上游源码 blob，也不证明设备兼容 |
| C1 | 最小证据引用契约已实现并本地验证 | `2a757f2` 覆盖 mixed archive+loose winner provenance、重生成清理 SQLite/WAL/SHM 与 refs/ESS 且保留用户分析/四槽位/baseline logcat，以及发布失败回滚后可重试；全量 424 项通过、1 项跳过，compileall/diff 检查通过。G003 仍因 C2 共享流式/预算及 100 MiB/1 GiB 验证未完成而保持 pending |
| C2 | 已有流式过滤/归档有界读，未完成 | 归档已加预算并有界读取，目录 loader/package 仍保留或合并全量字符串；单归档预算不是跨输入共享预算，也不证明端到端 100 MiB/1 GiB 内存目标 |
| V1 | manager 门禁已完成、已验证 | `e7a03bf` 后的 V1 增量在 manager 接受边界结合当前轮 immutable span、同组 ANR anchor、主线程 direct-blocker、PID/时间/scope 与字段引用资格；还要求受信任的 `eligibleForHighCausalConfidence=true`。当前 auditor 明确保持该值为 false，因此所有现有 high 均 fail-closed；旧无 store 路径仍仅为 low/medium 的 `structure_only` |
| E1 / Q1 | 已有 hint eval，未完成 | 当前 13 个 eval 用例只验证 hint；机制、引用、跨版本语料准入、冻结评测集和正式性能/成本门禁尚未闭环 |
| DQ1 | 已有局部门禁，未完成 | R1 已校验 PID/进程/窗口/采样锁环；尚未抽成带统一引用、缺口与 provenance 的共享 dump auditor，不能把窗口外等同于 stale |
| D1 / D2 | 已有分类/报告/专项指引，未完成 | no-focus/Input 分类和四槽位已有；尚缺结构化 target/blame/lifecycle 闭环、dumpsys adapter 与 WMS/AMS/Recents 状态校验 |
| F1 | 已有类型/模板，未完成 | Broadcast/Service/Provider/Job 等已有识别基础，尚非逐版本组件状态机 pack |
| F2 | 已有 stack/lock/pressure hint，未完成 | 缺 caller-peer-transaction 关联、共享 dump 质量接口及完整 Watchdog/cause-amplifier 判定 |
| P1 | 未实现 | 无 Perfetto/WinScope capability runner、版本化查询/adapter 与时钟/实体映射验收 |
| A1 | 已有 Agent/re-probe，未完成 | 按组 EntityMap 已接入，但仍遍历全部 `SUB_AGENT_SPECS`；`additionalTags` 只解析未执行，probe 参数/预算及能力驱动 SOP 未完成 |
| DOC | 部分完成 | 本次 TODO 同步、R1 hint 迁移说明已完成；后续故事文档和完整 v1 DoD 仍待对应实现/验证，不提前勾选 |

当前代码证据：
- [身份解析](../anr_evidence/log_identity.py)、[EntityMap](../anr_evidence/entity_linker.py)、[真实 Agent 调用与结果接受边界](../anr_evidence/ai_agent.py)；[HTTP mock 集成回归](../tests/test_ai_agent.py) 验证了按组身份，但不证明脱敏。
- [FusionScope](../anr_evidence/cross_source_fusion.py)、[dump 锁环门禁](../anr_evidence/trace_preprocessor.py)、[压力线索](../anr_evidence/anrmanager_parser.py)；旧“无 scope 升级 / CONFIRMED 根因 / CPU 推断泄漏”问题已修复，不再作为未完成项。
- [分类器](../anr_evidence/extraction/classification.py)、[source kinds](../anr_evidence/constants.py)、[loader](../anr_evidence/loaders/core.py)、[package 合并](../anr_evidence/loaders/package.py)、[ESS](../anr_evidence/evidence_slice.py)、[现有 eval](../anr_evidence/eval.py)、[模板 remediation](../anr_evidence/remediation.py)。

运行时状态单独看待：本地 `.omx/ultragoal/goals.json` 仍记录 G001 complete、G002 in_progress、G003–G010 pending。当前检出的 Android 17 B1 与 C1 最小证据引用契约已实现并完成本地验证；但 G002 仍包含未完成的 B2 Android 14–16 profiles 与 OEM overlays，G003 仍包含未完成的 C2 共享流式/预算及 100 MiB/1 GiB 验证，均不能宣称完成。本轮文档更新不改写原运行账本、不启动旧大目标，也不推断其他工作树状态。

## 2. 已存在，保留而非重做

- [x] 存在：G001 Input/WMS/SF 有界摘要、三 family、多样性/去重、透明 relevanceScore、Input/no-focus 门控及产物保留测试。去重仅限摘要，分数仅表示检索相关性。
- [x] 存在：Phase 1–8、baseline/type-template additive extraction、按 ANR 分组和四个分析槽位。
- [x] 存在：多组 trace/logcat 预处理缓存、known-anchor 过滤、Meminfo 窗口、ESS/权重/ΔT/context flooding；不能再写成“从零实现”。
- [x] 存在：Multi-Agent、有限 re-probe、LLM 超时/重试、Replay、13 个 hint eval 用例和 CI。存在不等于已有类型化工具、引用验证或完整机制评测。
- [x] 存在：合成 ZIP/TAR/目录端到端测试；不等于有经授权、脱敏且人工标注的跨版本真实 corpus。

## 3. 推荐执行顺序与最小故事

**R2 → R1 → B1 → C1 最小契约已完成**；下一条确定性实现链为 **V1 → DQ1 → D1**。group ID 冲突已作为独立 P0 小修完成；S1 首批门禁已落地，但整体 P0 仍未完成，后续脱敏/安全验收不能被 B1/C1 长期挤后。S1/E1 从首个相关改动开始贯穿；当前默认拒绝外发，仅允许显式声明的合成测试数据；完整脱敏验收前不得将真实私有日志送入远程 Agent，离线确定性路径继续可用。B2、C2、D2 和后续扩展分别按需要交付，不阻塞第一条可靠 Input 闭环。优先级表示风险/价值，不代表所有 P1 项必须串行完成。

### R1 · 已完成 · 跨源 promotion 与保守输出（`a7a1347`；原 P0）

- [x] 优化：为 fusion 补目标身份、group/time scope 和具体旁证引用；未知身份、仅相邻日志、其他进程信号只作为 context，不提升目标 hint。全局压力独立标记，不自动归为目标根因。
- [x] 优化：生成提示删除可修改三个保守字段的例外；区分 hint 强度与因果结论，删除“trace-only 可直接定根因”的无条件表述。不得删除原始证据。
- [x] 验收：同窗口无关 PID、anchor/trace 错 PID、缺失身份、窗口外 kernel 信号不升级；同实体且时间重叠的有效旁证保留规则允许的升级；记录命中原始行，不只有 regex 名称。
- [x] 验收：先补负例，再调整已有“无身份也升级”的测试为明确正/负例；保留多 group 缓存隔离；生成提示及确定性输出持续约束 `finalJudgment=false` / `notRootCauseYet=true` / `requiresHumanConfirmation=true`。
- [x] 优化：单独迁移旧 hint 旁路到证据质量门禁：跨 dump 同 TID 环必须校验 PID/时间/ANR 归属；CPU/RSS 单独只能支持压力线索，不能证明泄漏。新增 wrong-PID/stale/重复 TID/仅 CPU+RSS 回归，降级过强表述而不删除原始观察。

> R1 当前范围已验证：不带 scope 的旧 fusion 调用仍有效，但不再提升置信度；引用中的 `lineIndex` 属于过滤视图，不冒充原文件 byte range。远程 LLM 的强制结构门禁在本轮 S1 落地，完整引用/因果校验仍由 V1 完成，完整 dump 生命周期仍由 DQ1/D2 完成。

### R2 · 已完成 · 结构化实体匹配（`a7a1347`；原 P0）

- [x] 优化：替换 EntityMap 的数字/包名子串匹配；区分 log emitter 和消息中的 target，PID/TID/UID 分类型，未解析的关联显式 unknown。不扩成完整 token 图。
- [x] 验收：PID `123` 不匹配 `1234`、耗时或时间戳；`com.demo` 不匹配 `com.demo.other`；子进程名称采用明确规则；同号 PID/TID/UID 不覆盖彼此类型；重复运行排序一致。
- [x] 验收：复用摘要已有身份语义与测试案例，不直接把私有评分 helper 变成全局 resolver；跨组/多进程负例覆盖真实调用路径。

### B1 · 已完成 · 单 profile 本体与旧分类兼容（G002 第一部分）

开发状态：Android 17 B1 已实现并完成本地验证。新增 `aosp_contract`、固定 Android 17 base/native revision manifest、两类 Input reason 识别及 Phase 1 `classification.timeoutOntology`；旧分类字段保留。其余八类显式 unsupported；无版本、未知版本、revision/类型冲突、多 ANR reason 均降级，不推断 directBlocker。验证覆盖 14 项 ontology 专项、18 项 ontology+legacy 专项、全量 unittest discovery 运行 418 项并整体通过（1 项跳过）、compileall 和 diff 检查。该结论只完成 G002 的 B1 部分；B2 仍未完成。详见 [来源与开发边界](aosp-anr-source-map.md)。

- [x] 新增：已核验 Android 17 AOSP profile 的固定 revision/path/symbol，并提供可本地校验的规则 manifest/source-map；分别输出 trigger、directBlocker、evidenceStatus，不从 trigger 推导 blocker。这里的 provenance 校验只验证 manifest 自洽性与 canonical source URL，不重新下载或重算上游源码 blob。
- [x] 验收：每个核验的 TimeoutKind 已映射或明确 unsupported；legacy public type 不变；未知/矛盾/缺少 build version 安全降级；每条已映射规则有 provenance 与匹配输入位置。原始 hash+byte-range 仍由 C1 补齐，不能把过滤行号伪称不可变引用。
- [x] 验收：正例、歧义、unknown、provided_type 兼容测试已独立通过；不虚构 SHA，也不声称 Android 14–17 全覆盖或真实设备兼容。

### B2 · P2 · 多版本与 OEM 扩展（G002 余项）

- [ ] 新增：Android 14、15、16 各为独立 profile/验收；OEM overlay 为单独 story，只有明确厂商来源时启用，不默认覆盖 AOSP。
- [ ] 验收：版本差异逐项测试；没有已核验 profile 的输入明确 unsupported；overlay 冲突 fail closed。合成 fixture 只能证明规则行为，不证明真实设备兼容。

### C1 · P1 · 最小证据引用契约（G003 第一部分；V1/D1 前置）

开发状态：C1 最小证据引用契约已实现，并由 `2a757f2` 的生命周期回归及全量验证确认。基础 API 保留原始 bytes、区分 archive container/member、显式关联 group，回读检查 hash/source/group/range；directory/ZIP/TAR/mixed loader 透传获胜来源，context 可选生成 SQLite、逐组引用 sidecar 与带 span 的 ESS。已验证 mixed archive+loose 输入只保留最终 winner provenance；关闭 store/ESS 后重生成会清理 `evidence.sqlite3`、WAL/SHM、refs 与 ESS，同时保留用户分析、四个分析槽位和 baseline `logcat.txt`；发布中途失败会回滚到上一代完整产物，随后重试成功。全量测试 424 项通过、1 项跳过，`compileall` 与 `git diff --check` 通过。该结论仅完成 C1 最小契约：G003 仍因 C2 共享流式/预算及 100 MiB/1 GiB 验证未完成而保持 pending，也不代表 V1 因果资格、S1 引用保真脱敏或 G002/B2 完成。

- [x] 新增：以 stdlib `sqlite3` 实现 artifact + span + group 及原始回读；按实际需求映射 ESS，暂不预建完整 finding/probe/图数据库。保留旧 package API。
- [x] 验收：引用解析到原始 artifact hash + byte range，覆盖 UTF-8 多字节、CRLF、无末尾换行、同名 shard、重复内容、文件变更与越界；归档引用明确区分 container/member，mixed archive+loose 输入只保留获胜来源。
- [x] 验收：source/group 校验阻止跨 ANR 引用；旧产物/字段兼容；重生成清理 SQLite/WAL/SHM、refs/ESS 时保留用户分析、`logcat.txt` 和四槽位，发布失败可回滚并重试。这个阶段不宣称达到大输入内存目标。
- [x] 优化：同毫秒多 ANR 的 group ID 已追加稳定哈希后缀；普通非冲突 ID 保持不变。回归覆盖目录、index、事件引用及真实 HTTP mock 中的按组 map；远程入口重复 ID 防御保留。此项作为独立 P0 小修完成，不代表 C1 store 完成。

### V1 · P1 · 确定性输出/引用验证（G008/G009 前置；依赖 C1）

开发状态：显式 store 模式在当前轮/当前组 span 目录与原始字节回读之上，已完成 V1 manager 接受门禁。chain/conclusion 均要求 groupId 和非空 span 引用；伪造/跨组/未提供/变化引用拒绝。`high` 仅在本地同时确认 matched identity、within-window time、eligible scope、verified immutable field references、受信任的 `eligibleForHighCausalConfidence=true`、同组 ANR anchor 和主线程 direct-blocker span 时接受；direct-blocker 的受支持签名目前限 Binder reply wait、lock wait 与 render/fence wait，并必须匹配 candidate 的 signalCategory。多个 high 结论未共用同一 blocker span 时作为矛盾拒绝。当前 `dump_quality` 刻意不生成 lifecycle/freshness 的正面证明，并保持 `eligibleForHighCausalConfidence=false`，故真实运行中的 high 均 fail-closed，直到后续 DQ1 提供受信任资格。`score`、retrieval relevance、hint 强度、specialist prose 与证据内 prompt injection 不会升级高置信；无 store 的旧路径仅接受 low/medium，标记为 `structure_only`。这只表示该窄 manager qualification 门禁已实现，`finalJudgment=false`、`notRootCauseYet=true` 与 `requiresHumanConfirmation=true` 仍是硬约束，且不证明 freshness、process lifetime 或最终根因。

- [x] 优化：在当前 Agent 结果接受边界验证结构、引用归属、保守字段和 high 候选的本地矛盾；不等待八类 specialist 或 Perfetto。
- [x] 验收：非法 JSON/类型、伪造/越界/跨组引用、将日志中的指令当规则、提升保守字段均拒绝或输出明确 invalid 状态；原始证据保留。mock LLM 测试无需 API key。
- [x] 验收：缺少 identity、time、scope、字段 immutable provenance、ANR anchor 或直接 blocker 时无法输出高因果置信；`relevanceScore` / hint 强度不能直接充当因果置信。

### S1 · P0 · 输入与模型边界（G003/G009 前置，贯穿）

- [x] 优化：ZIP/TAR 已增加成员数、单项/累计解压字节预算及实际有界读取；TAR 在推进下一 header 前校验当前成员，扩展元数据也受解压流预算约束。路径/link/special-member 拒绝；混合目录不会吞掉已读取归档的安全异常。
- [x] 新增：远程请求默认拒绝，仅支持调用方显式声明的合成测试数据；最终 HTTP payload 的常见 secret/PII 标记和当前 API key 命中即拒绝。加入请求/响应字节、HTTP 尝试（含重试）、输出 token、迭代上限；原始 baseline 不改写。
- [x] 验收：manager 接受点强制校验完整 JSON、字段类型/范围、三个保守字段和 re-probe 参数；拒绝重复 key、非标准数字、未知字段和过深结构；迭代耗尽不再恢复未校验结果。HTTP mock 覆盖实际请求拒绝和子 Agent 内容转发边界。
- [x] 验收：小型压缩膨胀、TAR 扩展元数据、累计实际读、异常编码、危险路径/link、secret/PII、恶意日志指令和违规模型结果均有回归；离线 baseline 保留，不自动执行模型/日志中的 shell、SQL 或设备命令。
- [ ] 新增：完成保留引用的脱敏 view、替换映射及真实数据外发准入。`synthetic_only` 是调用方声明，不是数据自动认证；当前敏感标记检测不是完整脱敏，不允许用它放行真实私有日志。
- [ ] 验收：完整 S1 仍需覆盖脱敏映射/出站契约、跨归档与 loose 输入的共享资源边界及更完整对抗样本。当前每归档预算不是端到端 RSS/硬墙钟保证；ZIP 中央目录由 stdlib 在成员数门禁前解析。V1 再接不可变引用和因果门禁。

详见 [P0 安全边界与迁移说明](safety-boundaries.md)。

### E1 · P1 · 分层评测与真实 corpus 准入（G009 前置，贯穿）

- [ ] 优化：在 D1 前冻结第一版评测 schema，复用现有 eval，分别统计 trigger、blocker、evidence status、引用、矛盾和高置信违规；每次规则变更补正例和反例。声明哪些输出被完整标注，不能把未列入 forbidden 的额外输出当作已证明正确，也不用“13 个 hint 测试通过”代替端到端正确率。
- [ ] 新增：真实样本需来源许可、脱敏校验、版本/设备信息及人工标注；按事故/设备来源隔离开发集与评测集，冻结 corpus revision 与各类最低 support 后再启用统计阈值，报告各类样本量、缺失标签和 unsupported，不强行塞入真实私有日志。
- [ ] 验收：已支持范围内引用完整性 100%、高置信门禁违规 0、对抗集关键 trigger 误报 0；没有样本的类别报 N/A。macro-F1 ≥0.95、top-3 ≥0.90 保留为冻结 corpus 后的目标，未满足数据条件不得宣称达标。

### DQ1 · P1 · 共享 dump 身份与质量边界（G004/G006 共用；依赖 R1/R2/C1/V1）

已有基础：`FusionScope` 与跨 trace 比对已有局部身份/窗口门禁；以下任务是统一接口、引用和质量语义，不重做 R1。

- [ ] 优化：在 R1 已收紧的判定上统一 dump PID、采样时间、ANR 归属及 stale/wrong-PID/unknown 状态，返回支撑引用与缺口；由 D1/D2/F2 复用，不另建平行规则。
- [ ] 验收：跨 dump 同 TID、PID 复用但时间不重叠、缺时间、晚采样、错组引用均无法满足高置信门槛；没有证据时状态 unknown，不凭统一固定延迟推断所有版本 stale。完整 queue/suppression 生命周期仍由 D2 增量接入。

### D1 · P1 · 最小 Input 证据闭环（G004 第一部分；依赖 B1/C1/R1/R2/V1/DQ1/E1）

- [ ] 优化：在已有 no-focus/Input 类型区分之上建立结构化 trigger 状态、anchor、原 target、blame target 和 dump PID/time 字段，仅对有证据的转移建边；不能只增加类型别名就算闭环。
- [ ] 验收：no-focus、unacked、错 PID、迟到 dump、恢复/取消、未知身份各有 fixture；缺少 WMS 映射不得猜 blame；纯 log/trace 无 Perfetto 也完成保守报告。
- [ ] 验收：D1 新闭环输出接入既有四槽位：三个专项结论分别写入 Trace / EventLog / Logcat-AnrManager 槽位，综合结论只写入 `#### AI Analysis — 最终 ANR 综合分析`；不是把同一综合结论复制四次。

### C2 · P2 · 单路径流式迁移与资源预算（G003 余项；依赖 C1/S1）

开发状态（2026-09-09）：目录 EventLog 已可经 staging → store → 单 group consumer；本轮新增 ZIP/TAR → 已打开 store 的 opt-in 成员流，保留 container/member provenance、实际共享预算和原子回滚，且不走 legacy 全文/base64 loader。该接口仍未接入 multi-group context/export，未覆盖完整过滤 baseline/compact view 单路径，也没有 100 MiB/1 GiB 基准，以下 C2 条目继续保持未完成。

- [ ] 优化：先目录/shard → store → 单 ANR consumer，再迁移 ZIP/TAR、多组和完整导出。迭代读取后不得由兼容 adapter 再构造全量大字符串。
- [ ] 验收：13 MiB 回归后分级运行 100 MiB / 1 GiB；记录 revision、机器、Python、输入哈希、group 数、冷暖运行次数、端到端 wall/峰值 RSS/磁盘成本；1 GiB 端到端峰值 RSS 目标 ≤512 MiB。
- [ ] 验收：保留完整过滤后的外部 baseline，以流式写出满足预算；compact LLM view 独立有界。快 CI 跑小 fixture，大基准独立执行；本轮不将旧 13 MiB 结果外推为 1 GiB 达标。

### D2 · P2 · WMS/AMS/Recents 扩展（G004 余项；依赖 D1）

- [ ] 新增：dumpsys input/window、token/display/blame transfer、queue/dump lifecycle 分 adapter 交付；按真实输入确认字段，不先实现无消费者的通用因果图。
- [ ] 优化：在 DQ1 共享边界上补充生命周期状态；F2 和涉及 dump 的图形 adapter 复用 DQ1，不另建彼此冲突的 stale/wrong-PID 判定器。
- [ ] 验收：pending-focus、embedded PID、重复/抑制/恢复、Recents cancel/finish 与缺失事件负例；状态缺口标 unknown，不因未找到日志认定事件未发生。

### 后续独立扩展（P2；不阻塞最小可靠 Input 版本）

| ID / 原目标 | 最小范围与依赖 | 独立验收边界 |
| --- | --- | --- |
| F1 / G005 | 复用已有类型和模板；B1/C1/V1/E1 后逐个增加 Broadcast、Service、Provider、Job、App-start/registered pack | 每个 pack 独立正例/中止/恢复/错组件/未知版本负例；trigger 不等于 root cause |
| F2 / G006 | R1/R2/B1/C1/V1/DQ1/E1 后先 caller-peer Binder 关联，再 lock owner、pressure、Watchdog | 未知 peer 明示；RUNNABLE 不等于高 CPU；pressure 区分 cause/amplifier/context；不是重写现有堆栈 hint |
| P1 / G007 | C1/S1 后先 artifact/capability 探测，再固定版本 Trace Processor 的一个 scheduling 查询；图形/WinScope adapter 单独故事 | 外部二进制由显式配置提供，不静默下载安装；版本/allowlist/时钟转换、无工具与不支持 schema 降级测试；各 adapter 自带 fixture |
| A1 / G008 | B1/S1/V1/E1 之后先让已有三个 specialist 按需运行，再实现一个有界 search/expand probe；SOP 独立 | 记录选择/跳过原因；参数、结果数、次数/耗时/token 上限；反证与无结果停止测试；新增角色须有证据需求，不以数量验收 |
| Q1 / G009 | 各 pack 与 E1 持续集成，再做完整版本发布评测 | 冻结 corpus + 分版本指标 + perf/cost/provenance；真实样本不足或外部工具未验时保留未完成状态 |
| DOC / G010 | 各故事同步公共文档；最终只做汇总独立审查 | 不把所有文档拖到最后；逐条核对测试/产物证据，旧版本/不支持项不能误标完成 |

- [ ] 优化：A1 首个 probe 故事先修复或替换当前 `_execute_re_probe`：`additionalTags` 目前被解析但没有执行，须定义可测试语义，非法 source/负窗口拒绝，重过滤后仍应用预算；不在旧主路径之外另建功能重叠的工具。
- [ ] 验收：F1/F2/P1/A1/Q1/DOC 各按上表建立独立证据后关闭；不能仅凭本表或原 G 编号状态勾选完成。

## 4. 保留但降级的维护任务

- [ ] 优化：P2 Agent 指引生成/原生 include，统一项目片段并保留各工具专属配置；先有一致性测试，不使用跨平台风险较高的 symlink，不覆盖 OMX 运行时标记。
- [ ] 优化：P3 CLI subprocess/临时 fixture helper，复用 `tests/helpers.py`，保留 Phase 2–8 CLI 真实入口及 lazy-execution 断言；仅随相关测试修改处理。
- [ ] 优化：P3 大文件拆分与 `dedupe_dicts` 性能，仅在 profile/变更热点/ownership 证据出现后安排；现在不新增抽象或依赖。

## 5. 本轮验证与后续通用完成条件

### R2/R1 实施结果（2026-09-05，已提交 `a7a1347`）

- R2：新增 `log_identity.py` 结构化身份解析；EntityMap 取消数字/包名子串匹配，区分 emitter/target 与 PID/TID/UID，查询支持 `entity_type`；selected trace 的引用只来自选中的完整 dump，压缩展示不会丢失实体库存；真实 Agent 路径为每个 ANR 构建独立 map 并标注 group，HTTP mock 已验证实际请求中的 PID/TID/UID 不串组。Trace 行号仍对应输入原文，其他来源位置属于传入的组内过滤视图。
- R1：fusion 要求目标与 trace 的身份、时间、group 一致；同组 AnrManager 可补充 legacy anchor PID，冲突则只保留 context。记录具体原始行、拒绝理由及过滤视图位置，每条规则/来源的旁证与 context 各有上限。
- 旧结论旁路：高 CPU/RSS 仅作为 pressure clue；修复生成提示残留的泄漏推断；跨 dump 相同 TID 还须匹配锁边/对象，区分 anchor 窗口与采样间隔，NaN/inf/负窗口拒绝。超出配置窗口不等于已证明 stale。
- 兼容变化：`consolidate_deadlock_across_traces` 的旧无身份调用保留单 dump 观察但不升级；远程 Agent 遇到重复 group ID 会在发送前明确拒绝，避免默默覆盖实体 map；消费端需从 `DEADLOCK_CYCLE_CONFIRMED` / `confidence=confirmed` 迁移为 `DEADLOCK_CYCLE_CORROBORATED` / `confidence=strong`，仍须人工确认。详见 [trace hint 迁移说明](trace-hint-annotation-design.md)。
- 没有修改原始 baseline、CLI 入口或四槽位布局；未新增第三方依赖，未修改 `.omx/ultragoal` 运行时状态；上述实现/测试及原 TODO 已提交为 `a7a1347`。后续 TODO 同步及 P0 首批改动已提交为 `37cd8d1`；提交不代表已推送。

### 验证证据

以下为前次文档同步/R2-R1 验证记录；本轮 P0 的新验证单独列在下一节，未重跑性能基准。

- `python3 -m unittest discover -s tests -v`：运行 352 项，351 通过、1 个可选样本跳过（实施前为 321 项）。
- `python3 scripts/run_eval.py tests/fixtures/eval`：13/13 hint 用例通过；正例补齐真实格式的 PID 身份，新增错身份/缺身份/跨窗口反例；这不是跨版本机制评测结果。
- `python3 -m compileall -q anr_evidence tests scripts`：通过。
- 上轮实现验收的 `python3 scripts/benchmark_input_window_digest.py`（历史记录，本轮未重跑）：13 MiB、6 group、1 轮预热/5 次测量；digest 开启相对关闭 wall 中位数变化 -0.37%，峰值 RSS 中位数 +0.09%，产物/事件及 15%/10% 阈值检查通过。该基准比较当前版本 digest 开关，不是与旧提交比较，也不证明 1 GiB 达标。
- 回归补测先复现了 selected trace 同 TID 串 dump、生成提示旧泄漏结论、legacy anchor 忽略有效 AnrManager PID、yearless 旁证未使用明确 anchor 年份，以及真实 Agent 全局实体 map 串组、压缩后 TID 库存缺失、重复 group ID 静默覆盖；修复后通过。
- Markdown 本地链接/任务标签与 `git diff --check`：通过；独立审查发现的窗口绕过、身份补全、旧 hint 文档及 Agent 集成缺口已逐项补测修复；最后的集成复核由主代理完成。
- 未运行外部 LLM、设备/真实 corpus 验证，未核验 AOSP 来源。没有独立 lint/typecheck 配置，不新增工具依赖来制造验证记录。

前次只读复现：两个不同进程的同时间 `am_anr` 曾生成 2 个 group、仅 1 个不同 ID；本轮已修复，详见下一节。

### 前轮 P0 实施结果（`37cd8d1`）

- group ID：同毫秒冲突采用稳定后缀，单组旧 ID 不变；生成端、FusionScope、事件、索引/目录和远程 map 使用相同 ID。
- S1：单归档成员/数据/元数据门禁、默认外发拒绝及合成测试边界、有限 HTTP 调用、manager 结构/保守字段强制校验已接入主路径；不是仅增加提示文字。
- 新增负例还复现并修复：目录入口吞安全异常、TAR 在检查前跳过大成员、quoted JSON 凭证漏检、迭代耗尽接受非法结果、unhashable confidence / 超大 score / 深层结构导致校验异常。
- 不改变 baseline/四槽位布局，不新增依赖，不修改运行账本。README 与安全迁移说明同步；完整脱敏、V1 不可变引用/因果校验、跨输入流式预算仍未完成。
- 新验证：`python3 -m unittest discover -s tests -q` 运行 391 项（390 通过、1 跳过）；`compileall` 通过；hint eval 13/13；文档链接/复选框统计与 `git diff --check` 通过。未运行外部 LLM、真实私有日志、设备测试或大输入 benchmark。

### Android 17 B1 本地验证（2026-09-08）

- `python3 -m unittest tests.test_aosp_timeout_ontology -q`：14 项 ontology 专项通过。
- `python3 -m unittest tests.test_aosp_timeout_ontology tests.test_cli_phase2 -q`：18 项 ontology + legacy Phase 2 兼容专项通过。
- `python3 -m unittest discover -s tests -q`：运行 418 项，结果 `OK (skipped=1)`。
- `python3 -m compileall -q anr_evidence tests scripts` 与 `git diff --check`：通过。
- 验证范围只证明当前检出的 Android 17 B1 规则、manifest 约束和 legacy 兼容行为；未重新下载/重算固定 URL 对应的上游源码 blob，未执行 Android 14–16/OEM overlay 验收，也未执行真实设备兼容性验证。因此 G002 仍未完成。

C1 最小证据引用契约与 V1 manager 高置信资格门禁已完成；后续按 **DQ1 → D1** 推进，C2 另行完成 G003 的共享流式/预算与 100 MiB/1 GiB 验证。S1/E1 的前置约束不变，不得将 V1 的窄 manager qualification 等同于完整脱敏/出站安全、process lifetime/freshness 证明、机制评测或最终根因结论。

- [ ] 验收：每个后续代码故事先补针对性回归，再实现，执行专项 → 全量 unittest → compileall → diff 检查；适用时补 eval/benchmark/真实样本，明确 skip 与外部门禁。
- [ ] 验收：baseline、CLI/产物兼容、四槽位保留、三项保守字段持续成立；只有证据与复核齐全才更新任务完成状态。
