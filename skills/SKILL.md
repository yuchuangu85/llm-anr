---
name: gesture-anr-analysis
description: 分析 Launcher/Quickstep 手势导航与 Recents 动画相关 ANR。Use when the user explicitly asks about this chain, or when an input/no-focus ANR also contains recents_animation_input_consumer, AbsSwipeUpHandler, OtherActivityInputConsumer, Recents lifecycle, or transition-visibility evidence; do not use for generic input/no-focus ANRs without a gesture or Recents signal.
---

# gesture-anr-analysis：手势导致 ANR 分析

## 1. 功能与适用场景

本 skill 用于分析 Android 手势导航、Overview/Recents transition、`no focused window` 和输入分发超时相关 ANR。重点回答：

1. ANR 是否发生在一次手势或进入 Recents 流程期间；
2. 是否存在 Recents 动画启动、回调分发或 finish 链路断裂；
3. 应用窗口没有焦点的直接原因是应用自身未绘制，还是被 transition leash 隐藏；
4. 根因属于哪条已知手势分支，证据强度是否足够；
5. 还缺哪些 trace、bugreport、dump 或复现日志。

**核心原则：时间接近只能作为高优先级候选信号，不能单独作为根因结论。** 必须把 `am_anr`、手势状态、Recents 生命周期、窗口可见性/焦点和 finish 结果串成可验证的证据链。

### 触发门槛

满足以下任一条件时使用本 skill：

1. 用户明确要求分析 Launcher/Quickstep 手势导航或 Recents 动画相关 ANR；
2. 同一目标 ANR 的证据同时包含：
   - **输入/焦点锚点**：`Input dispatching timed out`、`does not have a focused window`，或应用已 `reportDrawFinished` 但仍因 `NOT_VISIBLE`/transition leash 无法获得焦点；
   - **手势/Recents 信号**：`recents_animation_input_consumer`、`ACTION_DOWN`/`ACTION_UP`、`OtherActivityInputConsumer`、`AbsSwipeUpHandler`、`startRecentsAnimation`、`onTasksAppeared`、`finishRecentsAnimation` 或 `mRecentsAnimationStartPending`。

只有通用 input timeout/no-focus 信号、没有任何手势或 Recents 信号时，不使用本 skill。触发只表示进入专项分析，不表示手势链已经被证明为根因。

### 与仓库 ANR 工作流衔接

本 skill 是基线 ANR 分析的增量专项，不能替代或删减基线证据：

1. 用户提供目录、ZIP、TAR 或 fixture 时，先按 `AGENTS.md` 运行 `scripts/anr_to_ai.py` 并确定目标 group；
2. 完成 Trace、EventLog、Logcat/AnrManager 的基线专项分析，再用本 skill 关联同一 group 内的手势、Recents、窗口和焦点证据；
3. 将专项证据分别写入对应 source slot，并把综合判断写入同一文件的 `#### AI Analysis — 最终 ANR 综合分析` slot；
4. 用户只提供零散明文日志、无法生成 context 时，使用第 7 节模板直接输出，并明确未生成/未写回的产物缺口。

## 2. 输入与证据边界

### 2.1 输入文件

优先收集以下文件，并保留原始文件，不要只提供经过人工筛选的片段：

| 优先级 | 输入 | 用途 |
|---|---|---|
| P0 | `events_log`/eventlog | `am_anr`、`input_focus`、`input_interaction`、`wm_*` 的精确时间锚点 |
| P0 | main logcat | Launcher、SystemUI、InputDispatcher、SurfaceFlinger、gesture 日志 |
| P1 | bugreport / `dumpsys activity` | `ActiveGestureErrorDetector`、gesture dump、ANR trace、进程状态 |
| P1 | `dumpsys window` / `dumpsys SurfaceFlinger` | focus、visibility、transition leash、layer parent |
| P2 | 复现脚本输出 | 手势注入方式、轮次、设备状态和复现前兆 |

输入可以是多个文件或 `.curf` 导出的文本。分析前先确认文本可读；如果文件以 `%TSD-Header` 开头或出现乱码，记录为 DLP 加密证据缺口，改用授权的原始 `.curf`、`git`/导出明文或用户提供的文本，不要依据乱码猜测内容。

### 2.2 证据分级

- **strong**：同一时间线中有 `am_anr`、明确的旧手势标识（`logId`/handler hash）、有界采集窗口内的 Recents 未 finish/异常状态证据，以及 window/SF 的隐藏或焦点恢复证据；
- **medium**：手势与 Recents 生命周期完整，但缺 ANR trace 或 transition layer dump；
- **weak**：只有 `input_interaction` 与 `am_anr` 时间接近，或只有 `ActiveGestureLog` 单条事件；
- **unproven**：仅凭“发生过上滑”或“Launcher 当时有负载”推断手势根因。

缺失 trace、AnrManager、meminfo、ActiveGesture dump、设备真实触摸信息时必须在报告中明确列出，不能补造应用主线程阻塞或系统资源归因。

### 2.3 原始日志举证（强制）

**分析结论必须尽可能以原始 log 举证，不能只给二次概括的时间线。** 每个会影响根因判定的关键事实，至少附一段可回溯的原文；证据不足时，明确写“原始日志未直接证实”，不能用代码路径推断伪装成日志事实。

| 结论类型 | 必须尽量附上的原始证据 | 最低要求 |
|---|---|---|
| `am_anr` / 无焦点 | `am_anr`、`InputDispatcher` 等待、`input_focus` 原文 | 目标行及前后 2~5 行 |
| 手势身份与起止 | `ACTION_DOWN`/`ACTION_UP`、`logId`、`h=<X>`/`handler=<X>` | 至少保留同一手势的关联键 |
| pending-start | `startRecentsAnimation`、`mRecentsAnimationStartPending`、controller/running 状态 | 原文 + 相邻状态日志 |
| listener 摘除 | `OtherActivityInputConsumer: removeListener: handler=<X> ... startPending=true`，或等价的 listener 集合/诊断状态 | 优先使用同 handler 的直接日志；旧版本只有调用路径时须标为推断 |
| 旋转 / Taskbar 支线 | `onHandleConfigurationChanged`、Taskbar recreate/destroy、`TaskbarOverlayController: onDestroy` | 原文只能证明入口，不可单独证明 listener 已摘 |
| 回调/finish 断裂 | `onAnimationStart`/`onTasksAppeared`、`finishRecentsAnimation`/`cleanUpRecentsAnimation`，或 detector 原文 | 需同时展示“发生”和“未在 ANR 前 finish”的来源 |
| 窗口被 transition 卡住 | `reportDrawFinished`、`HAS_DRAWN`、`mVisible=false`、transition merge / leash | 至少一条应用绘制 + 一条焦点/可见性/transition 原文 |

#### 2.3.1 摘录规则

1. **逐字保留原文**：保留时间戳、pid/tid、priority、tag、消息、关键数值、handler hash、transition id；不得把推测补进代码块。
2. **保留上下文**：默认截取命中行前后 2~5 行；跨 tag 的因果关系可拆成多个小片段，但每段都标明其来源和时间，不要拼接成看似连续的一段日志。
3. **可回溯定位**：每个片段标题写明 `来源文件`；可取得时再标行号、`logId`、handler hash 或 timestamp。原始文件很大时，报告中保留精简片段，同时记录可复现的 `rg -n -C` 过滤式。
4. **严格区分来源**：main logcat/eventlog 的实时行、bugreport/dumpsys 中的历史 dump、源码推导必须分别标注。`ActiveGestureLog history` 不是实时主 log；不得写成“logcat 在当时打印”。
5. **不以 grep 缺失证明未执行**：`trackEvent(NO_OP)` 不出文本；对“未收到 callback”“未 finish”要结合 handler hash、detector/dump 状态和 ANR 前时间窗口说明。
6. **隐私最小化**：只有确实涉及隐私/凭据时才打码；打码要保留时间、tag、状态和关联键，并注明已打码。不得为美观而省略决定性字段。

推荐片段格式：

````markdown
#### E3 — pending-start（来源：`main_log.txt:12345`，logId=157，h=4b9a8fa）

```text
08-19 11:55:01.380 ... 原始日志行
08-19 11:55:01.381 ... 原始上下文行
```

说明：这段只证明 `startPending=true`；不证明 listener 是否仍在集合。
````

> “尽可能”不等于整段倾倒几十万行日志：优先给每一个因果环节的最小完整原文上下文。无法取得原文时，保留缺口并降低结论强度。

### 2.4 已抓取日志的整理

本 skill **只分析已经抓取并提供的日志文件，不负责实时连接设备或现场执行 dump 命令**。输入可以是合并后的 log，也可以是多个独立文件；先保留原始文件，再按日志源整理为：

- `events_log`/eventlog：`am_anr`、`input_focus`、`input_interaction`、`wm_*`；
- main logcat：`TouchInteractionService`、`AbsSwipeUpHandler`、`TaskAnimationManager`、`RecentsAnimationCallbacks`、`RecentsAnimationController`、`ActiveGestureLog`、`InputDispatcher`、`SurfaceFlinger`；
- 已抓取的 `TouchInteractionService`/`dumpsys input`/`dumpsys window`/`dumpsys SurfaceFlinger`/bugreport：补充当时的状态、焦点和可见性；
- 已抓取的复现脚本输出：说明手势是人工触摸、`input swipe` 还是 monkey 注入。

分析时不得因为输入缺少某个 dump 就自行假设其结果；应把缺失项列入 evidence gaps，并在结论中降低置信度。若设备版本没有 `ActiveGestureErrorDetector`，或者提供的日志中没有 detector/dump 内容，只能标记为 `unavailable`，不能把“没有 detector 日志”解释为“没有手势错误”。

## 3. 标准分析流程

### Phase 1：以 `am_anr` 建立唯一时间锚点

1. 在当前 `anr_ai_context` group 或用户指定的目标事件中找到对应 `am_anr`，记录时间、PID、包名和 ANR reason；同一输入有多个 ANR 时逐组分析，不用“最后一个 `am_anr`”替代目标选择；
2. 以该时间为 `T0`，先查看建议窗口 `T0 - 20s` 到 `T0 + 5s`；日志过大时先缩小到 `±10s`，发现跨窗口的手势后再扩大；
3. 在窗口内查找：
   - 手势起始：`ACTION_DOWN`、`startTouchTrackingForWindowAnimation`、`logId=`；
   - 手势结束：`ACTION_UP`、`setEndTarget`、`onGestureEnded`、`resetStateForAnimationCancel`；
   - Recents 输入：`input_interaction` 和 `recents_animation_input_consumer`；
   - Recents 启动/结束：`startRecentsAnimation`、`onAnimationStart`、`onTasksAppeared`、`finishRecentsAnimation`；
   - 旋转入口：`onHandleConfigurationChanged`、`PerDisplayTaskbarResource` recreate/destroy、`TaskbarOverlayController: onDestroy`、`onConsumerAboutToBeSwitched`、`removeListener`。

对每一个候选事实先截取原始 log 片段（遵循 §2.3.1），再写时间线。时间线是索引，不是原始日志的替代品。

如果 `am_anr` 落在一次手势或进入 Recents 的完整窗口内，标为 **gesture-related candidate / 高优先级候选**，并继续 Phase 2、3。必须确定该手势的起始日志，不能只引用 ANR 前最近的一条 `ACTION_UP`。

如果 `input_interaction: Interaction with: [recents_animation_input_consumer]` 与 `am_anr` 时间很接近，也标为 Recents 候选；建议默认以 `|Tinteraction - Tam_anr| <= 10s` 作为初筛范围，但应根据输入分发超时长度和日志采集情况调整。该信号本身不证明动画卡住，仍需检查 start/finish 和窗口焦点链。

### Phase 2：只保留高信噪日志

先按日志源分别过滤，再按时间和关联键回放。不要一开始把整个 logcat 交给分析者或模型。

#### 2.1 一次性初筛

在可用 shell 中：

```bash
rg -n -i -C 3 \
  'am_anr|InputDispatcher|input_focus|input_interaction|recents_animation_input_consumer|ACTION_DOWN|ACTION_UP|startRecentsAnimation|finishRecentsAnimation' \
  events_log.txt main_log.txt > gesture_anr_screen.txt
```

如果日志在一个文件中：

```bash
rg -n -i -C 4 \
  'am_anr|input_focus|input_interaction|recents_animation_input_consumer|ACTION_DOWN|ACTION_UP|startRecentsAnimation|onTasksAppeared|finishRecentsAnimation' \
  logcat.txt > gesture_anr_screen.txt
```

#### 2.2 按证据组过滤

**ANR 与输入分发：**

```bash
rg -n -i -C 4 \
  'am_anr|InputDispatcher|Input dispatching timed out|no window has focus|focused window|input_focus|input_interaction|recents_animation_input_consumer' \
  events_log.txt main_log.txt
```

**Recents 生命周期：**

```bash
rg -n -i -C 5 \
  'TaskAnimationManager|startRecentsAnimation|mRecentsAnimationStartPending|onAnimationStart|onRecentsAnimationStart|onTasksAppeared|finishRecentsAnimation|finishRunningRecentsAnimation|cleanUpRecentsAnimation|onAnimationFinished|continueRecentsAnimation' \
  main_log.txt
```

**手势状态和 listener：**

```bash
rg -n -i -C 5 \
  'ActiveGestureLog|ActiveGestureErrorDetector|ACTION_DOWN|ACTION_UP|setEndTarget|onGestureEnded|onConsumerAboutToBeSwitched|resetStateForAnimationCancel|removeListener|invalidateHandler|AbsSwipeUpHandler|OtherActivityInputConsumer' \
  main_log.txt
```

**窗口、Surface 与焦点：**

```bash
rg -n -i -C 5 \
  'reportDrawFinished|firstWindowDrawn|allDrawn|transition-leash|hidden by parent or layer flag|mVisible=false|NOT_VISIBLE|Focus entering|Requesting to set focus|Pruning input queue' \
  events_log.txt main_log.txt
```

**手势 dump 和错误检测器：**

```bash
rg -n -i -C 8 \
  'Logs for logId|Error messages for gesture ID|No errors detected|requested recents animation|onTasksAppeared was not expected|finish.*animation|settled on end target|invalid velocity' \
  main_log.txt bugreport.txt tis_dump.txt
```

> `rg` 找不到文件时，应先确认输入路径和导出格式；不要把“未找到”直接当作“事件未发生”。

#### 2.3 三种关联键

按以下优先级回放一条手势：

1. **时间戳**：从 `ACTION_DOWN` 开始，跟到 `ACTION_UP`、start callback、tasks appeared、finish/ANR；
2. **`logId`**：同一 `ActiveGestureLog` 的事件优先归为同一 gesture；
3. **handler hash**：`h=<X>` 或 `handler=<X>` 用来确认是否是同一个 `AbsSwipeUpHandler`。旧 handler 和后续新 handler 必须分开建时间线。

日志来自 logcat 和 eventlog 时，先统一时间格式和时区，再比较时间；不要使用文件行号替代时间。

### Phase 3：构造因果链

至少回答下面四个问题：

1. **应用是否已正常恢复和绘制？**
   - 有 `wm_on_resume_called`、`wm_on_top_resumed_gained_called`、`reportDrawFinished`、`firstWindowDrawn/allDrawn`，且应用无明显 `Skipped frames`/slow dispatch，则不能把应用主线程当作已证实根因。
2. **窗口为何没有焦点？**
   - 查 `input_focus` 的请求/恢复原因；
   - 查 SF 是否显示 `notVisible=1`、`hidden by parent or layer flag`；
   - 将 transition leash、task 可见性和 `NOT_VISIBLE` 联系起来。
3. **InputDispatcher 等待的直接阻塞是什么？**
   - 记录等待开始、等待时长和目标窗口；
   - `InputDispatcher` 的标准等待时长不是修复目标，不应通过调大超时掩盖问题。
4. **Recents 是否最终 finish？**
   - 查 `finishRecentsAnimation`、`finishRunningRecentsAnimation`、`cleanUpRecentsAnimation`；
   - 若 finish 只在 ANR 后或新手势接管后出现，说明先前存在挂起窗口。

推荐时间线：

```text
ACTION_DOWN
  -> startRecentsAnimation / startPending=true
  -> ACTION_UP / setEndTarget
  -> cancel、consumer switch 或 rotation
  -> onAnimationStart / onTasksAppeared
  -> finishRecentsAnimation（应在 ANR 前）
  -> input_focus / reportDrawFinished / ANR
```

## 4. 已知根因分支

### 分支 A：page-scroll callback 永久缓存

典型特征：

- `handleNormalGestureEnd`、`calculateEndTarget` 或 `setEndTarget` 之后，期望的 page transition callback 没有执行；
- `runOnPageScrollsInitialized(handleNormalGestureEndCallback)` 被缓存；
- 缺少 `STATE_RECENTS_SCROLLING_FINISHED`、`onSettledOnEndTarget` 和最终 `finishRecentsAnimation`；
- RecentsView 后续 layout/rotation 路径没有释放 pending callback。

判定时要区分“`trackEvent` 没有文本”和“真正没有执行”：优先使用 `ActiveGestureErrorDetector` 的 `Error messages for gesture ID`、dump 状态和有文本的 `addLog` 证据。

### 分支 B：pending-start 窗口内 listener 被摘除

典型时序：

1. `startRecentsAnimation` 已将 `mRecentsAnimationStartPending` 设为 true，但 `mController` 仍为空；
2. consumer switch、rotation 或取消路径触发 `onConsumerAboutToBeSwitched`/`removeListener`；
3. 原始 `AbsSwipeUpHandler` 已从 `RecentsAnimationCallbacks.mListeners` 移除；
4. 之后系统仍打印 `RecentsAnimationCallbacks.onAnimationStart` 或 `onTasksAppeared`，但这些回调只到达 `TaskAnimationManager` 的匿名 listener；
5. 同一旧 handler hash 没有 `AbsSwipeUpHandler.onRecentsAnimationStart`，也没有 `AbsSwipeUpHandler.onTasksAppeared without controller`；
6. 动画直到新手势接管或 ANR 后才 finish。

**关键判据是特定 listener 是否仍注册，不是 listener 总数是否为 0。** `TaskAnimationManager` 自己的 listener 仍会存在，所以框架级回调日志不能证明业务 handler 收到了回调。

#### B 的证据强度与结论用语

| 证据组合 | 可以下的结论 | 禁止的表述 |
|---|---|---|
| 同 hash 的 `OtherActivityInputConsumer: removeListener: handler=<X> ... startPending=true`，随后出现 framework start/tasks 回调，且 ANR 前无旧 handler finish | **直接证实：严格分支 B** | — |
| `startPending=true` → `onConsumerAboutToBeSwitched`/handler invalidated → 晚到的 framework start/tasks callback → 未 finish；旧版本没有 `removeListener` 诊断 | **高置信代码路径推断：与分支 B 一致** | “日志已证实 old handler 被移除” |
| 只有 `TaskbarOverlayController: onDestroy` 或 Taskbar destroy | **rotation/consumer-cleanup 候选入口** | “Taskbar 销毁必然调用 `removeListener`” |
| 只有 `RecentsAnimationCallbacks.onAnimationStart` / `onTasksAppeared` | 有 callback 分发，但**无法证明**指定 handler 是否收到 | “old handler 已收到/未收到该回调” |

旋转场景需额外验证链路：

```text
Launcher.onHandleConfigurationChanged
  -> StatefulActivity.reapplyUi
  -> StateManager.reapplyState(true)
  -> StateManager.cancelAnimation  // 只重置 StateManager.mConfig，不操作 Recents listener

PerDisplayTaskbarResource.onConfigurationChanged
  -> recreateTaskbarForDisplay -> destroyTaskbarForDisplay
  -> TaskbarActivityContext.onDestroy
  -> mInputConsumerCleanUpSet.forEach(onConsumerAboutToBeSwitched)
  -> [仅当 active consumer 确已注册到该 set]
     OtherActivityInputConsumer.removeListener
```

`TaskbarActivityContext.onDestroy()` 只会通知 `mInputConsumerCleanUpSet` 中的 consumer；不能仅凭 `TaskbarOverlayController: onDestroy` 推定 active `OtherActivityInputConsumer` 在集合内。并且 `removeListener()` 还可由手势结束/失效回调等其他入口触发，最终应以新版 `removeListener: handler=<X>` 原文定案。

不要把后续新建的 `h=<Y>` 当作旧手势 `h=<X>` 的回调证据。必须分别记录：

```text
old handler h=X: ACTION_DOWN -> removeListener -> no handler callback
new handler h=Y: later ACTION_DOWN/continue -> onRecentsAnimationStart
```

### 分支 C：仅有 pending-start 但 handler 仍在集合

如果 `onTasksAppeared without controller` 对同一 handler 打印，说明 handler 仍在 listener 集合中，但 controller 尚未建立或状态不一致。这不是“listener 已摘除”分支；应继续检查 start callback 顺序、controller 建立、取消和 one-shot finish 请求。

### 分支 D：普通应用/系统负载根因

只有在以下证据支持时，才把应用主线程、CPU、I/O、内存或 SystemUI 卡顿列为主因：

- ANR trace 明确显示目标应用主线程阻塞；或
- AnrManager 有 CPU TOTAL、iowait、PSI、Top CPU/内存进程等完整数据；或
- 目标应用在 `reportDrawFinished` 前已经出现明确 slow dispatch/绘制失败。

存在 `lowmemorykiller`、`Skipped frames` 或系统负载日志，只能先列为背景/放大因素，不能覆盖已经成立的 Recents + focus 证据链。

## 5. ActiveGestureLog 的限制与误判防止

- `ActiveGestureLog.addLog(...)` 的非 `NO_OP` 事件会即时输出文本并进入 gesture history；
- `ActiveGestureLog.trackEvent(...)` 使用 `CompoundString.NO_OP`，事件可能只进入内部状态/`encounteredEvents`，不会产生可 grep 的文本；
- 因此 **grep 不到某个 `trackEvent` 不等于该事件未执行**；
- `dumpsys activity`、`TouchInteractionService.dump` 或 `ActiveGestureErrorDetector` 的 `Error messages for gesture ID` 更适合验证状态机断点；
- 一次 dump 最多覆盖最近有限数量的 gesture（当前实现保留最多 15 个），采集延迟过大可能丢失目标 gesture。

同理，出现一条 `RecentsAnimationCallbacks.onTasksAppeared` 只证明至少一个 listener 收到回调，不证明指定的 `AbsSwipeUpHandler` 收到回调。

## 6. 结论规则

报告开头必须先给出：

- **是否手势相关**：是 / 高度疑似 / 证据不足 / 否；
- **置信度**：strong / medium / weak；
- **直接阻塞点**：例如“Calculator 已绘制，但 transition leash 使窗口 `NOT_VISIBLE`，InputDispatcher 等待焦点”；
- **根因链**：例如“pending-start 时旧 handler 被移出 listener 集合，动画无人 finish”。

推荐结论措辞：

> ANR 与手势/Recents 流程高度相关（confidence: strong/medium）。`am_anr` 前可定位到 `logId=<N>`、`h=<X>` 的手势；该手势在 `startPending=true` 后被取消/切换，Recents 动画在 ANR 前未完成 finish。目标应用已/未完成绘制；窗口焦点缺失由 `<具体 window/SF 证据>` 支持。`<缺失 trace/dump>` 仍需人工补证，因此不把未证实的应用线程/系统负载因素写成主因。

若只有 `input_interaction` 与 `am_anr` 接近，应写：

> 发现 Recents 输入交互与 ANR 时间接近，属于高优先级候选；当前没有足够证据证明动画未 finish 或导致窗口不可见。需要补抓 Recents 生命周期、InputDispatcher、window/SF 和 gesture dump 后再定根因。

## 7. 报告模板

````markdown
# Gesture ANR Analysis

## 1. Judgment
- gesture-related: yes / likely / insufficient / no
- confidence: strong / medium / weak
- ANR: <time>, <pid>, <package>, <reason>
- direct blocker: <focus/visibility/app-main-thread>
- suspected branch: <A/B/C/D/unknown>

## 2. Evidence timeline
| Time | ΔT from am_anr | Source | logId/handler | Event | Raw ref |
|---|---:|---|---|---|---|

（`Raw ref` 指向第 3 节的片段编号，如 `E3`）

## 3. Raw log excerpts（必填）
> 每条关键结论一个片段：逐字原文 + 来源文件（可得时加行号/logId/handler hash）+ 本片段能证明/不能证明什么。
> main logcat 实时行、bugreport/dumpsys 历史 dump、源码推断必须分开标注。

### E1 — <fact>（来源：`<file>[:line]`，logId=<N>，h=<X>）
<pre><code>&lt;verbatim lines, with 2-5 lines of context&gt;
</code></pre>
证明：<...>；不证明：<...>

### E2 — ...

## 4. Causal chain
1. Gesture start: <... (E1)>
2. Recents start/pending: <... (E2)>
3. Cancel, switch, rotation or listener change: <... (E3)>
4. Callback and finish: <... (E4)>
5. Window/focus and ANR: <... (E5)>

## 5. Root-cause candidates
| Candidate | Confidence | Supporting evidence (raw refs) | Counter-evidence/gap |
|---|---|---|---|

## 6. Evidence gaps
- [ ] ANR trace
- [ ] AnrManager CPU/PSI/meminfo
- [ ] dumpsys window / SurfaceFlinger
- [ ] ActiveGestureErrorDetector dump
- [ ] real-touch input instead of monkey injection
- [ ] `OtherActivityInputConsumer: removeListener: handler=<X>`（旧版本无此日志时，listener 摘除只能是推断）

## 7. Next capture / validation
<commands and reproduction steps, including the `rg -n -C` filters used>

## 8. Conservative JSON tail
```json
{
  "finalJudgment": false,
  "notRootCauseYet": true,
  "requiresHumanConfirmation": true
}
```
````

## 8. 已抓取日志的验证重点

本 skill 不执行实时复现，也不要求分析过程中连接设备。基于已有日志验证修复或判断风险时，重点检查：

1. `onConsumerAboutToBeSwitched` 在 pending 状态下是否先安装接管 listener；
2. `resetStateForAnimationCancel` 是否登记 pending-start one-shot finish；
3. `TaskAnimationManager.onRecentsAnimationStart` 是否识别 orphan listener 并 finish；
4. 同一 `logId` 是否出现 `finishRecentsAnimation`/`cleanUpRecentsAnimation`；
5. 是否出现 `no focused window` ANR；
6. 同一手势的 listener/hash 是否闭环；
7. 提供的日志是否包含足够的 `input_focus`、window/SF 和 gesture dump 证据。

如果需要补充日志，应在分析报告的 evidence gaps 中说明需要用户提供哪些已抓取文件；不要在 skill 内自动执行 `adb`、实时 dump 或复现脚本。

## 9. 参考路由

本仓库内的通用分析参考：

- [`anr-trace-analysis.md`](anr-trace-analysis.md)：Trace 专项边界；
- [`anr-eventlog-analysis.md`](anr-eventlog-analysis.md)：`am_anr` 与焦点/输入时间线；
- [`anr-logcat-analysis.md`](anr-logcat-analysis.md)：Logcat、AnrManager 与 window/surface 证据；
- [`anr-analysis.md`](anr-analysis.md)：最终综合分析与写回契约。

以下是 Launcher/Quickstep checkout 中可能存在的条件参考，并非本仓库内置文件。只有用户提供相应 checkout 且路径实际存在时才读取；否则将源码/案例交叉验证记为 evidence gap，不得假装已读取：

- `doc/recents_animation_hang_anr_analysis.md` 与相关 `anr-reports/`：已知案例和修复分支；
- `quickstep/src_protolog/com/android/quickstep/util/ActiveGestureLog.java`：`addLog`、`trackEvent(NO_OP)` 和 gesture dump 行为；
- `quickstep/src/com/android/quickstep/TaskAnimationManager.java`：Recents start/finish 状态；
- `quickstep/src/com/android/quickstep/inputconsumers/OtherActivityInputConsumer.java`：consumer switch、listener 移除和接管；
- `tools/repro_recents_hang_anr.sh`：竞态复现参考。
