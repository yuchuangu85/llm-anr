# Android ANR AI 分析指南

本指南是 `scripts/anr_to_ai.py` 或 `python3 -m anr_evidence --build-ai-context <输入>` 生成的 AI 工作区的统一分析说明。
其中 `anr_analysis.md` 从 `# ANR AI Context Cache` 开始，只保留当前 ANR 的证据、过滤日志和四个分析槽位；请先阅读本指南，再填写对应工作区中的分析内容。每个 `anr_ai_context/<group-id>/` 还默认输出独立的 `anr_reconstruction.md` 和 `anr_reconstruction.json`，不改变原四个槽位与 `logcat.txt`。`index.json` 通过各组的 `artifactPaths.reconstruction` / `artifactPaths.reconstructionJson` 暴露重建路径。

## 分析原则

- 只基于工作区中的 Trace、EventLog、Logcat、AnrManager 和 Meminfo 证据分析，明确区分证据、推断和缺失信息。
- 先识别 ANR 类型和直接触发条件，不要把 `input_dispatching_timeout` 的结论套用于其他类型。
- `rootCausePatternHints[]` 只是候选提示，不等于最终根因；必须用多来源证据交叉验证。
- AnrManager 的 `Reason`、`Load`、PSI、`CPU TOTAL`、`CPU >90% processes` 和 `Top CPU processes` 优先于仅凭 Trace 的自由推断。
- 如果来源缺失、过滤为空、时间不一致或使用回退锚点，必须在“证据质量”中明确说明。
- 所有结论保持保守：`finalJudgment=false`、`notRootCauseYet=true`、`requiresHumanConfirmation=true`。

## Trace 线索和死锁判断

- `### 死锁检测` 是程序化锁图分析结果，可信度高于仅凭栈帧推断。
- `[DEADLOCK_CYCLE / strong / critical]` 表示等锁环成立且环上节点均处于阻塞状态，可以作为高强度根因候选。
- `[DEADLOCK_LIKELY]`、`[DEADLOCK_SELF]`、`[LOCK_OWNER_BLOCKED]`、`[LOCK_OWNER_SLEEPING]` 和 `[LOCK_CONTENTION_BLOCKED]` 必须结合完整线程状态说明，不能直接升级为确定根因。
- Trace 中的 `▸ HINT[...]` 是程序化注解，不是原始 AOSP 日志；可以引用，但要标明其性质。
- `NATIVE_POLL_BUT_BUSY` 表示主线程累计运行量较高，禁止简单判定主线程空闲；`NATIVE_POLL_IDLE_LIKELY` 只表示可能空闲，仍需结合其他来源。
- `MAIN_BINDER_WAIT_REPLY`、`MAIN_SP_APPLY_WAIT`、`MAIN_IO_BLOCKED`、`MAIN_DB_BLOCKED`、`MAIN_NETWORK_BLOCKED`、`MAIN_GC_PAUSED` 和 `MAIN_RENDER_WAIT_FENCE` 命中时，应继续查对应服务端、锁持有者、I/O 或渲染证据。

## AnrManager 负载分析顺序

1. 先看 `Load` 和 PSI，判断 CPU、I/O、内存压力。
2. 再看 `CPU TOTAL` 和 `iowait`；`CPU TOTAL >=90%` 时标记整机/任务负载重。
3. 列出所有 `CPU >90% processes`，区分目标包自身高负载和外部系统进程影响。
4. 如果目标包 CPU 高于约 85%，要明确标记应用自身负载过高，并结合目标包 Meminfo、GC、LMK/OOM 和 ANR 元数据。
5. 如果缺少目标包 Meminfo，不要把“没有证据”写成“已排除内存问题”。

## 四阶段分析顺序

按以下顺序填写当前 `anr_analysis.md` 中的四个槽位：

1. `#### AI Analysis — Trace 堆栈`
   - 总结主线程状态、直接阻塞点、相关线程、锁持有者/通信对端、堆栈提示、缺口和置信度。
2. `#### AI Analysis — EventLog 事件日志`
   - 以 `am_anr` 为 ΔT=0，列出 ANR 前窗口的生命周期、焦点、输入、进程和状态机变化。
3. `#### AI Analysis — Logcat / AnrManager`
   - 读取同目录的 `logcat.txt`，说明触发日志、窗口/焦点/Surface 序列、转储生命周期、负载、PSI 和 Meminfo。
   - 过滤规则与 EventLog 对照见 [Logcat 过滤配置参考](logcat-tags-reference.md)；普通关键词窗口、AnrManager 完整块与原始前置上下文不能混为同一种过滤规则。
4. `#### AI Analysis — 最终 ANR 综合分析`
   - 跨源汇总并按下方固定标题输出最终报告和 JSON 尾部。

## 独立证据重建与刷新

独立重建默认先基于日志生成；AI 槽位未填不阻止 `anr_reconstruction.md` / `anr_reconstruction.json` 输出。它们是证据驱动的 ANR 过程重建，不是真实执行重放；现有 `replay` 命令仍用于回归重跑。

每个 ANR 分组独立保留 `<group-id>/anr_reconstruction.md`，不生成跨组汇总的重建 Markdown。紧凑展示不改变产物路径、组隔离或完整数据保留契约：

- `anr_reconstruction.md` 最多展示 24 个关键时间线节点、简短证据摘录与来源坐标、最多三条未验证候选摘要，以及重要缺口/警告。连续、内容相同且时间可解析的记录合并为首末观测端点和次数；不同身份、存在中间观察或时间未知的记录不合并。端点不代表期间状态连续成立，更不是阻塞时长证明；未展开记录仍在 JSON 中。
- `anr_reconstruction.json` 保留全部观测事件、证据引用、AI 原文和关系，包括 Markdown 省略的细节；Markdown 的去重与精简不删除 JSON 记录。
- Markdown 不逐条重复所有原始日志或堆栈、每条关系边、四个 AI 槽位全文或候选 JSON。完整分析仍在 `anr_analysis.md`，原始日志仍按既有路径查阅；候选摘要始终未验证，不因精简而改变保守标志。

- `observed`：日志或原始证据实际记录的观察，不扩展为未观测的执行历史。
- `inferred`：机制解释、跨事件因果假设或 AI 候选解释，必须与观察分开，引用需核验。
- `unknown`：缺失、矛盾、身份或时间无法对齐等缺口，不能用常见时限或经验补全。
- JSON 逐事件保留时间、同一可比较时钟域内相对 ANR 锚点的 ΔT、进程/线程/窗口等身份和原始证据定位；Markdown 只摘录关键里程碑。无法确认的字段明确标为缺口，不编造时间或身份；不可比较的时钟域不跨时钟排序或计算 ΔT。
- 时间先后不等于因果。没有等待起点或实际时限证据时，不能用 `am_anr - 5s` 补造过程；单份转储或堆栈只说明采样时状态，不能证明整个超时窗口持续阻塞。

四个 AI 槽位全部填完后，必须运行以下命令刷新独立重建产物，而不是重新提取输入：

```bash
python3 scripts/reconstruct_anr.py <context-root|group-directory|anr_analysis.md>
# 可选：将外部 AI 报告纳入未验证候选解释
python3 scripts/reconstruct_anr.py <context-root|group-directory|anr_analysis.md> --ai-report <JSON>
```

上下文根目录会处理其中每个组；组目录或 `anr_analysis.md` 路径只处理对应组。刷新不重新提取证据，也不改写四个 AI 槽位。`--ai-report <JSON>` 不是观测来源：外部报告及其引用必须回到当前组原始证据核验，不能因导入或引用核验就把候选解释升级为 `observed` 事实。

多组导入外部 AI 报告时，使用 `{"groups": [{"groupId": "<目标组 id>", "candidateChains": [...]}]}` 显式归属；不把一个未指定组的报告复制给所有 ANR。引用使用当前 `anr_reconstruction.json` 中的真实 `eventId`，或唯一精确匹配的完整 `rawLine`。不存在/不唯一的引用记为 `invalid_references`，引用存在也不证明因果成立。

刷新以最近一次提取的事件为基础，并校验工作区证据摘要；若手动修改了过滤日志或锚点，刷新会要求重新运行原提取命令，以免沿用过期事件。修改 AI 槽位不会触发重新提取，产物内的引用行号会随正文位置更新。来源行号基于加载后的逻辑文本，多个归档分片合并时不冒充某个物理文件的行号。

同毫秒但身份不同的事故使用稳定 hash 后缀区分。复用输出目录而更换输入时，会校验 `inputIdentity` 并另建组目录，旧目录和人工分析保留、不迁入新事故；当前 `index.json` 只索引本次输入。相同输入的证据变化时保留原 AI 槽位，但 `analysisRevision` 标记为 `stale` 或 `unknown`，重建文档明确提示旧解释未在新证据版本上重新核验。

可选导出逐行证据与相对时间：`python3 scripts/anr_to_ai.py <输入> --build-ess --delta-t-normalized`，分别生成组内的 `evidence.jsonl` 和 `time_normalized.json`。它们不执行墙上时钟与开机运行时钟（uptime/boottime）的跨时钟校准；重建文档会将不可比较时钟域分开。

最终交付保留写入四槽位的 `anr_analysis.md`，并提供每个组已刷新的 `anr_reconstruction.md` 和 `anr_reconstruction.json` 路径；独立重建不能取代最终综合分析槽位，也不能只在聊天中给出重建结论。正式交付还须按下节 finalize，提供逐组 `acceptance.json` 路径、就绪状态与限制；草稿和失败组须单独标明。

<a id="report-acceptance-and-finalization"></a>

## 统一报告验收

统一验收由 `report_validation.py` 与 `acceptance.py` 实现，工作区、Manager 和 Phase 7 分别适配各自的数据形态。实现清单与验证证据见 [report-acceptance-design.md](report-acceptance-design.md)；验收通过仍不代表现场根因已经确认。

### 填写完成与交付就绪

`analysisComplete` 保持原义：四个 AI 槽位已填写。它不是证据质量、报告有效性或可交付性的判断。最终槽位仍为 `#### AI Analysis — 最终 ANR 综合分析`，保留原 `final-anr` marker，不新增第五槽位或额外 Markdown。`acceptance.json` 是正式收尾时生成的机器回执，不是第五份分析。

规则计算 `deliveryReadiness`，优先级从高到低为：

- `blocked`：结构、事故身份、支撑引用、证据版本或产物一致性有硬错误，不可交付。
- `needs_review`：机器检查通过，但特定关键断言缺少有效复核，不可交付。
- `ready_with_limitations`：缺口与停止原因已披露，受影响断言已降级，可带限制交付。
- `ready`：当前规则的全部必需检查通过，无已声明的重大交付限制，可交付。

未运行的必需检查不能算通过。`ready` 与 `ready_with_limitations` 都只说明当前内容满足交付规则；当前版本不裁定根因真值，不证明修复生效或事故已复现。所有报告仍保留 `finalJudgment=false`、`notRootCauseYet=true`、`requiresHumanConfirmation=true`；模拟步骤仍是候选，`validation=unverified`、`executed=false`，不因验收通过而变为已执行。

### 只读验证与正式收尾

提取、草稿生成和重建刷新允许槽位为 pending，不要求预先存在回执。四槽全部填完后，先运行上节的重建刷新，再执行 finalize；只想检查现有产物时可先做只读验证：

```bash
# 只读：校验已有内容，不重新提取、不刷新、不写回执或 index.json
python3 -m anr_evidence --validate-report <context-root|group-directory|anr_analysis.md>
# 显式写入：刷新重建、验收、写逐组 acceptance.json，更新索引就绪度
python3 -m anr_evidence --finalize <context-root|group-directory|anr_analysis.md>
# 可选：两个入口都接受同一份逐问题复核记录
python3 -m anr_evidence --validate-report <context-root|group-directory|anr_analysis.md> --review-record <JSON>
python3 -m anr_evidence --finalize <context-root|group-directory|anr_analysis.md> --review-record <JSON>
```

上下文根目录选择索引内所有组；组目录或分析文件只选择对应组，不把单组成功当成工作区全部通过。两个入口均输出机器可读 JSON，明确逐组状态、阻断项、复核项、限制及处理失败；任一所选组不可交付或处理失败时退出码非零。`ready`、`ready_with_limitations` 为可交付状态，后者必须随报告披露限制。批量允许部分组成功，但不能隐藏失败或把已有成功回执套用于其余组。

finalize 不改写四个 AI 槽位，也不重新提取输入；它必须在同一内容快照上刷新并验证，写入前重新检查依赖内容，中途修改不能得到成功回执。证据摘要不匹配时应先按原流程重新提取和复核分析，不能靠 finalize 刷新绕过。只读验证也不能自动修补过期或缺失产物。

### 逐问题复核与证据边界

`--review-record <JSON>` 向验证器或 finalizer 传入外部复核记录；API 对应 `review_records`。每条记录针对具体问题，包含可识别的 `reviewer`、问题及组的 `scope`，并绑定当前 `evidenceRevision` 与 `analysisDigest`。记录只处理匹配范围内的复核项，不能跨问题、跨组或跨内容版本复用。模型 JSON 自填“已通过”、就绪字段或自称 reviewer 不构成有效复核，复核也不授予模型最终裁决权。

- 四句笼统文字、未替换模板、空 JSON、重复槽位、围栏代码块中的假标题不能代替完整报告；四槽 filled 仍可能 `blocked`。
- 不存在、不唯一、跨组或错误版本的引用若用于支撑当前断言，属于硬错误；不能仅在“证据质量”列出它却保留原结论。引用解析成功也只证明可定位，不证明因果或内容真实。
- 旧证据上的分析若仍被当作当前结论支撑，或重建与分析、证据版本、依赖产物不一致，均硬阻断。复核记录不能覆盖硬错误；须修正引用、复核或收敛断言，并重新刷新验收。
- 缺 Trace、来源为空或根因未知不自动阻断。对应来源槽位仍须写明“本来源无有效结论”、具体缺口及补证计划，最终槽位与 JSON 同步保留限制并降级相关断言，才可能 `ready_with_limitations`，不能伪造线程、持续时长或根因来换取通过。
- 未决补探须说明缺什么、已尝试什么、为何停止及下一步采集核验；不能将未执行检查当成功，也不要求无限补探。结构化模型/模拟步骤仍遵守下文的所有类型契约。

### 回执与公开 API

每组 `acceptance.json` 绑定该组身份、验收规则版本、`evidenceRevision`、`analysisDigest` 以及依赖产物内容摘要，记录检查结果、硬错误、复核项和限制。回执与索引不纳入自身内容摘要；`index.json` 只聚合逐组就绪度与路径，不成为根因真值来源。finalize 前没有回执不妨碍草稿或只读预检；正式交付需有与当前内容匹配的回执，不能只检查文件存在。

返回值 `deliverable` 表示当前候选满足交付门槛；`finalized` 表示所有选中组还具有匹配当前内容的成功回执。`readyGroupIds`、`blockedGroupIds`、`reviewGroupIds`、`finalizedGroupIds` 分开列出，避免把只读预检当成已经发布回执。

旧分析的证据版本为 `stale`/`unknown` 时，重新提取和刷新本身不会清除旧支撑。作者须实质更新分析，并在最终 JSON 中增加与索引一致的 `evidenceRevision`，显式声明核对了当前证据；只改排版不算重审。此声明不证明因果，只将更新后的内容作为 `unverified` 候选重新验收。

外部复核文件格式为 `{"reviews": [...]}`。每条记录须包含 `source="external-review"`、`reviewer`、`scope`、`rationale`、`groupId`、当前 `evidenceRevision`/`analysisDigest`、具体 `issueIds` 和 `decision="accepted_with_limitations"`（或 `accepted`）。这是一份由调用者显式提供的本地复核声明，不是密码学身份认证；硬错误不能通过复核豁免。成功且内容完全匹配的回执才可复用已记录的复核。

Phase 3 起保留 `evidenceCatalog`、`evidenceIdentity`、`evidenceRevision` 并透传至 Phase 7，用于校验身份和支撑引用。旧包若仅携带片段，按 `LEGACY_SNIPPET_PROVENANCE` 披露无法回查的限制；不能用新增片段绕过已携带的可信记录清单。`render_final_delivery(..., draft=True)` 仅供诊断并明确标记 `not_ready`；CLI 的 `--report` 保持草稿入口。

严格 JSON 报告解析上限为 4 MiB，拒绝重复键、非有限数值、尾随数据和非法 Unicode；工作区单个依赖文件的验收读取上限为 64 MiB。超过上限会返回明确失败，不截断证据后冒充通过；这不改变原始日志加载和草稿提取规则。

编辑证据、分析或依赖产物会使旧回执失效，需重新验收；仅排版变化也需重验，但不因此直接断言结论已过期。证据版本变化还须处理旧分析支撑和复核记录的有效性。回执不能跨组重放，成功组不能掩盖失败组。

包 API 的工作区入口由 `acceptance.py` 提供，与 CLI 使用同一规则：

```python
from anr_evidence import finalize_workspace_reports, validate_workspace_reports

validation = validate_workspace_reports("anr_ai_context", review_records=None)
# 仅需正式收尾时调用；会刷新并写回执和索引。
acceptance = finalize_workspace_reports("anr_ai_context", review_records=None)
```

签名为 `validate_workspace_reports(path, *, review_records=None)` 与 `finalize_workspace_reports(path, *, review_records=None)`。共享纯校验内核 `validate_manager_report`、`validate_phase_report` 分别适配 manager JSON 报告和确定性阶段报告，不进行文件写入，不强制内存报告满足 Markdown 四槽位或磁盘产物要求。

确定性 `--deliver` 对 Phase 7 包执行统一交付门禁；`--report` 继续允许输出草稿，不受正式交付门禁阻止。manager 的提示约束、重建导入的引用解析、确定性草稿成功均不等于已通过各自的统一验收，运行时接入须分别核验。

<a id="final-anr-固定输出"></a>

## 最终 ANR 固定输出

最终槽位必须包含以下 Markdown 标题，且每节至少包含证据、专项结论或明确的 `_无保留证据_`：

- `## 综合分析结论`
- `## 时间线`
- `## Trace 证据分析`
- `## EventLog 证据分析`
- `## Logcat 与 AnrManager 证据分析`
- `## 直接阻塞点`
- `## 候选根因链`
- `## 超时输入事件`
- `## ANR 发生模型`
- `## 模拟操作步骤`
- `## 证据质量`
- `## 修复建议`

候选根因链按置信度排序，明确区分：触发类型 → 直接阻塞点 → 上游诱因 → 责任边界 → 证据强度。直接阻塞点只能写证据直接支持的内容；例如 `focused window` 缺失可以作为直接阻塞点，但不能在没有对应栈的情况下虚构某个业务函数。

### 所有类型的模型与模拟步骤

所有 ANR 类型都必须填写 `## ANR 发生模型`、`## 模拟操作步骤`，仍位于原 `final-anr` 内，不新增第五个槽位。两节及 AI 数组 `anrModel`、`simulationSteps` 必须包含候选内容或具体缺口与补证计划，不能因为非 input 而写不适用或一律留空。只有 `## 超时输入事件` 是 input 专项：非 input 在这一节写“不适用（input 专项）”，`inputEventAssessments=[]`。unknown 不猜成 input，明确分类、触发点与责任边界缺口并给出补证计划。

- **超时输入事件**：分别列出 reason 描述事件与真正最早到期条目，记录事件类型、`action`、`keyCode`、native seq、输入日志 `eventId`、`deviceId`、`source`、`displayId`、window、channel，未知字段用 `null`。已发布连接的 reason 可以描述剩余 waitQueue 队首，不证明它就是真正最早到期条目；须核验各条目的投递及截止时间。native seq 不能与 Java 序号、输入日志 `eventId`、重建 `eventId` 或窗口 token 互换。无焦点分支描述的是等待投递事件，不是已投递未 ACK；其 native seq/channel 可能未知或不适用。
- **ANR 发生模型（所有类型）**：分别描述计时起点/有效阈值/触发条件、阻塞或等待、通知/最终归因、清理/恢复。日志支撑标为 `observed`，机制解释标为 `inferred`，未观察节点标为 `unknown`；整体模型仍是候选。禁止假设固定 5 秒或其他固定超时、用 ANR 时刻倒推起点、跨时钟强行算时长，或将 Trace 快照当作持续阻塞证明。输入分支的 Java finish 返回、`handled=false`、一条 FINISHED 都不能单独证明连接已恢复；其他分支也须分别核验完成与恢复。
- **模拟操作步骤（所有类型）**：明确为“候选未执行，不保证复现”，包含前置条件、编号动作、预期观察、采集核验和缺失参数。只用证据支持且参数已知的动作；没有可支持动作时内部 `actions=[]`，但 `simulationSteps` 仍须给出具体缺口和补证计划，不能整体留空。不得编造坐标、业务入口、设备/窗口配置或故障注入，不自动运行设备命令，也不执行 adb 输入或故障注入。采集对象应匹配当前分型的组件、进程和完成链，输入分支才要求连接/seq/显示屏等专项身份；只观察到 UI 变化不代表原故障已复现。`replay` 仍为分析流水线回归重跑，不是设备操作脚本或事故真实回放。

模型和候选步骤沿用 `anr_evidence/anr_strategy.py` 的分型重点，不新增固定超时或要求真实故障注入：

- `broadcast_timeout`：核验 BroadcastQueue、receiver/onReceive、goAsync/finishReceiver 及进程启动；缺少 receiver、Intent 或完成时间时列为参数/证据缺口。
- `service_timeout`：核验前后台 service、执行与生命周期回调、进程启动和主线程；不把短示例窗口当通用有效阈值。
- `content_provider_timeout`：区分 provider publish/cold-start 与 query/CRUD/Binder 链，记录调用方、服务方与缺失请求身份。
- `job_scheduler_timeout`：核验 JobScheduler、JobService onStartJob/onStopJob、bind/start 和 main 生命周期。`jobFinished` 不等于 onStartJob/onStopJob 按时返回，异步任务完成与回调按时返回分别举证；用户发起任务的 `setNotification` 是另一分支，不能用普通回调或 jobFinished 证据代替。任务类型、配置或触发条件未知时先列缺口、补证，不新增固定超时。
- `system_watchdog_swt`：核验 system_server 的受监控 Handler/monitor、锁与 Binder 对端，不按普通 app ANR 归因，不提出触发真实 watchdog 的故障注入。
- `unknown`：列出类型分类、直接触发点、组件/进程身份、起点/阈值缺口，提出补采 reason、事件日志、对应进程 Trace 等计划；不能用一个输入模板填满未知节点。

三个新增数组 `inputEventAssessments`、`anrModel`、`simulationSteps` 的元素统一包含 `summary`、`supportingEventIds` 或 `evidenceRefs`、`uncertainties`。模型和模拟步骤（事件评估也按候选处理）固定为 `certainty=inferred`、`validation=unverified`、`executed=false`、`requiresHumanConfirmation=true`；模型内部可以引用观测支撑或标注未知阶段，但不能将模型整体升级为事实。

`supportingEventIds` 只填当前组重建中真实存在的 `eventId`，不是输入日志中的 `eventId`；`evidenceRefs` 使用当前组唯一精确匹配的完整 `rawLine`。没有可用引用时留空并写明缺口。引用存在不证明事件评估、机制、步骤或因果成立；AI 声称的身份、时长和引用均不能凭报告本身成为 `observed`。

多智能体 manager 保持 JSON-only，用这三个数组表达候选内容。以上是模板/提示约束，不是响应合规保证；manager 的 raw `integrated_report` 须经 `validate_manager_report` 实际校验，不能因 prompt 要求了保守标志就声称运行时已强制执行。运行时接入情况与原始证据、实际输出仍须分别核验。

### 实际 runtime 路径与候选边界

公共派生 `incidentScenario`（`anr-incident-scenario-v1`）、input 专项 `inputAnr` 与 AI 输出数组是不同数据层，不相互替代：

- `incidentScenario` 适用于所有 ANR 类型，包含 `anrModel`、`simulationSteps`、`missingParameters`、`evidenceGaps`，顶层保持 `certainty=inferred`、`validation=unverified`、`executed=false`，并保留 `finalJudgment=false`、`notRootCauseYet=true`、`requiresHumanConfirmation=true`。模型/步骤可给出具体缺口而非虚构完整链路，缺失证据不会把 unknown 自动升级为 input。
- 非 input 的 `incidentScenario.anrModel` 顶层是候选；模板节点除精确 anchor ANR 上报以外均为 `unknown`。关键词只生成 `candidateObservationIds`，仅保留在 JSON，不升级 `observed`，不写成已观测的 Markdown 时间线，也不证明节点实际发生、计时起止、机制或恢复。精确 anchor 只支持 ANR 上报，不能顺带证明通知或上游节点；保留已有 `evidenceGaps` 和缺失参数。
- `inputAnr.reportedEvents` 保存原样 reason 及其可提取的身份，包括 `nativeSeq`、`reportedSeq`、`seqDomain`、window、connection 等；原文出现的普通 seq 不等于已确认的原生序号。缺少可归属身份的匿名 reason 仍为 `unverified` 候选，不补造连接或事件身份。
- `inputAnr.triggeringEvent` 表示真正最早到期条目；当前尚未核验，保持 `null`，不能拿 reason 描述事件替代。
- input 类型的 `inputAnr.anrModel`、`inputAnr.simulationSteps` 复用模型/步骤，专项事件身份仍独立保留；缺少操作参数时列出 `missingParameters`，不声称计划已执行或保证复现。
- 确定性流水线由 Phase 3 生成并经 Phase 5 → Phase 6 → Phase 7 透传 `incidentScenario`，保留 input 类型的 `inputAnr`。`--report` / `--deliver` 和独立重建 Markdown 对所有类型同步展示“ANR 发生模型”“模拟操作步骤”，input 另展示“超时输入事件”。非 input 只有输入专项可写不适用。
- AI 的 `inputEventAssessments`、`anrModel`、`simulationSteps` 在重建 `aiClaims` 中独立保存并校验引用，不覆盖 `incidentScenario` / `inputAnr`，也不增删或修改 `observed` 事件。新字段 `referenceValidation` 只表示引用解析结果；即使引用成功，候选的 `validation` 始终为 `unverified`，`executed=false`，模型和步骤保持 `certainty=inferred`、`requiresHumanConfirmation=true`。引用成功不证明候选内容、因果或设备执行。
- 重建构建和已有 `anr-reconstruction-v1` sidecar 刷新均自动派生 `incidentScenario`；input 仍有 `inputAnr`。无需重填或改写四个 AI 槽位，既有证据摘要/身份核验边界不变；过滤证据被修改时仍须重新提取，不能靠刷新绕过。manager prompt 接收已有 `incidentScenario` 摘要，并继续支持原 `inputAnr` 摘要。

以上派生、透传与重建导入校验不等于 manager raw `integrated_report` 已经过同样的运行时处理，二者验证边界须分别报告。

最终自由文本之后追加独立的 `json` 围栏代码块，结构保持如下：

```json
{
  "anrType": "input_dispatching_timeout | no_focus_window | broadcast_timeout | service_timeout | content_provider_timeout | job_scheduler_timeout | system_watchdog_swt | unknown",
  "primaryRootCauseHintId": "<真实 hint id 或 null>",
  "primaryRootCauseDescription": "<不超过 80 字>",
  "supportingHintIds": ["<上下文中真实出现的 hint id>"],
  "blockingThread": {"tid": "<tid 或 null>", "name": "<name 或 null>", "frame": "<frame 或 null>"},
  "ownerThread": {"tid": null, "name": null, "frame": null},
  "sourceAnalyses": {
    "trace": {"summary": "...", "keyEvidence": ["..."], "gaps": ["..."]},
    "eventLog": {"summary": "...", "keyEvidence": ["..."], "gaps": ["..."]},
    "logcat": {"summary": "...", "keyEvidence": ["..."], "gaps": ["..."]}
  },
  "candidateChains": [
    {"rank": 1, "confidence": "critical|strong|weak", "summary": "...", "evidence": ["..."]}
  ],
  "inputEventAssessments": [
    {
      "summary": "<reason 描述事件与真正最早到期条目的区别；无焦点为等待投递事件>",
      "reasonEvent": {"type": null, "action": null, "keyCode": null, "nativeSeq": null, "eventId": null, "deviceId": null, "source": null, "displayId": null, "window": null, "channel": null},
      "earliestExpiredEntry": null,
      "supportingEventIds": [],
      "uncertainties": ["<身份或最早到期条目的证据缺口>"],
      "certainty": "inferred",
      "validation": "unverified",
      "executed": false,
      "requiresHumanConfirmation": true
    }
  ],
  "anrModel": [
    {
      "summary": "<按当前 ANR 类型给出候选模型；缺证时列分类/触发点等具体缺口>",
      "phases": [
        {"phase": "timing", "summary": "<起点、有效阈值与触发条件待核验>", "certainty": "unknown", "candidateObservationIds": []},
        {"phase": "blocking", "summary": "<等待区间待核验；快照不证明持续时长>", "certainty": "unknown", "candidateObservationIds": []},
        {"phase": "notification", "summary": "<通知/归因待核验；关键词不证明机制发生>", "certainty": "unknown", "candidateObservationIds": []},
        {"phase": "recovery", "summary": "<清理与恢复分别核验>", "certainty": "unknown", "candidateObservationIds": []}
      ],
      "supportingEventIds": [],
      "uncertainties": ["<缺失节点、时钟或因果关系>"],
      "certainty": "inferred",
      "validation": "unverified",
      "executed": false,
      "requiresHumanConfirmation": true
    }
  ],
  "simulationSteps": [
    {
      "summary": "<候选未执行，不保证复现>",
      "preconditions": [],
      "actions": [],
      "expectedObservations": [],
      "captureChecks": [],
      "missingParameters": ["<待补参数；有证据支持后 actions 才填写 step/action 编号动作>"],
      "evidenceRefs": [],
      "uncertainties": ["<待核验条件和机制>"],
      "certainty": "inferred",
      "validation": "unverified",
      "executed": false,
      "requiresHumanConfirmation": true
    }
  ],
  "remediationSuggestions": ["..."],
  "evidenceGaps": ["..."],
  "finalJudgment": false,
  "notRootCauseYet": true,
  "requiresHumanConfirmation": true
}
```

`primaryRootCauseHintId` 和 `supportingHintIds` 只能引用当前工作区中实际出现的提示；证据不足时使用 `null`。不要把程序化提示或外部进程的慢消息直接当成目标包根因。
