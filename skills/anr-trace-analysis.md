---
name: anr-trace-analysis
description: ANR Trace 专项分析 skill。Use when analyzing the filtered Trace section of an ANR context, including main thread state, stack frames, schedstat, locks, Binder/render waits, Deadlock Detection, Trace Hints, trace coverage boundaries, and trace-only evidence gaps.
---

# ANR Trace 分析 Skill

目标：只基于 Trace 证据形成可审计的 Trace 专项结论。不要分析 EventLog / Logcat 的根因，只标记需要它们补证的缺口。Trace 结论必须同时说明“已覆盖能力”和“不能仅凭单份 trace 证明的边界”。

## 输入

- `### Trace` 原始证据块。
- Trace 前的 `### Deadlock Detection`、`### Trace Hints`。
- 如需校验能力边界，可回源阅读 `wiki/ANR-trace覆盖清单.md`、`wiki/ANR-trace文件分析.md` 与 `wiki/ANR基础知识.md`。本 skill 已合并这些文件中与 Trace 专项分析相关的核心内容。

## 当前实现落点与可用能力

核心实现主要落在：

- `anr_evidence/trace_preprocessor.py`
- `anr_evidence/extractor.py`
- `anr_evidence/normalizer.py`
- `anr_evidence/analyzer.py`
- `scripts/anr_preprocessor.py`

当前 Trace 侧可稳定依赖的能力：

1. trace section 切分与压缩。
2. 主线程优先识别，并保留 ANR 相关线程。
3. Binder / 锁竞争 / native poll / futex / GPU wait 等基础 block hint 提取。
4. 线程基础字段提取：
   - `threadName`
   - `tid`
   - `sysTid`
   - `prio`
   - `daemon`
   - `group`
   - `sCount/dsCount/ucsCount`
   - `flags/obj/self`
   - `nice/cgrp/sched/handle`
   - `linuxState/schedstat/utm/stm/core/hz`
   - `heldMutexes`
   - `waitObject`
   - `lockOwnerTid`
5. 主线程关键帧提取：
   - `mainThreadNativeTopFrame`
   - `mainThreadJavaTopFrame`
   - `mainThreadLooperFrame`
6. Phase2 / Phase3 / report / delivery 可直接展示主线程摘要。


## ANR 基础知识速查（Trace 分析必用）

### Linux 进程/线程状态

Trace 中的 `state` / Linux 状态只描述采样瞬间，不能单独推出持续超时。解释时按下表保守归因：

| 状态 | 含义 | Trace 分析用法 |
|---|---|---|
| `R` | Running / Runnable，正在运行或在运行队列中 | 可支持 `runnable-cpu` 候选；仍需 CPU/调度时序证明持续性 |
| `S` | Interruptible sleep，可中断睡眠，等待事件 | 常见于 futex、epoll、binder 等等待；需结合栈帧解释等待对象 |
| `D` | Uninterruptible sleep，不可中断睡眠，常见于同步 IO / 内核等待 | 可支持 IO / binder / kernel wait 候选；需结合 native frame 与系统负载 |
| `T` | Stopped / traced，停止或被调试 | 可能与调试/暂停相关；需避免直接归因为业务阻塞 |
| `Z` | Zombie，线程/进程已终止 | 通常不是主线程直接阻塞根因 |
| `<` / `N` | 高/低优先级任务 | 作为调度上下文旁证，不能单独定因 |
| `L` | 有内存页被锁定 | 多为底层/实时任务上下文，需结合 native 栈 |
| `s` / `l` | 会话首进程 / 多线程进程 | 背景属性，不是根因 |

### 常见 ANR 原因分类

Trace 专项分析只负责识别“直接阻塞形态”，最终归因需 Final ANR 跨源确认：

- 应用侧耗时：死循环、主线程 IO、主线程大数据处理、同步数据库/网络、UI 渲染重任务。
- 应用侧锁问题：主线程等待子线程锁、锁链、死锁、owner 持锁做 IO/等待。
- 应用侧内存压力：内存占用过高导致频繁 GC、交换、分配变慢；Trace 只能提示 STW/VM wait，需要 meminfo/GC/PSI 补证。
- 系统侧 CPU 抢占：其它前台/高负载进程导致目标线程调度不足；Trace 只能给 schedstat/Linux state 旁证。
- 系统服务无响应：主线程 Binder 等 system_server/HAL/服务端回包；需要服务端 trace 或 Binder wait-chain。
- 其它应用占用内存/IO/CPU：作为外部压力候选，不能直接定责目标应用。

### Trace 字段含义

输出 Trace evidence 时优先使用这些字段名，避免混淆：

| 字段 | 含义 | 注意事项 |
|---|---|---|
| `main` | 主线程标识 | 非主线程通常为 `Thread-X` 或业务命名线程 |
| `prio` | Java/ART 线程优先级 | 默认常见为 5；不是 Linux nice |
| `tid` | ART 线程唯一标识 | 不是 Linux 线程号 |
| `sysTid` | Linux 线程号 | 主线程 `sysTid` 通常等于进程 pid |
| `group` | 线程组名称 | 用于区分 main/system/业务线程组 |
| `sCount` | 线程被挂起次数 | 只能作为暂停历史线索 |
| `dsCount` | 被调试器挂起次数 | 高值提示调试/暂停干扰，但不能单独定因 |
| `obj` | Java Thread 对象地址 | 用于引用原始 trace，不解释业务含义 |
| `self` | Native Thread 地址 | 用于引用原始 trace，不解释业务含义 |
| `nice` | Linux 调度 nice 值 | 越大优先级越低；需结合 cgrp/sched |
| `cgrp` | 调度归属组 | top-app/foreground/background 会影响调度解释 |
| `sched` | 调度策略和优先级 | 作为调度上下文，不单独定因 |
| `handle` | 线程处理函数地址 | 低层字段，通常只记录 |
| `schedstat` | runNs / waitNs / timeSlices | 执行时间、等待时间、时间片；等待远大于运行只支持调度等待候选 |
| `utm` / `stm` | 用户态 / 内核态 jiffies | 判断 CPU 消耗方向，需结合 HZ 和时间窗口 |
| `core` | 最后运行 CPU 核 | 仅作调度上下文 |
| `refrigerator` | 进程/线程冻结相关状态 | 多线程处于 `__refrigerator` 时提示冻结导致 ANR 的候选 |

### Thread.java ↔ ART Thread.cpp 状态映射

| Java 状态 | ART/native 状态 | Trace 解释 |
|---|---|---|
| `TERMINATED` | `ZOMBIE` | 已死亡 |
| `RUNNABLE` | `RUNNING` / `RUNNABLE` | 可运行或正在运行 |
| `TIMED_WAITING` | `TIMED_WAIT` | 带超时的 wait/sleep/join |
| `BLOCKED` | `MONITOR` | 等待对象锁；需找 owner thread |
| `WAITING` | `WAIT` | 无超时 wait；需找等待对象和唤醒方 |
| `NEW` | `INITIALIZING` / `STARTING` | 新建或启动中 |
| `RUNNABLE` | `NATIVE` | 正在执行 JNI/native；必须看 native top frame |
| `WAITING` | `VMWAIT` | 等待 VM 资源；需结合 GC/STW/Debugger 证据 |
| `RUNNABLE` | `SUSPENDED` | 暂停，常见于 GC 或 debug；单线程不能证明 STW |
| unknown | `UNKNOWN` | 解析不明，列为证据缺口 |

### 负载和内存基础解释（Trace 中只能作为旁证）

如果 Trace 或 AnrManager 附近出现 CPU/内存摘要，应按以下规则解释：

- CPU 字段：`user` 为用户态，`kernel` 为内核态，`iowait` 为等待 IO，`irq/softirq` 为硬/软中断。
- `iowait` 高：提示 IO 瓶颈候选，继续找主线程同步 IO、major faults、mmc/blk 相关日志。
- 单进程 CPU 百分比不是以 100% 为上限；多核设备上限约等于核心数 × 100%。
- `ago` 表示 ANR 前 CPU 使用情况，`later` 表示 ANR 后或 dump 后情况；Final ANR 需区分根因窗口与 dump 污染。
- 内存字段如 `Free memory until OOME` 很小，只能提示应用接近 OOM；需 meminfo/GC/PSI/LMK 交叉验证。
- EventLog `am_meminfo` 的 Cached + Free 可粗略判断系统可用内存；低内存阈值需结合设备总内存，不要单凭一行定责。

## Trace 文件阅读顺序与归属判断

1. 先读 section 头：`----- pid <pid> at <time> -----` 或 `----- pid <pid> -----`，记录 dump 时间、pid。
2. 再读 `Cmd line: <process>`，确认该 trace section 对应的进程名。
3. 再读 runtime 概况：`JNI`、`DALVIK THREADS`、`(mutexes: ...)`，判断是否存在调试/VM/锁统计上下文。
4. 最后读线程块：优先 main，其次 owner/peer、Binder、RenderThread、Signal Catcher、JDWP、Input/Surface/业务线程。
5. 不要把“某应用出现在 trace 文件里”直接当作根因。trace 可能包含多个活着的进程；只有 ANR anchor / section 顶部 / `Cmd line` / EventLog `am_anr` 共同指向时，才可把该进程作为目标进程。
6. 如果目标应用不在顶部或不是 `am_anr` 进程，只能说明它在 ANR 采样时存在；是否相关需跨源确认。

## 特殊线程和 mutex 缩写

### 常见特殊线程

| 线程 | 作用 | 分析要求 |
|---|---|---|
| `Binder_*` / `Binder Thread #N` | 进程 Binder 线程池，处理 Binder 请求 | 主线程等 Binder 时，要看 Binder 线程是否饱和、阻塞、或在处理对端调用 |
| `Signal Catcher` | 接收/处理 `SIGQUIT` 等信号并触发 trace dump | 它 RUNNABLE 通常只是 dump 机制，不是 ANR 根因 |
| `JDWP` | 虚拟机调试支持线程 | 通常 daemon，可作为是否被调试/暂停的旁证 |
| `RenderThread` / `android.anim` | 渲染/动画相关线程 | main 在 doFrame/fence/Surface 时需联动判断 |
| `FinalizerDaemon` / GC/Heap worker | GC/对象回收相关 | 仅在多线程 VM/GC 暂停、内存压力证据一致时升级为候选 |

### mutexes 缩写

`DALVIK THREADS` 后的 `(mutexes: ...)` 缩写含义：

| 缩写 | 含义 |
|---|---|
| `tll` | thread list lock |
| `tsl` | thread suspend lock |
| `tscl` | thread suspend count lock |
| `ghl` | gc heap lock |
| `hwl` | heap worker lock |
| `hwll` | heap worker list lock |

这些字段是 VM/ART 锁统计上下文；只有和具体线程栈、held/waiting lock、GC/暂停证据一致时，才可进入候选链。

## Java / ART / Linux / Perfetto 根因映射

Trace 中 main 的状态是 ART/CPP Thread 状态，需要映射到 Java 状态、Linux sched state、Perfetto thread_state 后再判断：

| Java State | ART State | Linux sched state | Perfetto 典型表现 | 关键函数/等待点 | 常见原因 | ANR 归因候选 |
|---|---|---|---|---|---|---|
| `RUNNABLE` | `RUNNING` | `R` | Running slice，占用 CPU | — | 主线程执行耗时逻辑 | 主线程执行超时 |
| `RUNNABLE` | `RUNNABLE` | `R` / runnable queue | runnable 但未调度 | — | CPU 抢占 / 调度延迟 | 调度延迟型 ANR |
| `RUNNABLE` | `NATIVE` | `R` / `S` / `D` | Native slice 或内核等待 | `binder_ioctl` / `eglSwapBuffers` / `poll` / IO | JNI / Binder / 渲染 / IO | Native 阻塞候选 |
| `RUNNABLE` | `SUSPENDED` | 停止/非普通 sched 态 | 无 CPU 执行 | — | GC / Debug suspend | STW / 调试暂停候选 |
| `BLOCKED` | `MONITOR` | `D` / `S` | futex_wait / monitor contention | `futex_wait` | synchronized 锁竞争 | 锁竞争 ANR |
| `WAITING` | `WAIT` | `S` | wait / park | `Object.wait` / `park` | 逻辑等待、缺唤醒 | 逻辑等待候选 |
| `TIMED_WAITING` | `TIMED_WAIT` | `S` | sleep / wait timeout | `nanosleep` / timed futex | sleep / timeout wait | 主线程 sleep 设计问题候选 |
| `WAITING` | `VMWAIT` | `S` | VM 内部等待 | GC / ClassLinker | 等待 VM 资源 | VM 资源阻塞候选 |
| `TERMINATED` | `ZOMBIE` | — | 无 slice | — | 线程结束 | 通常忽略 |
| `NEW` | `INITIALIZING` / `STARTING` | — | 无或启动中 | `clone` / `pthread_create` | 创建/启动中 | 通常忽略 |
| — | `UNKNOWN` | `?` | 不明确 | — | trace 缺失/解析不明 | 标记异常/缺口 |

自动分析时遵守以下门槛：

- main 处于 `BLOCKED` / `WAITING` / `TIMED_WAITING`，通常说明主线程正在函数阻塞/等待；仍需通过栈帧、等待对象、owner/peer 证明具体链路。
- main 看起来无异常或在 `nativePollOnce`，优先排查 CPU 负载、内存环境、系统服务/跨进程等待、窗口/焦点/渲染等外部因素。
- `MONITOR` 多由同步块/同步方法造成，必须找 owner thread。
- `SUSPENDED` 在 debugger 或 GC 暂停中都可能出现，必须用 `dsCount`、JDWP、GC/STW、全线程状态做区分。

## Trace 典型形态速查

### 主线程同步 IO / 文件写入

特征：main 栈顶为 `libcore.io.Posix.open`、`FileInputStream/FileOutputStream`、业务日志/文件写方法，Looper/Receiver/ActivityThread 在栈底。

结论边界：Trace 可证明主线程在同步 IO；是否超过 ANR 阈值、是否因系统 IO 压力放大，需要 Logcat/AnrManager `iowait`、major faults、block/mmc 证据补证。

### 主线程等待 MessageQueue / 正常 idle

特征：`Object.wait` → `MessageQueue.next` → `Looper.loop`，或 native `epoll_wait` / `nativePollOnce`。

结论边界：可能是空闲快照，不等于根因；如果 EventLog/Logcat 指向 no-focus/input timeout，要把 trace 标成 `idle-or-ambiguous` 或替罪羊候选。

### 主线程等待业务对象 / GLSurfaceView / 第三方 SDK

特征：`Object.wait` / `park` 后接业务对象，例如 `GLSurfaceView$GLThreadManager`、地图/相机/播放器 SDK 的 pause/destroy/release。

结论边界：Trace 可证明主线程等待某对象；必须找唤醒方/owner/GLThread/SDK 线程，否则只能输出“逻辑等待候选”。

### 主线程锁竞争 / 死锁

特征：`waiting to lock <obj> ... held by thread N`，或 futex/monitor contention；owner 线程同时持锁并执行/等待。

结论边界：有 owner thread 和锁边才能形成锁竞争链；有环并被 Deadlock Detection 标记时可升为高置信死锁候选。

### Binder 线程池与主线程 Binder wait

特征：主线程 `BinderProxy.transact` / `waitForResponse` / `talkWithDriver`；Binder 线程出现 `joinThreadPool` / `getAndExecuteCommand` / `binder_ioctl`。

结论边界：Trace 可证明 Binder 等待；根因归属必须找 server 端或 Binder wait-chain，不能只因 `Binder_*` 存在就判断 Binder 池异常。

### Suspended / Debug / GC 干扰

特征：main 或大量线程 `SUSPENDED`，`sCount/dsCount` 异常，JDWP 活跃，或 VM/GC 相关帧集中。

结论边界：单线程 Suspended 不能证明 STW；多线程同时暂停 + GC/Debugger 证据一致时才作为 GC/debug 暂停候选。

## 固定步骤

1. 锚定 trace section：sourcePath、pid/process、selectedSectionIndex、selectedSectionTimestamp、与 ANR anchor 的 delta。
2. 展开 main thread：name/tid/sysTid/prio、ART/Java/Linux state、group/sCount/dsCount/ucsCount/flags/obj/self、nice/cgrp/sched/handle、core、schedstat/utm/stm/HZ。
3. 展开栈：top native frame、top Java frame、looper frame、held mutexes、waitObject/lockOwnerTid、heldLocks/waitingLocks。
4. 判断直接阻塞类型：lock / binder / io / db / network / render / nativePoll / runnable-cpu / scheduler-delay / idle-or-ambiguous。
5. 若有锁：沿 waiter → owner → lockObject 分析 ownerThread；有锁环时引用 Deadlock Detection hint id。
6. 若有 Binder：说明 client wait frame、binderCallKind、native frame 关键词；没有对端 trace/binder info 时必须列为缺口。
7. 若有 Render/GPU/STW/CPU summary：说明它与 main thread 的关系，只能作为旁证时明确降级。
8. 对照覆盖边界输出：已证明、部分覆盖、未证明、需要 EventLog/Logcat/Perfetto/CPU/GC 补证。
9. 输出 Trace-only 结论：直接阻塞点、关联线程/hints、证据强度、缺口和置信度。

## 字段覆盖对照

### 线程自身信息

以下静态结构信息已基本覆盖完整，可作为 Trace 专项分析的主要证据：

| 项目 | 覆盖状态 | 使用方式 |
|---|---|---|
| 主线程识别 (`main`) | 已覆盖 | 优先定位 ANR 进程 main thread |
| `prio` / `tid` / ART state | 已覆盖 | 解释线程头；注意 `tid` 是 ART 线程标识 |
| `group/sCount/dsCount/ucsCount/flags/obj/self` | 已覆盖 | 展开线程对象与调试/挂起上下文 |
| `sysTid/nice/cgrp/sched/handle` | 已覆盖 | 展开 Linux 调度上下文；`sysTid` 才是 Linux 线程号 |
| `state/schedstat/utm/stm/core/HZ` | 已覆盖 | 分析运行/等待/时间片，但不能单独证明持续超时 |
| `held mutexes` | 已覆盖 | 识别 ART/JNI/monitor 等持有锁 |
| Java/native stack 首帧 | 已覆盖 | 输出 top native/java/looper frame |
| `waiting to lock ... held by thread X` | 已覆盖 | 通过 `waitObject` + `lockOwnerTid` 建锁等待边 |

### 线程状态映射边界

| 规则项 | 覆盖状态 | 分析要求 |
|---|---|---|
| ART → Java 状态映射 | 已覆盖 | 使用 `artThreadState` + `javaThreadState` |
| `BLOCKED/MONITOR` | 部分覆盖 | 可识别 monitor/lockOwnerTid；完整闭环需 owner thread 证据 |
| `WAIT/VMWAIT/TIMED_WAIT` | 部分覆盖 | 可做基础映射；跨线程/时序原因需补证 |
| `SUSPENDED` / STW | 部分覆盖 | 单份 trace 只能提示；确认 STW 需全线程同时性和 GC/暂停证据 |
| `UNKNOWN` | 部分覆盖 | 解析不到时标记 unknown，并列为证据质量缺口 |

## 自动分类规则覆盖边界

### 1. 主线程 CPU 执行超时

- 可提取：`linuxState`、`schedstat`、`utm/stm`、主线程关键 Java/native 帧。
- 不可仅凭单份 trace 稳定证明：持续 `>5s`、CPU usage 高、Runnable 但长期未被调度。
- 输出要求：最多写为 `runnable-cpu` 或 `scheduler-delay` 候选，并要求 CPU/Perfetto/logcat 负载补证。

### 2. 锁竞争 ANR

- 可提取：`blockHint = monitor_contention / lock_contention / futex_wait`、`waitObject`、`lockOwnerTid`。
- 分析要求：必须反查 owner thread；若 owner 正在 RUNNING/IO/WAITING/BLOCKED，要写明 owner 状态与栈。
- 不可过度承诺：没有 owner thread 或锁环时，不能升级为完整锁竞争根因链。

### 3. Binder 阻塞

- 可提取：`binder_wait`、`binder_reply_wait`、`binder_backlog`、`nativeTopFrame`。
- 重点识别 native frame：`binder_ioctl`、`talkWithDriver`、`waitForResponse`、`joinThreadPool`、reply wait。
- 不可过度承诺：没有 system_server/HAL/server 侧 trace 或 binder wait-chain 时，只能证明主线程在 Binder 直接阻塞，不能定责对端。

### 4. InputDispatcher ANR

- 可提取：`focus_window_wait`、`input_dispatch_wait`、trace 中 `InputDispatcher` / `no window has focus` / `input dispatching` 线索。
- 不可仅凭 trace 完成：dispatcher finish 等待关系、timeout 真实触发点、窗口/焦点顺序。
- 输出要求：标记需要 EventLog/Logcat 侧 InputDispatcher、WindowManager、focus/window/surface 证据补证。

### 5. 调度延迟 / CPU 抢占

- 可提取：`linuxState`、`schedstat`、`nice/core/cgrp`。
- 不可仅凭 trace 稳定证明：长时间未被调度、系统 CPU 高负载、调度饥饿持续窗口。
- 输出要求：只有当 schedstat 明显支持时列为候选；需要 CPU load、AnrManager CPU TOTAL、Perfetto thread_state 等补证。

### 6. GC 暂停 / STW

- 可提取：`artThreadState`、`javaThreadState`、部分 GC/VM wait frame。
- 不可仅凭单线程证明：所有线程同时 `SUSPENDED/Stopped`、GC/Debugger/Signal-Catcher 暂停原因。
- 输出要求：必须写明是否具备全线程状态聚合；没有则只能作为候选或缺口。

### 7. Render / GPU 卡顿

- 可提取：`gpu_wait`、`threadRole = render`、RenderThread 线程识别、main thread `doFrame`/Choreographer 线索（若存在）。
- 不可仅凭 trace 闭环证明：RenderThread 与 main 成对关联、GPU/fence 未完成、SurfaceFlinger/frame timeline 责任。
- 输出要求：需要 SurfaceFlinger、frame timeline、Perfetto 或 logcat 侧渲染证据补证。

## 最可靠结论与禁止过度承诺

### 已可稳定依赖的 Trace 结论

当前 Trace 分析最可靠的结论范围：

1. 从真实 trace 中正确抽出主线程完整块。
2. 提取主线程调度/上下文字段。
3. 提取主线程 top native/java/looper frame。
4. 识别基础阻塞类别：
   - focus window
   - input dispatch
   - binder
   - monitor / lock
   - futex
   - native poll
   - gpu wait
5. 为最终 ANR 阶段输出结构化主线程摘要。

### 不能仅凭单份 Trace 稳定给出的结论

以下结论必须降级为候选，并要求跨源/时序补证：

1. CPU 抢占型 ANR。
2. STW/GC 暂停闭环。
3. Binder wait chain 根因归属。
4. 锁 owner 线程完整闭环。
5. Render/GPU 未完成闭环。

## 输出格式

```markdown
#### AI Analysis — Trace
- Trace section: ...
- Main thread: ...
- Direct trace blocker: ...
- Related threads / hints: ...
- Coverage boundary: 已覆盖/部分覆盖/未证明；需要哪些跨源补证
- Trace-only conclusion: ...
- Evidence gaps: ...
- Confidence: high|medium|low
```

## 保守规则

- `nativePollOnce` 只能说明该快照在等消息/epoll，不能单独定根因。
- 单份 trace 的 RUNNABLE 快照不能证明“持续 >5s”。
- 没有 owner thread 时，锁等待不能升级为完整锁竞争链。
- 没有对端时，Binder 只能是直接阻塞点，不能定责 server。
- 没有全线程同时性和 GC/暂停证据时，不能把 `SUSPENDED` 判成 STW 根因。
- 没有 RenderThread/SF/fence/frame timeline 闭环时，GPU/Render 只能是候选。
- Metadata 是索引，原始 trace 行和 hint id 才是引用证据。

## 后续增强 TODO（用于标记缺口，不作为当前结论）

P1：owner thread 反查、Binder native frame 归一化、main `doFrame` / Choreographer 识别、RenderThread block 归并、`UNKNOWN/SUSPENDED/VMWAIT` findings。

P2：EventLog/Logcat 联动 InputDispatcher finish、CPU usage/loadavg/sched latency、Perfetto thread_state/slice、GC/STW 同时性判定。

P3：在 report/delivery 增加“锁竞争 / Binder / Render / CPU 抢占覆盖状态”附录，并在 remediation 中按 `lockOwnerTid / binder_wait / gpu_wait` 细化建议模板。
