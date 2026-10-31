---
name: no-focused-window-anr-analysis
description: 分析 Android no focused window / no_focus_window ANR，关联焦点应用、输入焦点窗口、窗口就绪、InputDispatcher 等待与 WMS 归因。Use for an explicit no-focus investigation or an ANR reason/dump showing a focus-dependent event waiting for a focused window; not for a delivered-event ACK timeout or an isolated focus change without a no-focus wait signal.
---

# No Focused Window ANR 分析

目标：解释“哪个 display 的哪个应用正在等待可接收焦点事件的窗口、窗口为何未就绪、等待是否真正超时、上报最终归因给谁”。将直接触发条件、上游原因和缺失证据分开，不把“无焦点窗口”等同于“应用主线程卡住”。

## 触发与工作流

满足任一条件时加载本 skill：

- 用户明确要求分析 no focused window / 无焦点窗口 ANR，或指定 `no_focus_window`。
- 同一 ANR 的 reason 包含 `does not have a focused window`、`no focused window`、`no focus window` 或 `no window has focus`。
- InputDispatcher 日志或 dump 显示有焦点应用、无焦点窗口，且需要焦点的事件正在等待窗口，例如 `Waiting because no window has focus` 或活动的 `mNoFocusedWindowAnrState`。

仅有 `Input dispatching timed out`、焦点切换、窗口不可见或 `nativePollOnce`，不足以判为此类型。已分发事件未 ACK 的 connection timeout 是另一条分支，有对应证据时读取 [input-dispatch-timeout 专项](../input-dispatch-timeout-anr-analysis/SKILL.md)；没有 no-focus 等待信号时不要套用本 skill 的计时和恢复机制。用户明确指定类型时仍需验证，证据不支持则说明冲突。

1. 对目录、ZIP、TAR 或 fixture，先运行基线命令；类型已确认时才加类型参数：

   ```bash
   python3 scripts/anr_to_ai.py <input> [--package <pkg>] [--anr-type no_focus_window]
   ```

2. 从 `index.json` 选择目标 group，读取 `anr_analysis.md` 及同目录 `logcat.txt`。保留 Trace、EventLog、Logcat/AnrManager 和 Meminfo 的全部基线证据，不因本专项而删减来源。
3. 按 Trace → EventLog → Logcat/AnrManager 顺序填写三个来源槽位，再将跨源综合写入同一文件的 `#### AI Analysis — 最终 ANR 综合分析`。沿用 [统一分析指南](../../docs/anr-ai-analysis-guide.md) 的固定标题和 JSON tail；不新增第五个槽位，不只在聊天中输出结论。
4. 若同时出现 Launcher/Quickstep/Recents 信号，再加载 [gesture-anr-analysis](../gesture-anr-analysis/SKILL.md)。本 skill 判断通用焦点等待与归因，手势 skill 判断 Recents/transition 的上游原因；两者增量叠加，不能仅凭无焦点推定手势故障。
5. 只有零散日志、无法生成 context 时，直接输出相同报告结构，明确未生成/未写回文件及来源缺口。

## 机制边界

开始分析前读取 [AOSP 机制与源码索引](references/aosp-mechanism.md)。它依据用户指定的 `AOSP_Frameworks/docs/Model/no-focused-window-anr`，保留源码快照身份和教学模型限制；设备版本不同须回查对应实现，不能照搬字段名或行号。

- **焦点应用不等于焦点窗口。** WMS 发布 `InputApplicationHandle` 与通过 SurfaceControl/SF 提交窗口焦点请求是不同路径。WMS 的 `Focus request` / `mInputFocus` 不证明 native FocusResolver 已授予焦点。
- **计时依赖目标查找。** 需要焦点的事件实际进入查找、目标 display 有 focused application 且没有 focused window，才可能新建此等待。普通 pointer 触摸按坐标命中；没有这类事件，或应用和窗口均为空，不凭“已经过了 5 秒”推定此 ANR。这些排除条件不证明旧等待已 reset 或历史上从未上报。
- **期限不是启动时间或输入原始时间加 5 秒。** 当前实现保存 `mNoFocusedWindowAnrState`，以首次查找的 `currentTime + application dispatch timeout` 建立 deadline；原始 `eventTime` 只是另存的事件信息。重复查找不自动续期。5000 ms 是基础默认值，应用配置、HW multiplier、instrumentation/wrapper 会改变有效阈值。
- **这是目标分发前等待。** `PENDING` 不能解释为“此事件已交给应用但 main 没有 ACK”。无焦点窗口与主线程阻塞可以有关，但要另用栈和生命周期证据连接。
- **到期还要复查。** native 重新确认等待 display 上的应用 token 未变、焦点窗口仍为空，才捕获 policy 通知；到期后 state reset 不代表通知撤回。实际处理受调度影响，不能把 ANR 打印时刻当作精确 deadline。
- **恢复与丢弃不同。** 实际获得新焦点、native 焦点应用变化等路径可以 reset；新焦点可将 pending 事件重新入队。不能把 reset 当成必然丢弃，也不能把触摸另一应用当成立即 reset；触摸裁剪有独立条件。
- **pre-ANR 与 full ANR 不同。** feature flag 只控制早期警告，不关闭或延长 full ANR。无警告日志不能证明没有等待或没有 full ANR。
- **native 超时不等于最终定责或弹窗。** 通知释放 dispatcher 锁后在 system_server 内经 JNI 到 Java/WMS；WMS 可过滤未知或 stopped Activity，也可归因给过久未兑现的焦点请求窗口。不能虚构“必须 RESUMED”的入口条件或断言必然杀进程。

## 分析步骤

### 1. 锚定同一次事故与有效时钟

从 `am_anr` 记录时间、PID、process/package、reason，以 ΔT=0 对齐各源；再用 InputDispatcher 等待/超时、WMS `ANR in` 和 AnrManager reason 交叉确认。同包多次 ANR 分组分析，不能混用恢复事件。

为关键事件记录 displayId、Activity/app token、window/input-channel token、eventId（能取得时），避免把 application token 当 window token，或把另一个 display 的焦点当作恢复证据。对齐 wall clock 与 uptime/monotonic 时需有映射；缺少等待起点或有效 timeout 时，写“无法精确还原计时”，不要用 `am_anr - 5s` 填空。

### 2. 分别建立应用与窗口两条线

- 应用线：启动/attach → Activity resume → focused application 发布 → 是否切换/停止/死亡。`wm_on_resume_called` 只是生命周期证据，不是输入焦点确认；没有日志也不能断言没有 resume。
- 窗口线：addWindow/relayout → surface/finishDrawing/reportDrawFinished → WMS focus request → SF window-info/focus-request 传递 → native focus 解析。逐段标出已证实、推断、缺失。
- 核对 FocusResolver 的 `NO_WINDOW`、`NOT_FOCUSABLE`、`NOT_VISIBLE`。可见/可触摸的 `NOT_FOCUSABLE` 窗口仍不能接收焦点按键；已绘制不保证可见或有焦点；同 token 多个 native window 的条件要按实际实现检查。存在 `focusTransferTarget` 时还要跟踪最终解析窗口，不能只检查最初 request token。
- 若已绘制但仍 `NOT_VISIBLE`，继续查 parent layer、transition leash、Recents/input consumer；不要因已画完就直接归责 SF，也不要因未获焦点就直接归责应用主线程。

### 3. 还原等待、到期、恢复和归因

按证据填写以下状态链，缺失节点明确留缺口：

```text
focused application A / display D
  -> focus-dependent event reaches target lookup, focused window = null
  -> arm at lookup time, event remains pending
  -> [optional pre-ANR warning]
  -> branch: actual focus granted / application changed / other verified reset
       -> old wait cancelled; inspect event requeue/drop/dispatch separately
  -> branch: deadline reached -> recheck application token and focused window
       -> changed application or window now present: skip old-app report, reset
       -> same application and still no window: capture policy command, reset
            -> JNI -> InputManagerService -> WMS AnrController
            -> drop / pending-focus-window attribution / ActivityRecord -> AMS
            -> [later focus recovery / dialog dismissal, if observed]
```

分别核对“等待的应用”“WMS 请求焦点的窗口”“最终 ANR 进程”；三者可能不同。若恢复晚于通知捕获，说明其为事后恢复，不以 dump 时 state 已空或窗口已获焦点否定历史 ANR。恢复早于日志打印时，也需区分事件发生、通知捕获与延迟打印。

### 4. 排序候选上游原因

- **应用窗口建立慢**：启动/resume/relayout/draw 延迟与等待窗口一致；有对应 main/worker/Binder/锁/I/O/渲染证据才进一步指向具体阻塞。resume 晚只支持生命周期延迟候选，不能单独定责。
- **窗口焦点资格不满足**：同 token、同 display 的明确 `NO_WINDOW` / `NOT_FOCUSABLE` / `NOT_VISIBLE` 证据；继续区分未注册、flag、父层隐藏或 transition 状态。
- **焦点请求未兑现/窗口信息更新滞后**：有 WMS request，但 native 仍无焦点；比较事务、window-info 与 resolver 时间线。缺少中间阶段时只能定位区间，不能宣称 SF/WMS 缺陷已证实。
- **Recents/transition 未收尾**：只有叠加手势 skill 并完成同一 gesture/handler、finish、layer/focus 证据链后才提高置信度。
- **进程死亡/切换、系统负载或调度延迟**：关联相同身份与时间窗的进程日志、CPU/PSI/I/O/Meminfo；单条高负载或 ANR 后快照只作候选，不替代焦点链证据。

`nativePollOnce`/Looper idle 可能是 dump 滞后或真正空闲，既不证明应用无责，也不证明其卡住。未取得 trace 时，直接阻塞点可以写“目标 display 缺少可接收焦点事件的窗口”，不要补造阻塞函数。

## 举证与输出检查

每个影响候选排序的关键结论附原始日志片段及来源路径/行号、时间和关联身份。源码解释机制，不能替代事故日志；完整采集窗口内未观察到事件与截断日志缺行要分开表述。

在统一报告的固定章节中补全：

- **时间线**：首次目标查找/等待起点、有效阈值与来源、焦点请求和资格、超时复查/上报、ANR anchor、dump 和恢复；未观测节点标缺失。
- **三源分析**：Trace 说明现场和快照局限；EventLog 说明 ANR、生命周期/焦点身份；Logcat/AnrManager 说明等待、窗口链、归因/恢复和负载。各来源无结论时在对应槽位写明缺口。
- **直接阻塞点**：区分分发前无窗口与已分发未 ACK；给出 display/app/window/event 的已知关联。
- **候选根因链**：按置信度排序，逐条给支持、反证、未证实环节和责任边界。只证实触发条件不等于证实上游根因。
- **证据质量**：列缺失起点、timeout/flag、跨时钟映射、dump 滞后、window/SF/input dump 与采集截断；不要把模型测试当成真机证据。
- **修复建议**：对应已支持的链条处理窗口建立耗时、错误 focusable/visibility flag、焦点事务或 transition 清理；证据不足时补采 InputDispatcher/WMS/SF/Perfetto 和复现，不以调大 timeout 或关闭 pre-ANR 掩盖问题。
- **JSON tail**：沿用统一 schema；`anrType` 按证据填写，未知线程/栈与 hint 用 `null`，保留 `finalJudgment=false`、`notRootCauseYet=true`、`requiresHumanConfirmation=true`。

## 回源资料

- [AOSP 机制、配置与源码索引](references/aosp-mechanism.md)
- [ANR 全流程与综合分析](../anr-analysis.md)
- [Launcher / Quickstep 手势专项](../gesture-anr-analysis/SKILL.md)
