---
name: input-dispatch-timeout-anr-analysis
description: 分析 Android input dispatch timeout / input_dispatching_timeout ANR，追踪已发布输入的 waitQueue、FINISHED、逐事件 deadline、连接失响应与恢复。Use for explicit input-dispatch-timeout investigations or evidence of a published event awaiting completion; classify generic input timeouts first, and route a pure no-focused-window wait to its separate skill rather than treating it as an ACK timeout.
---

# Input Dispatch Timeout ANR 分析

目标：回答哪个连接的哪些已发布事件尚未完成、超时发生在哪一段、日志 reason 对应哪条记录、上报与恢复如何衔接。直接触发条件不等于上游根因；`finish` 返回、`handled=false`、画面已出现或一份空闲栈都不能单独证明 Dispatcher 已清理等待。

## 触发与分流

满足任一条件时读取本 skill：

- 用户明确要求分析 input dispatch timeout / 输入分发超时，或指定 `input_dispatching_timeout`；先验证实际分支，不因用户用语而预设 ACK 超时。
- 同一 ANR 有连接失响应 reason，例如 `<channel> is not responding. Waited ...ms for ...`，并指向已发送的 KeyEvent/MotionEvent 或未完成派发。
- 日志、InputDispatcher dump 或 trace 证明某连接发布成功后仍有 `waitQueue` / `AnrTracker` 等待完成的记录。

仅有通用 `Input dispatching timed out` 时先分型。若证据仅为 `does not have a focused window` / `Waiting because no window has focus`，使用 [no-focused-window 专项](../no-focused-window-anr-analysis/SKILL.md)，不能把分发前等待套成已分发未 ACK。只有确有不同事件/阶段的证据时才并列分析两条路径，不混用它们的计时起点。

Launcher/Quickstep/Recents 信号同时满足 [手势专项](../gesture-anr-analysis/SKILL.md) 的触发条件时再叠加；Recents 出现本身不证明它导致连接超时。只有主线程慢、Binder/锁/渲染栈而没有输入完成链证据时，保留通用 trace 候选，不反推某条事件已经发布。

## 输入与仓库工作流

1. 对目录、ZIP、TAR 或 fixture，先执行基线提取；只有确认分支或需要显式策略时才指定类型：

   ```bash
   python3 scripts/anr_to_ai.py <input> [--package <pkg>] [--anr-type input_dispatching_timeout]
   ```

2. 从 `index.json` 选择目标 group，读取 `anr_analysis.md`、同目录 `logcat.txt` 和独立重建产物。保留 Trace、EventLog、Logcat/AnrManager、Meminfo 等基线证据，不为本专项删减来源。
3. 按 Trace → EventLog → Logcat/AnrManager 顺序写三个来源槽位，再在 `#### AI Analysis — 最终 ANR 综合分析` 汇总。遵循 [统一指南](../../docs/anr-ai-analysis-guide.md) 的固定标题、JSON tail 和保守标志，不新增第五个槽位。
4. 四槽位完成后运行 `python3 scripts/reconstruct_anr.py <context-root|group-directory|anr_analysis.md>`；交付原分析和已刷新的 `anr_reconstruction.md` / `anr_reconstruction.json`。重建的 `observed`、`inferred`、`unknown` 不得混写。
5. 只有零散明文日志时直接输出同样的报告结构，明确未生成/未写回文件及采集缺口；不为了凑齐流程伪造证据。

开始解释前读取 [AOSP 机制与源码索引](references/aosp-mechanism.md)。依据的是指定本地源码快照；其他 Android/厂商版本应回查对应实现，不能照搬字段、行号和阈值。

## 关键机制与禁止混淆

- **发布与计时登记不同。** `startDispatchCycleLocked` 在尝试发布前设置 `deliveryTime`、`timeoutTime`；只有成功发布才把该 outbound 记录移入 `waitQueue`，并在连接 responsive 时登记 `AnrTracker`。`WOULD_BLOCK` 不建立该条目的 ACK 追踪：waitQueue 非空时可保留 outbound 等待推进，waitQueue 为空时当前源码会 abort、清理派发队列，并把 NORMAL 连接标为 BROKEN；其他发布错误也走异常分支。不能以当前条目未发布排除其他已发布事件超时，也不能把 broken-channel 清队列当作正常 ACK 完成。
- **起点与有效阈值逐事件核对。** 该记录的 deadline 来自本轮派发 `currentTime + 有效 timeout`，不是物理输入 `eventTime` 或 Java callback 起点加 5 秒。`deliveryTime` 先于发布尝试赋值，不是成功返回时刻；未发布条目重试时可重新赋值，不是给已经在 waitQueue 的条目续期。配置按 monitor、窗口信息、native 兜底等实际分支选择；5000 ms 只是基础默认值，倍率和 instrumentation/wrapper 等配置可改变阈值，不能对已乘倍率的配置重复叠乘。
- **客户端有多段边界。** socket 已发布 → receiver 消费 → 输入处理链 → finish → FINISHED 发出/返回 → Dispatcher 完成命令清理，任何相邻两点都不能当同一个时刻。InputChannel 是 socket 传输，不是每事件 Binder 分发；应用内 finish JNI 和 system_server 内 policy JNI 也不是跨进程 Binder finish RPC。
- **序号与对象身份不同。** Java `event.getSequenceNumber()` 通过 `mSeqMap` 映射 native dispatch seq。关联键至少含 connection/input-channel token 与 native seq；应用对象序号、日志 `eventId`、窗口 token 和 PID 不可互换。批处理还可能把一个 Java finish 展开为多个 native seq。
- **handled 不是完成开关。** 普通非批量 Motion 的 `handled=false` 仍发送 FINISHED 并按 seq 清理。Key 的未处理分支可能另起 fallback，应把原记录完成和新 fallback 分开，不把它们合并为无限等待。
- **允许多个在途事件。** 同一连接可以同时有多条 `waitQueue` 记录，独立保存 deadline。不要用旧 Java 注释推定“一条 ACK 后才能派发下一条”，也不要认为一条 ACK 清空整个连接。
- **触发条目与 reason 条目可能不同。** Tracker 选最早 deadline 的连接；连接版 `onAnrLocked` 的 reason 使用最早发送且仍在 `waitQueue` 的队首。新事件采用更短 timeout 时，晚发事件可能先到期，但 reason 仍描述较早事件。`Waited N ms` 及此路径通知的 `timeoutDuration` 是队首已等待时长，不一定是配置阈值。
- **失响应不等于队列清空或通道断开。** 到期后设 `responsive=false`，`eraseToken` 删除该连接所有 Tracker 索引；原 `waitQueue` 仍在。`cancelEventsForAnrLocked` 会合成取消输入，不能因此把原记录画成已完成，也不能宣称连接立即断开。
- **清理与恢复分别举证。** FINISHED 读到后还要经过排队完成命令；按匹配 seq 清理原记录及相应 Tracker 索引，再检查剩余队列是否有过期项。不是任何 ACK 都能恢复，也不要求队列必须为空；移除旧 Tracker 不等于其索引全被重建。
- **命令顺序和 Java 归因不能跳过。** 当前 native command queue 为 FIFO；已排队的失响应通知不能被后来排队的完成/恢复命令越过。这不表示 seq 必须按 waitQueue 队首顺序 ACK，也不是 AMS 异步工作的全局完成顺序。policy 调用前释放 Dispatcher 锁，在 system_server 内经 JNI → IMS → WMS；WMS 按实际目标 token/PID/Activity 再归因。不能断言必走当前 Activity、必弹窗、必杀进程或必延长本次 native deadline。
- **恢复不抹掉历史。** responsive 通知不撤回已经排队或执行的全部 ANR 工作，不证明业务完成、首帧呈现或 GPU 完成。no-focus 的 pre-ANR flag/预警公式也不能搬到本连接分支。

## 分析步骤

### 1. 定位同一次 ANR、连接与时钟

以 EventLog `am_anr` 锚定 PID、进程、reason 与时间，再关联 InputDispatcher 原始 reason、WMS/AnrManager 日志、dump 和恢复。将 ANR 检测、policy 通知、AMS 受理、异步采样与日志打印分开。

记录可获得的 connection/input-channel token、窗口名/窗口 token、displayId、实际 owner/target PID、native seq、eventId、事件种类/action、Java sequence 映射。窗口重建、PID/seq 复用、嵌入窗口和多窗口时分开关联；不要用同包名或相近时间代替身份闭环。

先核对 wall-clock 与 uptime/monotonic 的映射。没有映射、发布起点或有效 timeout 时，标“无法精确还原”，不使用 `am_anr - 5s`、原始 `eventTime + 5s` 或 reason 的等待时长补造 deadline。

### 2. 为每条在途记录分开列账

每条记录独立列出以下已观察节点，未观察到则保留 unknown：

```text
target selected -> outbound entry -> publication attempt
  -> WOULD_BLOCK: no new ACK tracking for this entry
       -> nonempty waitQueue: outbound may wait for later progress
       -> empty waitQueue: abort queues; NORMAL connection becomes BROKEN
  -> other publication error: follow broken/error path, not normal ACK completion
  -> successful publication: waitQueue; tracker insertion only if responsive
       -> receiver consumes -> input handling -> finish requested
       -> FINISHED transport -> dispatcher receive -> completion command
       -> matching seq removed; other entries remain
```

至少区分 `eventTime`、`deliveryTime`、当时有效 timeout、deadline、客户端接收/处理/finish、FINISHED 接收、waitQueue 清理。只存在一条总耗时或一份栈时，不能给中间阶段分配虚构时长。

### 3. 核对超时与恢复分支

```text
earliest tracked deadline reached during a dispatcher check
  -> connection unresponsive; eraseToken (not waitQueue.clear)
  -> reason describes oldest remaining sent entry, not necessarily trigger entry
  -> queue unresponsive policy command; synthesize cancellation side effects
  -> later FINISHED cleanup removes its matching entry
       -> another overdue entry remains: stay unresponsive
       -> no overdue entries remain: responsive notification may be queued
```

把“最早到期的记录”“reason 描述的记录”“最终归因的进程”分别说明。若日志只给 reason，不能声称已经识别真正最早 deadline 的 seq。未知取消事件、批处理或 fallback 可能影响剩余队列，恢复判断需注明这些缺口。

### 4. 排序上游候选

- **Looper 未及时消费**：发布成功后接收回调迟到，结合接收线程的消息、主线程栈、锁/Binder/I/O、调度证据；不能只凭发布到 callback 的空白区间断言主线程跑业务。
- **输入处理链耗时**：接收/处理开始已证实，finish 迟到；定位 listener、View/InputStage 或对应 consumer，关联 owner/peer、渲染与负载，不把所有输入都套成相同 View 回调。
- **延迟 finish/异步责任未履行**：处理表面结束但 finish 未完成，核对接收器、异步阶段、dispose、同 Looper 调用要求和 seq 映射。缺 finish 日志与确实没调用是不同结论。
- **finish 后仍未清理**：只有同一 seq 的 finish 与服务端等待/清理证据闭合后，才将区间定位到 native outbound、socket 返回或完成命令。不能直接宣称 socket 通常延迟数秒；FINISHED socket write 成功也不证明对端清理。
- **多事件/配置/系统压力**：分别复算已知 deadline，结合剩余 waitQueue、窗口配置、AnrManager CPU/PSI/IO/Meminfo。一个事件完成不能解释其他 seq，高负载本身不能代替这条连接链的证据。

`nativePollOnce` 或 dump 时栈已恢复只能说明采样现场；可能有采样滞后、延迟 finish 或返回清理问题，但都需要补证。没有线程证据时，直接阻塞点可以写“已发布事件的连接完成等待尚未解除”，不补造应用阻塞函数。

## 报告与重建检查

沿用统一报告章节，将本专项信息写入对应来源和最终槽位：

- **时间线**：事件/连接身份、发布与清理、逐事件 deadline、触发与 reason 区别、失响应/取消/恢复及采样，逐项给原文路径、行号、时间。
- **直接阻塞点与候选链**：明确缺失的是哪一段完成进展。每条候选附支持、反证、未证实环节及置信度；源码只解释机制，不作为事故日志替身。
- **证据质量**：列出缺失的 publish/seq/FINISHED、队列快照、超时配置、时钟映射、采集截断、批处理/fallback/其他窗口、对端线程及 dump 滞后。`analysisComplete` 或来源齐全不代表因果链已证实。
- **修复建议**：对已支持的阶段处理主线程阻塞、输入 callback 耗时、异步 finish 责任或通道/系统侧延迟；证据不足先补采同 seq 的 trace/dump/Perfetto。不能用调大 timeout 或把 `handled` 强行设为 true 代替修复。
- **JSON 与独立文档**：只引用当前 group 实际存在的 hint/event ID；未知字段用 `null`，保留 `finalJudgment=false`、`notRootCauseYet=true`、`requiresHumanConfirmation=true`。刷新后的重建文档也必须将机制推断与观察分开，保留未知时间，不把教学模型的节点全部填成 observed。

## 回源资料

- [AOSP 机制、源码与模型边界](references/aosp-mechanism.md)
- [ANR 全流程](../anr-analysis.md)
- [No Focused Window 专项](../no-focused-window-anr-analysis/SKILL.md)
- [Launcher / Quickstep 专项](../gesture-anr-analysis/SKILL.md)
