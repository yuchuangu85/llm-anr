# D2 dumpsys Input / Window adapter contract

状态：**implemented local slice, not device-compatibility accepted**（2026-09-09）。

`anr_evidence.dumpsys_input_window.parse_dumpsys_input_window(...)` 是 D2 的第一个真实 raw-source consumer：它将可选的 `dumpsys_input` 与 `dumpsys_window` source 解析为严格、source-local 的 snapshot，并由 `dump_quality.annotate_dump_quality_with_dumpsys_snapshot(...)` 投影到 DQ1，再由既有 `inputLifecycle` 渲染。它不构造通用因果图，也不改变 DQ1 的 PID、时间、stale、process-lifetime、group association 或 causal-confidence 判定。

## 输入接入与 resource boundary

- directory/ZIP/TAR 的 C2 default route 把 `dumpsys/input(.txt)`、`dumpsys/window(.txt)` 这类 `dumpsys/` 路径保留为**候选 source artifact**；仅凭路径不会形成语义结论。
- 普通 loader 需要同时看到严格 header 和至少一个标记字段才将内容归类为 `dumpsys_input` / `dumpsys_window`。否则内容仍是不支持的候选，adapter 输出 `unsupported` 和明确 gap。
- parser 对每个 source 限制为 2 MiB、10,000 logical lines；超限、缺 source 或非 UTF-8/materialization 失败均不扩大读取，返回 gap。
- store-backed materialization 恢复 physical line delimiter，避免把原始 dump 的相邻记录拼成虚构的一行；原始 bytes、artifact commitment 与 source coordinate 仍由 store 保存。

## 当前消费的严格字段

adapter 只消费下列带标签的 raw snapshot 字段；未命中、缺字段或冲突均是 `unknown` / `rejected` / `conflicted`，不会用 package-name、TID、时间接近或 Recents handler 修补。

| Source | 必需 header | 已消费字段 | 当前 consumer |
| --- | --- | --- | --- |
| `dumpsys_input` | `Input Dispatcher State:` | `FocusedWindow:`、`FocusedApplication:`、`PendingEvent(s):`、行内 `inputChannelToken` / `mInputChannelToken`、`mDisplayId` | DQ1 snapshot 的 Input/queue 状态与 input→window transfer candidate |
| `dumpsys_window` | `WINDOW MANAGER WINDOWS` | `mCurrentFocus=`、`mFocusedApp=`、`Window #… Window{…}:`、`mDisplayId=`、`mSession=Session{… PID:…}`、`mInputChannelToken=`、`mHasSurface=true` + `mViewVisibility=0x0` | DQ1 snapshot 的 Window state、PID/process/display/token candidate |

一个 `transfer.status = matched` 必须同时满足：Input 与 WMS 的 `Window{…}` identity 相同、恰有一个 WMS block、input-channel token 精确相同，且两个 display ID 精确相同。token/display 缺失为 `unknown`；不同值为 `rejected`；多个 WMS record 为 `conflicted`。

## DQ1 / blame boundary

DQ1 新增的 `dumpsysSnapshot` 只是 source-local 投影：

```json
{
  "inputStatus": "observed | absent | unsupported",
  "windowStatus": "observed | absent | unsupported",
  "queueStatus": "pending | empty | unknown",
  "transferStatus": "matched | rejected | conflicted | unknown",
  "blameTransfer": {
    "status": "unqualified",
    "reason": "dumpsys_capture_time_unavailable"
  },
  "captureTimestamp": null,
  "lifecycleStatus": "unknown"
}
```

同一 DQ1 record 还暴露唯一共享的 `dumpLifecycle` view：

```json
{
  "schemaVersion": "dq1-dump-lifecycle-v1",
  "status": "unavailable | partial | observed",
  "source": "none | dumpsys_input_window",
  "captureTimeStatus": "unavailable",
  "inputStatus": "unknown | observed | absent | unsupported",
  "windowStatus": "unknown | observed | absent | unsupported",
  "queueStatus": "pending | empty | unknown",
  "transferStatus": "matched | rejected | conflicted | unknown",
  "blameTransferStatus": "unavailable | unqualified",
  "identityStatus": "<exact DQ1 copy>",
  "timeStatus": "<exact DQ1 copy>",
  "associationStatus": "<exact DQ1 copy>",
  "freshnessStatus": "<exact DQ1 copy>",
  "stalenessStatus": "<exact DQ1 copy>",
  "processLifetimeStatus": "<exact DQ1 copy>",
  "lifecycleStatus": "<exact DQ1 copy>",
  "provenanceStatus": "<exact DQ1 copy>"
}
```

`dumpLifecycle.status` 只描述 source-local snapshot 可用性；它不表示 snapshot 发生在 anchor、也不表示 dump fresh/stale、PID 未复用或存在可归责 lifecycle。D1、F2 和未来涉及 dump 的图形 adapter 必须 copy/project 这个 DQ1 view；不得各自生成 stale/wrong-PID/wrong-process/association 判定器。当前没有 WinScope 图形 dump consumer，metadata-only archive inspection 不能充当 DQ1 evidence。

`blameTransfer` 的 candidate 仅表示字段可映射，**不是** `inputLifecycle.blameTarget`，不是责任归因，也不改变 `finalJudgment=false`、`notRootCauseYet=true`、`requiresHumanConfirmation=true`。原始 dumpsys body 没有可信 capture timestamp 时，不能证明它发生在 ANR anchor；因此 DQ1 的 generic `lifecycleStatus`、freshness、staleness、process lifetime 与 high-confidence 资格保持原有 fail-closed 值。

## 已验证与未验证

本地测试覆盖：严格 header、精确 token/display/window transfer、token/display mismatch、缺字段、非 dump 文本、DQ1 projection 不改变 identity/lifecycle、以及 raw directory 和 ZIP default route 到 `anr_analysis.md` 的现有 `inputLifecycle` consumer。

这不是 Android 版本 profile、真实设备 dumpsys 格式、OEM output、capture-time provenance、完整 Input queue lifecycle、完整 Recents state machine 或根因兼容性证明。缺失事件必须保留 `unknown`，不能据此认定未发生。
