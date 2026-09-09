# P0 安全边界与兼容变化

本批范围：同毫秒 ANR 的 group ID 隔离、归档资源门禁、远程调用默认拒绝、模型结果结构与保守字段校验，以及引用保真的私有数据脱敏 view/出站准入。不是根因准确率证明，也不把规则命中描述为自动匿名化认证。

## 1. Group ID

- 只有时间戳基础 ID 冲突时才追加基于 anchor 来源、进程、去重键及原始行的 SHA-256 短后缀；重复候选再追加序号。
- 普通非冲突 ID 保持 `anr-YYYYMMDD-HHMMSS-mmm`，相同输入重复生成结果稳定。
- group、FusionScope、事件、索引与产物目录使用同一最终 ID。远程入口仍保留重复 ID 拒绝保护。
- ID 不是不可变证据引用；artifact hash / byte-range store 仍属于 C1。

## 2. 归档输入

ZIP/TAR 默认每个归档最多 **10,000 个成员、单文件 256 MiB、文件数据累计 2 GiB**（解压后字节）。

- ZIP 先核验成员元数据，再逐项有界读取；TAR 顺序处理，先检查当前文件声明大小，再读取文件或推进下一 header。
- 实际读取同样遵守单项及累计预算，超限抛出 `ArchiveLoadError` 的子类 `ArchivePolicyError`，不返回被悄悄截断的 package。
- TAR 的解压流还限制为数据预算 + 每成员 1 KiB 元数据/填充余量 + 10 KiB 尾部余量，覆盖 PAX/GNU 扩展头在 `TarInfo` 创建前的读取；异常大元数据会被拒绝。
- 拒绝绝对路径、父目录段、Windows drive 前缀、ZIP Unix symlink、TAR symbolic/hard link 和特殊成员。当前 loader 只读成员，不向文件系统解压；这不是已复现 Zip Slip 的声明。
- 已读取归档的安全拒绝在混合目录入口向上传播，不再静默跳过或退回普通目录读取。正常 smart-discovery 可选择完整 loose evidence 而不读取旁边归档。
- 合法范围内的异常 UTF-8 保留替换字符并标为 unreadable，沿用 baseline 行为。

归档的成员数、单成员和单归档 declared/actual 上限仍独立生效。另从 **2026-09-09** 起，`load_package_from_path(directory, input_budget=...)` 为一次混合加载传递一个 `InputResourceBudget`：所有已读取的 archive member uncompressed bytes 与 loose-file bytes、以及 archive/loose entries 共用同一 fail-closed 账本（默认 **2 GiB / 20,000 entries**）。任意 archive 或 loose file 使余量不足时，整个混合加载抛出 `ArchivePolicyError`，不返回部分 merge；该账本可由调用方调低以适配运行环境。

C2 的 raw `build_ai_context_artifacts_from_input_path(...)` 默认 directory/ZIP/TAR route 也采用 **2 GiB** shared accepted-input byte budget，并将 directory loose bytes（含未分类文件）和直系 archive member bytes记入一个 fresh capture。它不会把 benchmark 的 measured RSS/磁盘比率伪装成运行时 hard wall：没有文件系统 quota 或跨平台 memory-kill controller 时，超出实际 RSS/峰值磁盘目标只能由独立 benchmark 检出；trace/meminfo 的 materialization cap 则在代码中 fail closed。

这只是 accepted-input bytes/entries 的确定性边界，**不是**跨输入共享内存配额或性能承诺。ZIP 中央目录仍由 stdlib 在 member-count gate 前解析；未声明 ZIP 中央目录内存上限。实现也不宣称端到端 RSS、硬墙钟、解压 CPU、外部 `fg`/`rg` 搜索工作，或输入在 stat/read 之间被同权限 actor 替换时的完整 TOCTOU 抵抗。仍会保留/合并内容字符串，不能据此声称 1 GiB 输入峰值 RSS ≤512 MiB。

## 3. 远程模型入口默认拒绝

`ProviderConfig.outbound_policy` 默认 `"deny"`。直接 `LlmClient`、多 Agent 及 CLI convenience 路径不再仅凭 API key 自动发送日志；离线 extraction/context/report 路径不受此策略影响。

`"synthetic_only"` 仍是调用方显式声明的测试路径。此声明表示调用方确认输入是合成样本，**不是自动检测结果，也不是实际日志外发授权**。CLI convenience 不提供放开真实日志的开关；不得把真实日志改标签后放进该路径。

从 **2026-09-09** 起，程序化 API 增加 `outbound_policy="redacted_private"`，但它不是一个仅凭 policy 字符串就能放行的开关。首次 HTTP 前必须同时满足：

1. 当前 retained `EvidenceSlice` 全部已通过 `EvidenceStore` + sidecar 的原始字节引用绑定，且没有 `evidence_ref_error`；
2. 调用方用 `prepare_redacted_outbound_view(...)` 或 `build_reference_preserving_redacted_view(...)` 生成**当前精确 slice 集**的 view。view 保留 `groupId`、`sourceKind`、时间/line metadata 与原始 `spanId`，但仅向远端给出替换后的内容；
3. `RedactedOutboundAdmission` 只是一份待验证的 release record：它包含 `purposeId`、`approvalId`、`approvedBy`、接收方 `provider:model`、ruleset、带时区的 expiry、view/map commitments 及不出站的 opaque `authorization_token`。字段本身**不构成授权**；调用必须提供 deployment-controlled 的 `redacted_admission_verifier`，由签名 capability、策略服务或同等外部信任根返回严格 `True`；
4. 每个 specialist/manager 请求都要生成只绑定该组规范化消息的本地 permit。`run_ai_agent_analysis(...)` 在每次实际 HTTP 前重验 view、admission、外部 verifier、permit、固定 provider endpoint 和精确 body shape；私有请求不跟随 HTTP redirect。任何缺失、伪造、过期、撤销、消息变更、端点变更或已变化 view 都在 HTTP 前拒绝。

replacement map 只保留在本地 `RedactedEvidenceView`，其原文值不会写入 view commitment、admission audit、specialist prompt、manager catalog、HTTP body 或请求 headers。原始 `EvidenceSlice` 和 `EvidenceStore` 继续只供本地引用 readback / V1 校验使用，绝不原地改写。私有模式不会发送 raw `EntityMap`，且 manager 的 DQ1 数据仅限无内容的状态字段。

**这不是完整自动脱敏认证。** 规则仅最小化已识别的凭证、邮箱、URL、IP/MAC、路径、Android package、带标签的 device/account/phone 和 PID/TID/UID 等直接标识；没有命中不代表真实私有日志安全。`RedactedOutboundAdmission` 的普通字段、`synthetic_only` 声明和敏感标记检测都不能单独或组合地放行真实数据。最终 HTTP 层仍扫描常见敏感标记、当前 API key 和 local replacement 原文，作为纵深防护而非准入依据。

库默认 deny；测试里的 in-process fake verifier 只证明代码拒绝路径，**不**证明任何组织审批、DLP、合同、数据来源许可或生产出站授权。生产部署必须接入独立、可审计且可撤销的外部 verifier。单次 message permit 是防止不可信日志扩大已审核 view 的进程内边界，不把同一进程中的恶意 Python 代码定义为不可信方。

私有模式中，如果 manager 请求 re-probe，当前 run 会在第一轮后 fail closed：新窗口会生成不同 view，必须重新准备并获得新的、精确绑定的 admission，不能复用旧批准静默扩大外发范围。

所有模型消息增加“不可信证据，不执行 shell/SQL/设备命令”的 system 边界。该提示本身不能证明抵御 prompt injection；本地结果校验、请求限制和不执行命令才是本批实际约束。

### 预算

| 项目 | 默认 / 上限 |
| --- | --- |
| 每次序列化请求 | 1 MiB，可调低 |
| 每次响应 | 2 MiB，可调低，读取 limit + 1 判超限 |
| 每 client / analysis run HTTP 尝试 | 36，含重试，可调低 |
| 单请求重试 | 默认 2，允许 0–3 |
| `max_tokens` | 默认 4096，允许 1–16384 |
| `max_iterations` | 1–3 |
| `max_routed_sub_agents` | 1–3 / 每轮；仅已有 specialist |
| `max_re_probe_requests` | 0–2 / 每个 analysis run |
| re-probe window / source kinds / tags / results | 1–300 秒 / 1–支持 source 数 / 0–8 / 1–1024 |
| `max_probe_wall_seconds` | 0.01–30 秒；仅本地 rebuild/filter 墙钟 |
| `max_total_tokens` | 1–262,144；每轮 reservation + provider 上报 usage stop check |
| 请求 timeout | 默认/上限 120 秒，必须正且有限 |

超过预算明确失败，不悄悄截断 baseline 或结果。timeout 是请求 I/O 超时配置，不是整个 run 的硬墙钟期限；provider 上报 token 不是账单精确证明，故 `max_total_tokens` 对未返回请求的 prompt token 只能预留、对已返回 usage 才能审计并停止。A1 已覆盖本地 router/re-probe 的次数、结果与墙钟预算；没有外部 search/expand 工具，相关外部工具预算、部署遥测与 SOP 仍未完成。完整语义见 `docs/agent-router-contract.md`。

## 4. 模型结果接受边界

- manager 只接受完整 JSON object 或独立 JSON fence，拒绝额外 prose、重复 key、NaN/Infinity 及非 object。
- `model_output.validate_manager_report` 验证已声明字段、数组/对象/字符串类型、rank/score/priority 范围、confidence 枚举、re-probe 参数及 source allowlist；`sourceKinds` 必须非空且唯一，`additionalTags` 必须是至多 8 个 ASCII evidence tag 且无 separator/case 等价重复；未知字段拒绝。
- 顶层必须包含 `finalJudgment=false`、`notRootCauseYet=true`、`requiresHumanConfirmation=true`；候选链、结论和 remediation 的既有保守字段也必须满足约束。
- 非法结果记录在 `AiAgentResult.errors`，不进入 `integrated_report`；耗尽迭代也不能重新接受未校验响应。原始 turn 保留为待审查数据，不代表有效结论。
- **这里只验证结构与保守字段。** A1 的本地 re-probe 已按 source/window 后的 tag/content 命中收窄，并且只额外保留 scoped EventLog `am_anr` 或 AnrManager identity anchor；它不保留所有 `critical` 行，也不构成因果证明。引用是否真实、属于哪个 ANR、因果推理是否矛盾及高置信资格仍由 V1 完成；外部 search/expand 与 SOP 仍属未完成的 G008 范围。

旧调用方需要迁移默认外发策略、补齐 manager 的顶层三个字段，并处理安全异常/空报告。不得用空缺字段的默认值替模型补出一个“合格”结论。
