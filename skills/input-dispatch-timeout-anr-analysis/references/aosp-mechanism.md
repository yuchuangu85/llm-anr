# Input Dispatch Timeout AOSP 机制参考

## 来源身份与适用边界

整理自用户指定的 `AOSP_Frameworks/docs/Model/input-dispatch-timeout-anr/`，依据 `data/sources.json`、`data/mechanism-review.md`、`data/source-identity.json`、`README.md` 和 `src/model.js`。下面的路径相对 `AOSP_Frameworks/`；skill 的分析流程自包含，不要求使用者机器上存在该相邻仓库。

`source-identity.json` 记录检查日期 **2026-10-28**、快照 **`24bc82b5d25dd6ebc97e8c3401fa1678cd9f3ba5`** 及文件 hash。`sources.json` 的 `publish.summary` 另写了旧快照 `76a71e299faf8979c87b88d872b5811fa81a71eb`，两者不能当成一致的版本声明；回源以身份清单、实际文件 hash 和符号为准，不能照抄摘要中的版本号。行号仅定位这一份本地源码，不是跨 Android/厂商版本的保证。

本次只读复核：实际 HEAD 与身份清单一致，17/17 个记录文件 SHA-256 匹配且无改动，36/36 段摘录匹配指定行区间。两个提交的 base/native tree 内容相同，支持“摘要提交标签过时、相关源码未漂移”，不表示提交身份相同，也不扩大为对所有旁注引用或整个源码树的逐文件认证。

摘录匹配、机制复核、纯模型测试、浏览器验证和真实设备 ANR 是不同证据。页面并未证明设备 flag、实际调度耗时、厂商路径或 ANR 对话框行为。

## 发布、接收与两类 outbound 队列

### Dispatcher 派发端

`InputDispatcher::startDispatchCycleLocked` 先为 `DispatchEntry` 保存：

```text
deliveryTime = currentTime
timeoutTime = currentTime + getDispatchingTimeoutLocked(connection token)
```

随后尝试发布。非零状态先返回；该条目 `WOULD_BLOCK` 不会因已填时间字段就自动进入 ACK 追踪。waitQueue 非空时保留 outbound 等待后续推进；若 waitQueue 为空却发布 `WOULD_BLOCK`，当前源码调用 `abortBrokenDispatchCycleLocked` 清理两条派发队列，NORMAL 连接会被标为 BROKEN 并通知通道损坏。其他发布错误也调用 abort。不能把这个错误清理当成收到 FINISHED；页面“保留 outbound”的简写不能省略空 waitQueue 例外。

成功发布才从连接 `outboundQueue` 移到 `waitQueue`，且仅在连接 responsive 时插入 Tracker。当前循环允许同一连接多条事件在途；同一轮多个条目也可能使用同一个 `currentTime`。未发布条目重试会重新赋值其 delivery/deadline；这不追溯改写已发送条目的 deadline。

stage `publish`：`native/services/inputflinger/dispatcher/InputDispatcher.cpp:3893`、`:3996`；空队列异常与 abort 的补充回查位置为 `:3999`、`:4094`。

### 客户端接收与完成

```text
InputEventReceiver.dispatchInputEvent(native seq, event)
  -> mSeqMap: Java event sequence -> native dispatch seq
  -> WindowInputEventReceiver.onInputEvent / processRawInputEvent
  -> enqueueInputEvent / InputStage processing
  -> ViewRootImpl.finishInputEvent
  -> InputEventReceiver.finishInputEvent
  -> nativeFinishInputEvent / NativeInputEventReceiver::finishInputEvent
  -> receiver's own mOutboundQueue -> processOutboundEvents
```

普通 ViewRoot receiver 绑定创建时的 Looper，通常为所属 UI 线程；它不代表所有自定义 InputConsumer，也不会为每条事件新建接收线程。兼容分支可能转换或直接消费事件，不能把全部输入都解释为同一个 View callback。

Java finish 通过映射还原 native seq，并清除本地映射；同 Looper 调用要求、dispose 或查不到映射均需检查。本地映射删除不等于 system_server 的等待清理。应用 native receiver 的 `mOutboundQueue` 存的是待回传消息，与 Dispatcher 连接的 outboundQueue 是不同对象；发送 `WOULD_BLOCK` 可以保留 Finish，等待再次可写。

stages `receive`、`finish`：`base/core/java/android/view/InputEventReceiver.java:213`、`:220`、`:276`；同目录 `ViewRootImpl.java:1898`、`:7970`、`:11249`、`:11347`、`:11453`、`:11660`；`base/core/jni/android_view_InputEventReceiver.cpp:274`、`:361`、`:784`。

## FINISHED、批处理与 seq 清理

`InputConsumer::sendFinishedSignal` 可先展开 Motion 批次的序号链，再发送最后一个 seq；不能普遍假设一个 Java finish 只对应一个 native 等待项。`sendUnchainedFinishedSignal` 写 `InputMessage::FINISHED`，header 携带 seq，body 携带 handled / consumeTime，经 InputChannel socket 回到发布端。

- `handled=false` 仍是完成信号。业务是否消费输入与传输是否完成不是同一维度。
- FINISHED 写成功不证明对端已经读取；`TIMELINE` 是另一种指标消息，不能代替 FINISHED。
- payload 的 socket 传输不是每事件 Binder；应用内 finish JNI 也不是跨进程 finish RPC。

`handleReceiveCallback` 循环读取响应；一次 fd 回调可包含多个完成消息。`finishDispatchCycleLocked` 安排完成命令，`doDispatchCycleFinishedCommand` 再查 seq 并清理。按键 fallback 等处理可能释放锁，因此清理前会重新查找；匹配记录移除时只删相应 `(timeoutTime, connectionToken)` Tracker 索引，其他记录仍在。

Tracker 是允许重复键的 multiset，并不直接存 seq；`erase` 只删一个匹配索引，不能根据其中只剩一个 token 推断只剩一条事件。未处理 Key 可能生成新 fallback，但不表示原记录永不完成。

stages `socket`、`ack`：`native/libs/input/InputConsumer.cpp:746`、`:824`；`native/libs/input/InputTransport.cpp:748`；`native/services/inputflinger/dispatcher/InputDispatcher.cpp:4076`、`:4137`、`:6700`、`:6771`；同目录 `AnrTracker.cpp:30`、`AnrTracker.h:45`。

## 到期条目、reason 与失响应

`AnrTracker::firstTimeout/firstToken` 选择最早 deadline 对应的连接。`processAnrsLocked` 的 connection 分支在检查时 `currentTime >= nextAnrCheck` 才处理，不保证 deadline 纳秒级准点执行；前面的 no-focused-window 分支是另一套等待状态。

选中连接后：

```text
connection.responsive = false
AnrTracker.eraseToken(connection token)
onAnrLocked(connection)
```

`eraseToken` 只删该连接所有计时索引，不接触 `waitQueue`，也不自动断开 channel。连接版 `onAnrLocked` 若发现队列已空会跳过上报；否则用 `waitQueue.front()` 的最早发送未完成记录生成 reason、eventId 和原始 eventTime。

reason 的 `Waited N ms` 来自 `now - oldestEntry.deliveryTime`；这里传向 policy 的 `timeoutDuration` 也取此已等待时长，未必是触发记录的阈值。窗口配置变化后，晚发事件可采用更短 timeout 而先到期，reason 却仍描述较早事件。日志只给队首信息时，不能宣称已识别真正先到期的 seq。

之后 `cancelEventsForAnrLocked` 合成取消输入，不将原等待项清空。不能把取消、通道断开、原事件 ACK 和连接恢复画成同一动作。

stage `timeout`：`native/services/inputflinger/dispatcher/AnrTracker.cpp:42`、`:56`；同目录 `InputDispatcher.cpp:1143`、`:2346`、`:6792`，特别是 `:6804` 对 deadline 与 reason 不一致的说明。

## Policy、进程归因与恢复

```text
postCommandLocked -> command FIFO -> scoped_unlock(mLock)
  -> NativeInputManager.notifyWindowUnresponsive (JNI CallVoidMethod)
  -> InputManagerService / TimeoutRecord
  -> InputManagerCallback -> WMS AnrController
  -> actual window target / embedded target / optional pid fallback
  -> ActivityRecord or pid route -> AMS -> AnrHelper
```

上述 policy JNI 和 Java 调用位于 system_server，不是自动跳转到应用或 system_server 主线程。携带 IBinder token 不代表 JNI 调用本身是 Binder transact；JNI 中缺失可选 PID 的占位数值也不能当作真实 PID=0 归因。

WMS 解析实际 InputTarget；嵌入窗口、其他进程借用 Activity token、无 Activity 或 token 解析失败时，不保证走原 Activity。ActivityRecord 还核对 window PID；WMS 在退出全局锁后才进入对应 AMS 调用。当前返回值不能据旧注释推导 native 必然原地延长已触发 deadline。

普通有效进程进入 AnrHelper；调试、instrumentation、进程消失等有其他分支。排队、异步采样与最终弹窗是不同边界，不能补造同一时间或断言必弹窗/必终止。

stages `policy`、`recovery`：`native/services/inputflinger/dispatcher/InputDispatcher.cpp:1672`、`:6905`、`:6930`、`:6955`；`base/services/core/jni/com_android_server_input_InputManagerService.cpp:1354`；`base/services/core/java/com/android/server/input/InputManagerService.java:2670`；同一 Java 根下 `wm/InputManagerCallback.java:111`、`wm/AnrController.java:247`、`:271`、`:315`，`wm/ActivityRecord.java:6819`、`am/ActivityManagerService.java:18452`、`:19304`、`:19328`、`am/AnrHelper.java:123`。

超时后仍可收到 FINISHED。清理一条记录后，`isConnectionResponsive` 扫描整个剩余 waitQueue：若仍有 `timeoutTime < now()` 的记录，不能恢复；无过期记录则可以恢复，即使队列非空。这个 `<` 与到期检查的 `>=` 不相同，不能把模型同刻排序当平台保证，也不能因清了一条就宣称响应恢复。

恢复再经排队 policy → JNI/IMS/WMS；AMS 的 `inputDispatchingResumed` 请求关闭对应进程 ANR dialogs，不是回滚历史超时或通用取消 AnrHelper 队列。已排队 unresponsive 通知不会被后来 ACK 清理/恢复命令越过，但这个 FIFO 不要求各 seq 从 waitQueue 队首依次完成，也不规定 AMS 异步工作的完成顺序。该恢复代码不重建此前删除的旧 Tracker 索引，不能解释为所有旧条目重新计时。

回源：`native/services/inputflinger/dispatcher/InputDispatcher.cpp:540`、`:6733`、`:6955`；`base/services/core/java/com/android/server/wm/AnrController.java:330`、`:348`；`base/services/core/java/com/android/server/am/ActivityManagerService.java:18469`。

## 配置与八种教学场景

基础默认 5000 ms 不等于所有窗口固定 5 秒。Java 默认已乘 `Build.HW_TIMEOUT_MULTIPLIER`；native 默认亦有倍率。`getDispatchingTimeoutLocked` 依次考虑 monitor、对应窗口、native 兜底；有效窗口直接提供 `dispatchingTimeout`，不再无条件加一个默认 timeout 或乘一次倍率。当前 instrumentation/wrapper 可选 60000 ms。

stage `configuration`：`native/libs/input/android/os/IInputConstants.aidl:23`；`base/core/java/android/os/InputConstants.java:23`；`native/services/inputflinger/dispatcher/InputDispatcher.cpp:125`、`:1195`；`native/libs/gui/include/gui/WindowInfo.h:307`；`base/services/core/java/com/android/server/wm/WindowProcessController.java:1482`、`ActivityTaskManagerService.java:345`。普通窗口赋值链可继续查 `InputMonitor.java:253` → `WindowState.java:1792` → `ActivityRecord.java:2177` / `InputWindowHandleWrapper.java:132`。

`src/model.js` 和 `tests/model.test.cjs` 的八个场景用于检查解释自洽，不是事故证据：

1. `looper`：发布后接收线程迟迟未消费，不等于输入 handler 本身运行超时。
2. `handler`：输入已进入处理链，但 finish 迟到。
3. `deferred`：表面工作结束，异步阶段或接收器仍未 finish。
4. `ack-delay`：finish 已调用而 Dispatcher 尚未清理；合并 native outbound、socket 返回和命令清理延迟的极端抽象，不代表正常 socket ACK 常延迟数秒。
5. `normal`：处理、finish、清理均在 deadline 前完成。
6. `unhandled`：选定 Motion 的 handled=false 仍能正常完成。
7. `multi`：各事件独立 deadline；E1 的 ACK 不删除 E2/E3，恢复还看其他过期项。
8. `changed-timeout`：E2 的短 deadline 先到，reason 仍描述 E1；只改变后发条目的阈值，不追溯改写已发送记录。

模型只选单窗口连接、成功发布、非批量 Motion、一至三条原始记录；不完整模拟 Key fallback、多窗口或所有取消事件。合成取消事件仅标注，恢复例子假设它们不引入额外过期项。默认基础 timeout、固定阶段时刻、policy 视觉间隔、同刻 ACK 优先清理均为教学选择，不是实测耗时或并发顺序保证。connection 分支不能照搬 no-focus pre-ANR 公式。
