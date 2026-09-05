# 未完成任务与验收清单

> 复核日期：2026-09-05；实施前代码基线：`4d266e2`。本文件是当前可版本管理的 backlog。
> 当前已完成 R2/R1 的本地实现与回归，其他未勾选任务仍待实施；未启动全部长期路线。
> `存在` 表示已有能力，`优化` 表示补强，`新增` 表示新增能力，`验收` 表示检查；`[x]` 只用于已有或已验证完成的条目。

## 1. 审查结论与状态校正

以下表格和证据诊断是实施前审查快照；R2/R1 的现状与本轮验证见第 3、5 节。

**总体方向合理，但原 G002–G010 不适合作为逐个一次性交付的故事。** 应保留长期目标，拆分版本覆盖、存储迁移、子系统规则和外部工具，并把正确性、安全与评测前置。

| 当前证据 | 不合理之处 | 调整（判断置信度：高，除另有标注） |
| --- | --- | --- |
| 本次检出没有 `docs/TODO.md`，但本地 Ultragoal brief 引用了它 | 执行入口断链；历史文档、运行状态容易被误当成实现事实 | 建立本文件；历史优化文档保留历史结果并链接到此 |
| G001 的摘要实现和专项测试存在 | 重新安排摘要开发会重复投入；局部精确匹配不等于全局实体隔离 | 标为存在，后续继续回归，不重新实现 |
| 本地 G002 状态为 `in_progress`，但没有 `anr_evidence/aosp_contract/`、`tests/test_aosp_timeout_ontology.py` 或 source-map | 不能把运行中状态、旧测试草稿说明当作已落地能力 | 按“未落地/待恢复”规划 B1；不推断其他工作树状态，不改写运行时状态 |
| `cross_source_fusion.py` 扫描文本命中即升级；`entity_linker.py` 使用 `term in line` | 现存错误归因风险应早于新本体、大存储和更多 Agent | 首先完成 R1/R2 |
| `ai_context.py` 提示仍有“除非证据极其充分”可改保守字段的例外 | 与项目固定保守契约冲突，不能拖到最后做文档一致性 | 纳入 R1 的生成提示与输出回归 |
| G002 同时要求 Android 14–17、规则包和 OEM overlay；当前只有旧分类器 | 范围过大、外部源码核验未闭环 | 单个已核验 profile 先行，其余逐版本验收；未核验明确 unsupported/unverified |
| G003 同时建七类表、迁移所有读取路径并验收 1 GiB | 尚未有消费者需求的 finding/probe 全表设计过早 | 先原始 artifact/span 引用，再一条流式链路；按消费者需要增表 |
| G008/G009 才安排引用、安全、评测 | 现有 LLM 路径已接受解析后的结果，晚加门禁会放大风险 | 引用/输出验证和隐私边界前置；高级路由后置 |
| G007 混合 Perfetto 与 WinScope 多类 adapter，G008 固定“八类角色” | 外部工具、时钟、schema 风险不同；角色数量不是验收结果 | 拆成 capability、单查询、独立图形 adapter；按覆盖和调用预算验收 |
| 原计划以 8–12 周和宏 F1 等作为完整 v1 目标 | 缺少冻结 corpus、逐类样本量、标注和机器基线，工期/准确率尚不可保证 | 保留为长期目标，不作为当前承诺；数据条件先验收（工期判断：中） |

证据入口：
- [摘要实现](../anr_evidence/input_window_evidence.py)、[精确匹配与 500+20+20 回归](../tests/test_input_window_evidence.py)、[产物/槽位保留回归](../tests/test_input_window_digest_integration.py)。
- [跨源升级](../anr_evidence/cross_source_fusion.py) `fuse_cross_source_evidence` 及 [现有测试](../tests/test_cross_source_fusion.py)：尚不接收目标身份；pipeline 测试甚至使用不同的 anchor PID / trace PID，不能作为归属正确的证明。
- [实体关联](../anr_evidence/entity_linker.py) `build_entity_map`、[分类器](../anr_evidence/extraction/classification.py) `classify_anr_type`。
- [加载器](../anr_evidence/loaders/core.py) `_read_archive_member` 全量 `read()`；[package 构造](../anr_evidence/loaders/package.py) 合并字符串；[ESS](../anr_evidence/evidence_slice.py) 枚举过滤后的行，不是原文件 byte-range。
- [LLM 路径](../anr_evidence/ai_agent.py) `_execute_re_probe` / `run_ai_agent_analysis`、[生成提示](../anr_evidence/ai_context.py)、[当前 hint 评测](../anr_evidence/eval.py)。
- [跨 trace hint](../anr_evidence/trace_preprocessor.py) 仍有“不再需要进一步复核”的表述；[AnrManager hint](../anr_evidence/anrmanager_parser.py) 仍有高 CPU/内存推测泄漏的表述。新增 verifier 不能放任旧输出旁路绕过门禁。

本地 `.omx/ultragoal/goals.json` 和 `2026-09-01-aosp-aligned-complete-anr-analysis-agent.md` 仅作为原任务编号与设计来源；本轮未修改它们。恢复原运行前，由具有该运行写权限的 leader 对照下述映射同步计划；不能继续把旧大目标当作已验证故事。本次不重验外部 AOSP SHA、符号或版本差异。

## 2. 已存在，保留而非重做

- [x] 存在：G001 Input/WMS/SF 有界摘要、三 family、多样性/去重、透明 relevanceScore、Input/no-focus 门控及产物保留测试。去重仅限摘要，分数仅表示检索相关性。
- [x] 存在：Phase 1–8、baseline/type-template additive extraction、按 ANR 分组和四个分析槽位。
- [x] 存在：多组 trace/logcat 预处理缓存、known-anchor 过滤、Meminfo 窗口、ESS/权重/ΔT/context flooding；不能再写成“从零实现”。
- [x] 存在：Multi-Agent、有限 re-probe、LLM 超时/重试、Replay、13 个 hint eval 用例和 CI。存在不等于已有类型化工具、引用验证或完整机制评测。
- [x] 存在：合成 ZIP/TAR/目录端到端测试；不等于有经授权、脱敏且人工标注的跨版本真实 corpus。

## 3. 推荐执行顺序与最小故事

先做 **R2 → R1**，再 **B1 → C1 → V1 → DQ1 → D1**。R1 中不依赖身份解析的保守提示修复可提前。S1/E1 从首个相关改动开始贯穿；S1 出站门禁通过前不得将真实私有日志送入远程 Agent，离线确定性路径继续可用。B2、C2、D2 和后续扩展分别按需要交付，不阻塞第一条可靠 Input 闭环。优先级表示风险/价值，不代表所有 P1 项必须串行完成。

### R1 · P0 · 跨源 promotion 与保守输出（原 G006/G008/G010 前置；身份部分依赖 R2）

- [x] 优化：为 fusion 补目标身份、group/time scope 和具体旁证引用；未知身份、仅相邻日志、其他进程信号只作为 context，不提升目标 hint。全局压力独立标记，不自动归为目标根因。
- [x] 优化：生成提示删除可修改三个保守字段的例外；区分 hint 强度与因果结论，删除“trace-only 可直接定根因”的无条件表述。不得删除原始证据。
- [x] 验收：同窗口无关 PID、anchor/trace 错 PID、缺失身份、窗口外 kernel 信号不升级；同实体且时间重叠的有效旁证保留规则允许的升级；记录命中原始行，不只有 regex 名称。
- [x] 验收：先补负例，再调整已有“无身份也升级”的测试为明确正/负例；保留多 group 缓存隔离；生成提示及确定性输出持续约束 `finalJudgment=false` / `notRootCauseYet=true` / `requiresHumanConfirmation=true`。
- [x] 优化：单独迁移旧 hint 旁路到证据质量门禁：跨 dump 同 TID 环必须校验 PID/时间/ANR 归属；CPU/RSS 单独只能支持压力线索，不能证明泄漏。新增 wrong-PID/stale/重复 TID/仅 CPU+RSS 回归，降级过强表述而不删除原始观察。

> R1 当前范围已验证：不带 scope 的旧 fusion 调用仍有效，但不再提升置信度；引用中的 `lineIndex` 属于过滤视图，不冒充原文件 byte range。远程 LLM 任意输出的强制结构/引用校验仍由 S1/V1 完成，完整 dump 生命周期仍由 DQ1/D2 完成。

### R2 · P0 · 结构化实体匹配（原 G004 的小切片）

- [x] 优化：替换 EntityMap 的数字/包名子串匹配；区分 log emitter 和消息中的 target，PID/TID/UID 分类型，未解析的关联显式 unknown。不扩成完整 token 图。
- [x] 验收：PID `123` 不匹配 `1234`、耗时或时间戳；`com.demo` 不匹配 `com.demo.other`；子进程名称采用明确规则；同号 PID/TID/UID 不覆盖彼此类型；重复运行排序一致。
- [x] 验收：复用摘要已有身份语义与测试案例，不直接把私有评分 helper 变成全局 resolver；跨组/多进程负例覆盖真实调用路径。

### B1 · P1 · 单 profile 本体与旧分类兼容（G002 第一部分）

- [ ] 新增：先核验一个 AOSP profile（原计划优先 Android 17）的不可变 revision/path/symbol，提交可离线验证的规则 manifest/source-map；分别输出 trigger、directBlocker、evidenceStatus，不从 trigger 推导 blocker。
- [ ] 验收：每个核验的 TimeoutKind 映射或明确 unsupported；legacy public type 不变；未知/矛盾/缺少 build version 安全降级；每条规则有 provenance 与匹配输入位置。原始 hash+byte-range 在 C1 补齐，不能把过滤行号伪称不可变引用。
- [ ] 验收：正例、歧义、unknown、provided_type 兼容测试独立通过；源码不能核验时记录缺口，不虚构 SHA，也不声称 Android 14–17 全覆盖。

### B2 · P2 · 多版本与 OEM 扩展（G002 余项）

- [ ] 新增：Android 14、15、16 各为独立 profile/验收；OEM overlay 为单独 story，只有明确厂商来源时启用，不默认覆盖 AOSP。
- [ ] 验收：版本差异逐项测试；没有已核验 profile 的输入明确 unsupported；overlay 冲突 fail closed。合成 fixture 只能证明规则行为，不证明真实设备兼容。

### C1 · P1 · 最小证据引用契约（G003 第一部分；V1/D1 前置）

- [ ] 新增：以 stdlib `sqlite3` 实现 artifact + span + group 及原始回读；按实际需求映射 ESS，暂不预建完整 finding/probe/图数据库。保留旧 package API。
- [ ] 验收：引用解析到原始 artifact hash + byte range，覆盖 UTF-8 多字节、CRLF、无末尾换行、同名 shard、重复内容、文件变更与越界；写明归档引用指向容器还是解压后的成员，不混淆两者。
- [ ] 验收：source/group 校验阻止跨 ANR 引用；旧产物/字段兼容，`logcat.txt` 和四槽位重生成不损坏。这个阶段不宣称达到大输入内存目标。

### V1 · P1 · 确定性输出/引用验证（G008/G009 前置；依赖 C1）

- [ ] 优化：在当前 Agent 结果接受边界验证结构、引用归属、保守字段和矛盾；不等八类 specialist 或 Perfetto 完成后再加入。
- [ ] 验收：非法 JSON/类型、伪造/越界/跨组引用、将日志中的指令当规则、提升保守字段均拒绝或输出明确 invalid 状态；原始证据保留。mock LLM 测试无需 API key。
- [ ] 验收：缺少身份、时间或直接 blocker 时无法输出高因果置信；`relevanceScore` / hint 强度不能直接充当因果置信。

### S1 · P0 · 输入与模型边界（G003/G009 前置，贯穿）

- [ ] 优化：为现有全量归档读取增加成员数、单项/总解压字节预算与明确错误；未来落盘引入路径/符号链接边界测试。当前 archive loader 读取成员而不 extractall，不能误报已证明 Zip Slip。
- [ ] 新增：发送模型前的脱敏 view、保留引用的替换映射、日志不可信数据边界和模型调用预算；原始 baseline 留在本地，拒绝/超限不得静默丢证据或泄露 secret。
- [ ] 验收：在现有结果接受点先校验 JSON 结构/字段类型/保守字段，无需等 C1；HTTP mock 检查实际出站 payload 无测试 secret/PII，不能仅检查脱敏 helper。无法证明安全的真实数据外发路径明确拒绝；V1 再接入完整不可变引用验证。
- [ ] 验收：压缩炸弹合成小 fixture、异常编码、危险成员名与 link、secret/PII、恶意日志指令均有负例；离线 baseline 不依赖外部调用。只生成采集建议，不自动执行模型/日志给出的 shell、SQL 或设备命令。

### E1 · P1 · 分层评测与真实 corpus 准入（G009 前置，贯穿）

- [ ] 优化：在 D1 前冻结第一版评测 schema，复用现有 eval，分别统计 trigger、blocker、evidence status、引用、矛盾和高置信违规；每次规则变更补正例和反例。声明哪些输出被完整标注，不能把未列入 forbidden 的额外输出当作已证明正确，也不用“13 个 hint 测试通过”代替端到端正确率。
- [ ] 新增：真实样本需来源许可、脱敏校验、版本/设备信息及人工标注；按事故/设备来源隔离开发集与评测集，冻结 corpus revision 与各类最低 support 后再启用统计阈值，报告各类样本量、缺失标签和 unsupported，不强行塞入真实私有日志。
- [ ] 验收：已支持范围内引用完整性 100%、高置信门禁违规 0、对抗集关键 trigger 误报 0；没有样本的类别报 N/A。macro-F1 ≥0.95、top-3 ≥0.90 保留为冻结 corpus 后的目标，未满足数据条件不得宣称达标。

### DQ1 · P1 · 共享 dump 身份与质量边界（G004/G006 共用；依赖 R1/R2/C1/V1）

- [ ] 优化：在 R1 已收紧的判定上统一 dump PID、采样时间、ANR 归属及 stale/wrong-PID/unknown 状态，返回支撑引用与缺口；由 D1/D2/F2 复用，不另建平行规则。
- [ ] 验收：跨 dump 同 TID、PID 复用但时间不重叠、缺时间、晚采样、错组引用均无法满足高置信门槛；没有证据时状态 unknown，不凭统一固定延迟推断所有版本 stale。完整 queue/suppression 生命周期仍由 D2 增量接入。

### D1 · P1 · 最小 Input 证据闭环（G004 第一部分；依赖 B1/C1/R1/R2/V1/DQ1/E1）

- [ ] 优化：先区分 no-focus / dispatched-but-unacknowledged；建立 anchor、原 target、blame target 和 dump PID/time 的明确字段，仅对有证据的转移建边。
- [ ] 验收：no-focus、unacked、错 PID、迟到 dump、恢复/取消、未知身份各有 fixture；缺少 WMS 映射不得猜 blame；纯 log/trace 无 Perfetto 也完成保守报告。
- [ ] 验收：三个专项结论分别写入 Trace / EventLog / Logcat-AnrManager 槽位，综合结论只写入 `#### AI Analysis — 最终 ANR 综合分析`；不是把同一综合结论复制四次。

### C2 · P2 · 单路径流式迁移与资源预算（G003 余项；依赖 C1/S1）

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

### R2/R1 实施结果（2026-09-05，本地未提交）

- R2：新增 `log_identity.py` 结构化身份解析；EntityMap 取消数字/包名子串匹配，区分 emitter/target 与 PID/TID/UID，查询支持 `entity_type`；selected trace 的引用只来自选中的完整 dump，压缩展示不会丢失实体库存；真实 Agent 路径为每个 ANR 构建独立 map 并标注 group，HTTP mock 已验证实际请求中的 PID/TID/UID 不串组。Trace 行号仍对应输入原文，其他来源位置属于传入的组内过滤视图。
- R1：fusion 要求目标与 trace 的身份、时间、group 一致；同组 AnrManager 可补充 legacy anchor PID，冲突则只保留 context。记录具体原始行、拒绝理由及过滤视图位置，每条规则/来源的旁证与 context 各有上限。
- 旧结论旁路：高 CPU/RSS 仅作为 pressure clue；修复生成提示残留的泄漏推断；跨 dump 相同 TID 还须匹配锁边/对象，区分 anchor 窗口与采样间隔，NaN/inf/负窗口拒绝。超出配置窗口不等于已证明 stale。
- 兼容变化：`consolidate_deadlock_across_traces` 的旧无身份调用保留单 dump 观察但不升级；远程 Agent 遇到重复 group ID 会在发送前明确拒绝，避免默默覆盖实体 map；消费端需从 `DEADLOCK_CYCLE_CONFIRMED` / `confidence=confirmed` 迁移为 `DEADLOCK_CYCLE_CORROBORATED` / `confidence=strong`，仍须人工确认。详见 [trace hint 迁移说明](trace-hint-annotation-design.md)。
- 没有修改原始 baseline、CLI 入口或四槽位布局；未新增第三方依赖，未修改 `.omx/ultragoal` 运行时状态，未提交或推送。

### 验证证据

- `python3 -m unittest discover -s tests -v`：运行 352 项，351 通过、1 个可选样本跳过（实施前为 321 项）。
- `python3 scripts/run_eval.py tests/fixtures/eval`：13/13 hint 用例通过；正例补齐真实格式的 PID 身份，新增错身份/缺身份/跨窗口反例；这不是跨版本机制评测结果。
- `python3 -m compileall -q anr_evidence tests scripts`：通过。
- `python3 scripts/benchmark_input_window_digest.py`：13 MiB、6 group、1 轮预热/5 次测量；digest 开启相对关闭 wall 中位数变化 -0.37%，峰值 RSS 中位数 +0.09%，产物/事件及 15%/10% 阈值检查通过。该基准比较当前版本 digest 开关，不是与旧提交比较，也不证明 1 GiB 达标。
- 回归补测先复现了 selected trace 同 TID 串 dump、生成提示旧泄漏结论、legacy anchor 忽略有效 AnrManager PID、yearless 旁证未使用明确 anchor 年份，以及真实 Agent 全局实体 map 串组、压缩后 TID 库存缺失、重复 group ID 静默覆盖；修复后通过。
- Markdown 本地链接/任务标签与 `git diff --check`：通过；独立审查发现的窗口绕过、身份补全、旧 hint 文档及 Agent 集成缺口已逐项补测修复；最后的集成复核由主代理完成。
- 未运行外部 LLM、设备/真实 corpus 验证，未核验 AOSP 来源。没有独立 lint/typecheck 配置，不新增工具依赖来制造验证记录。

后续仍按 **B1 → C1 → V1 → DQ1 → D1** 推进；S1/E1 的前置约束不变，不得将 R1 的提示修复等同于 S1 已具备出站安全保证。

- [ ] 验收：每个后续代码故事先补针对性回归，再实现，执行专项 → 全量 unittest → compileall → diff 检查；适用时补 eval/benchmark/真实样本，明确 skip 与外部门禁。
- [ ] 验收：baseline、CLI/产物兼容、四槽位保留、三项保守字段持续成立；只有证据与复核齐全才更新任务完成状态。
