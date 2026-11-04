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

最终交付保留写入四槽位的 `anr_analysis.md`，并提供每个组已刷新的 `anr_reconstruction.md` 和 `anr_reconstruction.json` 路径；独立重建不能取代最终综合分析槽位，也不能只在聊天中给出重建结论。

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
- `## 证据质量`
- `## 修复建议`

候选根因链按置信度排序，明确区分：触发类型 → 直接阻塞点 → 上游诱因 → 责任边界 → 证据强度。直接阻塞点只能写证据直接支持的内容；例如 `focused window` 缺失可以作为直接阻塞点，但不能在没有对应栈的情况下虚构某个业务函数。

最终自由文本之后追加独立的 `json` 围栏代码块，结构保持如下：

```json
{
  "anrType": "input_dispatching_timeout | no_focus_window | broadcast_timeout | service_timeout | provider_timeout | unknown",
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
  "remediationSuggestions": ["..."],
  "evidenceGaps": ["..."],
  "finalJudgment": false,
  "notRootCauseYet": true,
  "requiresHumanConfirmation": true
}
```

`primaryRootCauseHintId` 和 `supportingHintIds` 只能引用当前工作区中实际出现的提示；证据不足时使用 `null`。不要把程序化提示或外部进程的慢消息直接当成目标包根因。
