# Android ANR AI 分析指南

本指南是 `scripts/anr_to_ai.py` 生成的 `anr_analysis.md` 工作区的统一分析说明。
生成的 Markdown 文件从 `# ANR AI Context Cache` 开始，只保留当前 ANR 的证据、过滤日志和四个分析槽位；请先阅读本指南，再填写对应工作区中的分析内容。

## 分析原则

- 只基于工作区中的 Trace、EventLog、Logcat、AnrManager 和 Meminfo 证据分析，明确区分证据、推断和缺失信息。
- 先识别 ANR 类型和直接触发条件，不要把 `input_dispatching_timeout` 的结论套用于其他类型。
- `rootCausePatternHints[]` 只是候选提示，不等于最终根因；必须用多来源证据交叉验证。
- AnrManager 的 `Reason`、`Load`、PSI、`CPU TOTAL`、`CPU >90% processes` 和 `Top CPU processes` 优先于仅凭 Trace 的自由推断。
- 如果来源缺失、过滤为空、时间不一致或使用 fallback anchor，必须在“证据质量”中明确说明。
- 所有结论保持保守：`finalJudgment=false`、`notRootCauseYet=true`、`requiresHumanConfirmation=true`。

## Trace 线索和死锁判断

- `### 死锁检测` 是程序化锁图分析结果，可信度高于仅凭栈帧推断。
- `[DEADLOCK_CYCLE / strong / critical]` 表示等锁环成立且环上节点均 Blocked，可以作为高强度根因候选。
- `[DEADLOCK_LIKELY]`、`[DEADLOCK_SELF]`、`[LOCK_OWNER_BLOCKED]`、`[LOCK_OWNER_SLEEPING]` 和 `[LOCK_CONTENTION_BLOCKED]` 必须结合完整线程状态说明，不能直接升级为确定根因。
- Trace 中的 `▸ HINT[...]` 是程序化注解，不是原始 AOSP 日志；可以引用，但要标明其性质。
- `NATIVE_POLL_BUT_BUSY` 表示主线程累计运行量较高，禁止简单判定主线程空闲；`NATIVE_POLL_IDLE_LIKELY` 只表示可能空闲，仍需结合其他来源。
- `MAIN_BINDER_WAIT_REPLY`、`MAIN_SP_APPLY_WAIT`、`MAIN_IO_BLOCKED`、`MAIN_DB_BLOCKED`、`MAIN_NETWORK_BLOCKED`、`MAIN_GC_PAUSED` 和 `MAIN_RENDER_WAIT_FENCE` 命中时，应继续查对应 server、owner、I/O 或渲染证据。

## AnrManager 负载分析顺序

1. 先看 `Load` 和 PSI，判断 CPU、I/O、内存压力。
2. 再看 `CPU TOTAL` 和 `iowait`；`CPU TOTAL >=90%` 时标记整机/任务负载重。
3. 列出所有 `CPU >90% processes`，区分目标包自身高负载和外部系统进程影响。
4. 如果目标包 CPU 高于约 85%，要明确标记应用自身负载过高，并结合目标包 Meminfo、GC、LMK/OOM 和 ANR metadata。
5. 如果缺少目标包 Meminfo，不要把“没有证据”写成“已排除内存问题”。

## 四阶段分析顺序

按以下顺序填写当前 `anr_analysis.md` 中的四个槽位：

1. `#### AI Analysis — Trace 堆栈`
   - 总结主线程状态、直接阻塞点、相关线程、owner/peer、Trace hints、缺口和置信度。
2. `#### AI Analysis — EventLog 事件日志`
   - 以 `am_anr` 为 ΔT=0，列出 ANR 前窗口的生命周期、焦点、输入、进程和状态机变化。
3. `#### AI Analysis — Logcat / AnrManager`
   - 读取同目录的 `logcat.txt`，说明触发日志、窗口/焦点/Surface 序列、dump 生命周期、负载、PSI 和 Meminfo。
4. `#### AI Analysis — 最终 ANR 综合分析`
   - 跨源汇总并按下方固定标题输出最终报告和 JSON 尾部。

## Final ANR 固定输出

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

最终自由文本之后追加独立的 `json` fenced code block，结构保持如下：

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

`primaryRootCauseHintId` 和 `supportingHintIds` 只能引用当前工作区中实际出现的 Hint；证据不足时使用 `null`。不要把程序化 hint 或外部进程的慢消息直接当成目标包根因。
