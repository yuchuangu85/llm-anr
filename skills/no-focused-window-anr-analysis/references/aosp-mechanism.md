# AOSP 无焦点窗口机制参考

## 来源与适用边界

整理自用户指定的 `AOSP_Frameworks/docs/Model/no-focused-window-anr/`，主要依据 `data/sources.json`、`data/mechanism-review.md`、`data/source-identity.json`、`src/model.js` 和 `README.md`，不是对外部文章正文的转述。

该材料记录的源码快照为 `24bc82b5d25dd6ebc97e8c3401fa1678cd9f3ba5`，检查日期为 **2026-10-28**。下面的路径相对 `AOSP_Frameworks/`，行号仅用于回查这一快照。技能的核心流程自包含，不要求分析机器上存在该相邻仓库；要确认其他 Android/厂商实现时，使用设备对应版本源码、符号和日志，而不是假定本快照通用于所有版本。

`source-verification.json` 只证明摘录匹配；`browser-verification.json` 和模型测试只证明本地页面/模型行为。它们不证明设备调度、厂商实现、真实 ANR 弹窗或进程终止。参考页面未取得所提微信文章的正文，不从该文章引入论点。

## 两条独立更新路径

### 焦点应用

```text
DisplayContent.setFocusedApp
  -> InputMonitor.setFocusedAppLw
  -> InputManagerService.setFocusedApplication
  -> NativeInputManager::setFocusedApplication
  -> InputDispatcher::setFocusedApplicationLocked
```

`InputApplicationHandle` 表示可归属的应用，不是可分发输入的窗口。原生应用句柄变化时清除旧等待；相同句柄直接返回，不能拿重复设置调用证明重置。

本快照 JNI 的 `NativeInputManager::setFocusedApplication` 对 null Java 句柄提前返回。因此不能从 Java `setFocusedApplication(null)` 推导“原生映射已清空/计时已取消”；直接传 null 的原生调用与 Java 调用不是同一证据。

回源阶段 `focused-application-route`：`DisplayContent.java:4501`、`InputMonitor.java:530`、`InputManagerService.java:1418`（均位于 `base/services/core/java/com/android/server/` 对应包），JNI 文件 `base/services/core/jni/com_android_server_input_InputManagerService.cpp:1500`，以及 `native/services/inputflinger/dispatcher/InputDispatcher.cpp:5869`。

### 焦点窗口

```text
InputMonitor.requestFocus / updateInputWindows
  -> SurfaceControl 事务（正常路径合并到待处理事务）
  -> SurfaceFlinger::updateInputFlinger / BackgroundExecutor
  -> 窗口信息更新和焦点请求
  -> InputDispatcher / FocusResolver
  -> 实际原生焦点变化
```

WMS `mInputFocus`、`mInputFocusRequestTimeMillis` 和 `input_focus: Focus request` 描述的是请求。SF 发出窗口信息/请求的顺序不证明接收确认或画面已经上屏。窗口信息后到时，`setInputWindowsLocked` 还会重新解析待处理的焦点请求。

FocusResolver 按目标 token 检查原生窗口：至少一个匹配窗口可见，且所有匹配窗口可聚焦；否则可返回 `NO_WINDOW`、`NOT_FOCUSABLE`、`NOT_VISIBLE`。`getResolvedFocusWindow` 还沿 `focusTransferTarget` 检查后继 token，选择链中最后一个合格窗口，并防止循环；只检查最初 token 会漏掉实际目标。WMS 自己也可能因无 Surface 或不可聚焦而暂缓发请求；应区分“未请求”和“已请求但原生层未兑现”。

回源阶段 `focused-window-route`、`focus-eligibility`：`base/services/core/java/com/android/server/wm/InputMonitor.java:507`、`:517`、`:607`，`native/services/surfaceflinger/SurfaceFlinger.cpp:4836`，`native/services/inputflinger/dispatcher/InputDispatcher.cpp:5739`、`:7247`、`:7373`，同目录 `FocusResolver.cpp:111`、`:170`；焦点转移补充回查 `FocusResolver.cpp:143`。

## 目标查找与计时

普通按键和非指针类运动事件的目标查找会使用焦点窗口；按键可先被策略层消费/延迟，不能只看到按键时间就认定已开始查找。普通指针类运动事件走 `findTouchedWindowTargets`，不通过这一分支启动无焦点等待，但可发生其他输入/ACK 超时。

`findFocusedWindowTargetLocked` 的关键条件：

- 焦点窗口和焦点应用均为空：返回 `FAILED`，不凭空归因给任意应用。
- 窗口为空、应用非空、尚无等待状态：保存 `mNoFocusedWindowAnrState` 和 `mAwaitedApplicationDisplayId`，返回 `PENDING`。
- 已有等待且未到期：继续 `PENDING`，不重置截止时间。
- 找到焦点窗口：重置等待，再继续检查暂停、注入权限等条件；有焦点不代表后续分发必然成功。

```text
timeout = applicationHandle.getDispatchingTimeout(nativeDefault)
timeoutEndTime = lookupCurrentTime + timeout
state.eventTime = original entry.eventTime
```

不要把旧版 `mNoFocusedWindowTimeoutTime` / `mAwaitedFocusedApplication` 当成本快照的执行状态。没有完成分发时 `mPendingEvent` 保留，这不是此事件已送达后缺少 `finishInputEvent`。

回源阶段 `focus-dependent-event-routing`、`arm-focus-wait`：`native/services/inputflinger/dispatcher/InputDispatcher.cpp:1207`、`:1355`、`:2028`、`:2194`、`:2442`、`:2469`、`:2486`、`:2497`。

### 超时配置与 pre-ANR 预警

- 默认输入超时基础值为 5000 ms，原生/Java 默认值均考虑硬件超时倍率；有效应用 token 使用其 `dispatchingTimeoutMillis`，不能再对该配置重复乘倍率。
- 本快照 instrumentation/wrapper 路径可选 60000 ms。ActivityRecord 配置更新与已创建等待状态的时长快照是不同事实，不自动原地延长既有截止时间。
- pre-ANR 预警由 `enable_anr_warning_callback_input_dispatcher` 控制；每个等待状态最多排入一次警告。源码存在该标志不证明设备的实际开关值。

```text
warningLead = max(timeoutDuration / 2, 2000 ms * HW multiplier)
preAnrTime = timeoutEndTime - warningLead
```

在 timeout=5000 ms、multiplier=1 的示例中是开始等待后 2500 ms，不是固定 2000 ms。较小配置可使计划预警点早于建立等待的时刻；回调仍由实际调度执行。警告不重置、不延期、不替代完整 ANR，长方法追踪还受另一标志控制。

回源阶段 `timeout-configuration`、`pre-anr-warning`：`native/libs/input/android/os/IInputConstants.aidl:23`，`native/libs/gui/include/gui/InputApplication.h:45`，`native/services/inputflinger/dispatcher/InputDispatcher.cpp:125`、`:1071`、`:1120`、`:1127`，同目录 `InputDispatcher.h:1031`；`base/core/java/android/os/InputConstants.java:23`；`base/services/core/java/com/android/server/wm/WindowProcessController.java:1487`、`ActivityTaskManagerService.java:345`、`ActivityRecord.java:2150`、`:2177`、`AnrController.java:150`。

## 到期复查与重置

`processAnrsLocked` 在 `currentTime >= timeoutEndTime` 时调用 `processNoFocusedWindowAnrLocked`：

1. 在等待的显示屏上重新取焦点应用，比对应用 token；应用已变化时跳过旧应用上报。该异常分支也检查 null，但本快照日志随后解引用该指针，不能承诺 null 时安全返回，更不能据此推断事故中实际发生过崩溃。
2. 重新取该显示屏的焦点窗口；已有非空窗口则不报，不额外要求窗口属于原等待应用。
3. 条件仍成立时 `onAnrLocked(applicationHandle)` 捕获应用、eventId、原始 eventTime 和超时时长并排入策略命令。
4. 返回后重置等待状态；这个重置不会撤销已经捕获的命令。

查找阶段丢事件分支用 `currentTime > timeoutEndTime`，与到期检查的 `>=` 不同；其中注释 `Already raised ANR` 不能独立证明策略已执行。Looper 唤醒、命令执行和 Java 日志受调度影响，不把截止时间等同于弹窗或日志打印时间。

重置入口包括查找发现焦点窗口、原生焦点应用句柄变化、实际授予非空新焦点、分发从冻结状态解冻、重置/全部丢弃、到期处理完成。不要把任意模式设置调用、单独 `releasePendingEventLocked`、WMS 焦点请求、应用焦点回调或指针按下当作直接重置。

`enqueueFocusEventLocked` 会将待处理事件放回入站队列队首，给它向新窗口分发的机会；A → B 取消旧等待不等于丢弃原按键。触摸其他应用可经 `mNextUnblockedEvent` / `DropReason::BLOCKED` 独立裁剪队列，要有对应条件才判断事件被丢弃。

回源阶段 `deadline-and-native-recheck`、`reset-entry-points`、`policy-jni-to-wms`：`native/services/inputflinger/dispatcher/InputDispatcher.cpp:1004`、`:1022`、`:1034`、`:1143`、`:1311`、`:1380`、`:1794`、`:2374`、`:2497`、`:5869`、`:5964`、`:6266`、`:6835`、`:7279`。

## 上报、归因与事后恢复

```text
onAnrLocked(applicationHandle) -> 命令已入队，Dispatcher 锁已释放
  -> NativeInputManager::notifyNoFocusedWindowAnr (JNI CallVoidMethod)
  -> InputManagerService.notifyNoFocusedWindowAnr / TimeoutRecord
  -> InputManagerCallback -> AnrController.notifyAppUnresponsive
  -> 待兑现焦点窗口路径或 ActivityRecord.inputDispatchingTimedOut
  -> AMS / AnrHelper（受下游进程/调试器/instrumentation 处理影响）
```

这一原生层 → Java 跳转位于 system_server，不是把触发事件通过 Binder 发到应用主线程。`includeAnrInfo` 控制附加信息，不控制是否转发完整 ANR。

WMS 检查未知 Activity 和 `mAppStopped`，没有本入口必须 `RESUMED` 的谓词。若已有焦点请求窗口且请求已等待时长达到该目标 Activity 的超时时限，可改归因到该窗口；它不一定属于原等待应用。这是归因判断，不是再等待一个完整超时周期，也不能单独证明 SF 故障或应用死锁。

事后 `AnrController.onFocusChanged` 只有在新焦点窗口属于该显示屏记录的无响应 Activity 时才走 `inputDispatchingResumed`。此处 AMS 请求关闭进程 ANR 对话框；它既不是 Activity 恢复的前置条件，也不抹掉历史超时或取消所有排队中的 ANR 任务。

回源阶段 `policy-jni-to-wms`、`wms-attribution-and-recovery`：`base/services/core/jni/com_android_server_input_InputManagerService.cpp:1335`，`base/services/core/java/com/android/server/input/InputManagerService.java:2654`，`base/services/core/java/com/android/server/wm/InputManagerCallback.java:105`、`AnrController.java:88`、`:99`、`:117`、`:374`、`ActivityRecord.java:6819`、`:6853`，以及 `base/services/core/java/com/android/server/am/ActivityManagerService.java:18469`、`:19338`。

## 七种教学对照场景

参考 `src/model.js` 与 `tests/model.test.cjs`，用于检查解释是否自洽，不能当成事故的原始证据：

1. 窗口晚于期限：等待、到期复查、排入通知；稍后恢复不撤回已捕获通知。
2. 窗口及时就绪：重置，待处理事件有机会继续分发。
3. 可见但 `NOT_FOCUSABLE`：可触摸仍不等于能接收焦点按键，可进入无焦点等待。
4. 没有需要焦点的事件：无本分支计时，不代表不会发生其他 ANR。
5. 只有普通指针触摸：坐标命中，不通过此分支建立等待。
6. 焦点应用与窗口都为空：目标查找失败，不建立本分支等待。
7. A → 已就绪的 B：取消 A 的旧等待，原待处理按键可重定向 B；示例明确排除了触摸裁剪。

教学模型只选单显示屏、单事件、默认 timeout=5000 ms、multiplier=1，并假设输入已通过前置策略、就绪窗口未暂停/未丢弃输入。同一时刻先更新窗口再检查超时、固定策略层展示延迟、可调窗口就绪时刻均是演示规则，不是平台并发次序或真实耗时保证。
