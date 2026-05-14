---
name: anr-load
description: ANR trace 字段、线程状态、CPU、内存、IO、负载与监控信号分析。Use when interpreting traces.txt thread headers, ART/Java/Linux thread states, schedstat, utm/stm, lock owner, Binder/render waits, Load, CPU usage, iowait, kswapd, PSI memory, LMK, mmcqd/exe_cq, WatchDog, Looper delay, or ANR monitoring/auto-attribution signals.
---

# ANR 负载分析与监控

负载证据用于解释“线程为什么没有及时执行”。它可以是根因，也可以只是放大因素。必须和 ANR 类型、主线程/对端堆栈、时间窗口一起判断。

## 1. Trace 字段速查

| 字段 | 诊断含义 |
|---|---|
| `prio` / `nice` | 调度优先级；nice 越低优先级越高 |
| `tid` / `sysTid` | Java/ART 线程号与系统线程号；主线程 sysTid 通常等于 pid |
| `group/sCount/dsCount/ucsCount` | 线程组、挂起次数、调试挂起次数；`dsCount` 不能单独定责 |
| `obj/self` | Java 线程对象地址与 native thread 地址，用于人工比对 |
| `cgrp/sched/handle` | 调度组、调度策略/优先级、线程处理函数地址 |
| `state` | Java/ART 状态；结合 Linux state 判断 Runnable、Sleeping、Blocked、Native |
| `utm` / `stm` | 用户态/内核态 CPU 时间；通常以 jiffies 计，需结合 `HZ` |
| `schedstat` | 三元组 `runNs waitNs timeSlices`；可辅助判断 CPU 执行、等待调度或 dump 采样局限 |
| `core` | 最后运行 CPU 核；只作辅助，不作根因 |
| `held mutexes` | ART/runtime mutex；`mutator lock` shared held 常见不等于业务锁 |
| `waiting to lock` / `held by` | Java 锁竞争核心证据 |

### 1.1 线程状态映射

| Java 语义 | ART/trace 状态 | Linux state | 分析要点 |
|---|---|---|---|
| RUNNABLE | RUNNING/RUNNABLE/NATIVE | R/S | 可能是 CPU 执行、JNI/native 调用或在 native 中睡眠；必须看栈帧 |
| BLOCKED | BLOCKED/MONITOR | S/D | 等 Java monitor/futex；沿 `held by thread` 找 owner |
| WAITING | WAIT/VMWAIT | S | 等待 notify/VM 资源；找唤醒方或 GC/ClassLinker 旁证 |
| TIMED_WAITING | TIMED_WAIT | S | sleep/wait(timeout)/join(timeout)；主线程 sleep 多为设计问题 |
| SUSPENDED | SUSPENDED | 非普通调度态 | 可能 GC/debugger STW；需多线程同时暂停或 GC/debug log |
| TERMINATED | ZOMBIE | — | 通常忽略 |
| UNKNOWN | UNKNOWN/缺失 | ? | 标记采样/解析缺口，不强行归因 |

## 2. 主线程状态判断

| 状态/堆栈 | 初步判断 | 下一步 |
|---|---|---|
| `Runnable` + 业务栈 | 业务计算/布局/循环或 CPU 竞争 | 看 schedstat、CPU hog、Slow dispatch |
| `Blocked` | Java 锁竞争 | 沿 `held by tid` 找持锁线程 |
| `Waiting/TimedWaiting` | wait/latch/sleep | 找唤醒方或 sleep 来源 |
| `BinderProxy.transactNative` / `waitForResponse` | Binder 同步等待 | 找 Binder 对端堆栈/服务端负载 |
| `nativePollOnce` | Looper 空闲或等待消息 | 结合 ANR 类型；No Focus/系统侧常见，不能单独定根因 |
| `futex` / syscall / IO | Native 阻塞 | 判断锁、IO、Surface、Vsync、内核等待 |

## 2.1 Trace 自动分类证据门槛

| 候选类型 | 必要证据 | 不能单靠什么下结论 |
|---|---|---|
| 主线程 CPU 执行超时 | main RUNNABLE/R + 业务/布局/循环栈 + Slow dispatch 或 CPU 时间窗口 | 单份 trace 的 RUNNABLE 快照 |
| 调度延迟 | main runnable 但 `schedstat.waitNs >> runNs` + CPU TOTAL/Load 高或外部 top 进程 | schedstat 单独一项 |
| 锁竞争 | main BLOCKED/MONITOR + `waiting to lock` + owner thread 栈 | 没有 owner 的锁对象 |
| 死锁 | 稳定锁环 waiter→owner→...→waiter，最好跨 trace 一致 | 普通链式阻塞 |
| Binder 阻塞 | `BinderProxy.transact`/`waitForResponse`/`talkWithDriver` + 对端进程/线程或 binder info | 只有客户端栈 |
| IO/DB/SP | 主线程 `open/read/write/fsync/sqlite/QueuedWork` + iowait/IO 时间窗口或业务栈 | dump 后 iowait |
| GC/STW | 多线程 SUSPENDED/VMWAIT + GC/ART log/PSI memory | 单线程 SUSPENDED |
| Render/GPU | main doFrame/ThreadedRenderer + RenderThread/SF/fence/buffer 旁证 | 单条 `dequeueBuffer` |

## 3. CPU 负载

1. 读取 `AnrManager: CPU usage from Xms to Yms ago` 的统计窗口；越靠近 ANR 前越可靠。
2. **先看 `TOTAL`**：全机 CPU 接近或超过 95% 时，高 CPU 本身可能导致调度延迟；80%+ 可作为重要诱因；同时读取 `iowait` 判断是否 IO 等待拉高。
3. **再看 Top 进程/线程**：若 ANR 进程自身 top 且 main/业务线程 runnable，偏应用侧 CPU 耗时；若其他进程/system_server top，偏外部抢占。
4. **高负载进程必须联动内存证据**：
   - 目标包高负载：继续查 meminfo/am_meminfo/am_pss、Java/native heap、GC、LMK/OOM、PSI memory，判断是否应用内存泄漏、内存抖动或 OOM 放大导致。
   - 其它进程高负载：同样检查该进程内存/IO/GC/LMK 证据；若成立，作为外部系统压力或跨进程影响候选，而不是直接归因到目标应用。
   - 缺少内存证据时，不得直接下“内存泄漏”或“OOM”结论，只能列为证据缺口。
   - 在本工具生成的 cache 中，`Meminfo Target/High-Load Follow-up` 是 AnrManager Top CPU/IO 后的固定后续证据，必须紧跟 AnrManager 分析使用。
5. 区分 CPU 使用率和 Load Average：Load 高但 CPU usage 不高时，可能包含不可中断 IO/等待。
6. `schedstat` 中等待时间远大于运行时间，支持 CPU 抢占/调度延迟判断。

## 4. IO 负载

| 信号 | 解释 |
|---|---|
| `iowait` 高 | CPU 等待 IO，线程可能看似 sleeping/native |
| `mmcqd` / `mmc-cmdqd` / `exe_cq` Top | 存储队列压力，常见系统侧 IO 瓶颈 |
| major fault 高 | 缺页读盘，可能受内存/IO 双重影响 |
| 主线程 `read/write/fsync/open` | 应用侧主线程阻塞 IO，尤其 DB/SP/文件 |

IO 结论必须和时间窗口匹配；dump 后的 IO 高不能直接解释 dump 前 ANR。

## 5. 内存压力

| 信号 | 解释 |
|---|---|
| `kswapd0` Top | 内核回收页，说明内存压力可能影响调度 |
| `/proc/pressure/memory` some/full 高 | 进程或全局因内存回收停顿 |
| `lowmemorykiller` | 系统杀进程；adj 越低被杀说明越严重 |
| Free/Available 极低、swap 活跃 | 可能引发启动慢、fork 慢、GC/缺页变多 |
| 目标进程 heap 持续增长/GC 频繁 | 应用泄漏或内存抖动可能是上游诱因 |

## 6. Looper 与监控信号

- `Slow dispatch took Xms`：某条消息执行慢，常能直接关联主线程耗时。
- `Slow delivery took Xms`：消息从入队到执行延迟，常见于前序消息堵塞或 CPU 抢占。
- `Slow Looper main ... late`：主线程累计延迟。
- WatchDog/BlockCanary/Raster/自动归因平台信号只能辅助定位，最终仍需 trace/log 证据闭环。

## 7. 抖音实践可复用经验

- Barrier 导致主线程“假死”：关注 MessageQueue sync barrier、异步消息、Choreographer/Raster 调度。
- SharedPreferences 等待：关注 `QueuedWork`、`apply()` 异步写入、生命周期等待、锁持有。
- 自动归因：先确定问题区间，再粗归因（CPU/IO/内存/锁/Binder），最后细归因到线程/方法/业务场景。

## 8. 负载结论模板

```text
Load window: <CPU usage from ... ago>
Total first: CPU TOTAL=<...>, iowait=<...>, Load=<...>
Top consumers: <target package high?|other process high?>
Memory follow-up: <target/other process meminfo|PSI|GC|LMK|OOM evidence or missing>
Affected ANR path: <main/binder/worker was runnable|blocked|io>
Conclusion: <root cause|amplifier|not enough evidence>
```

## 回源阅读

- [../ANR基础知识.md](../ANR基础知识.md)
- [../ANR-trace文件分析.md](../ANR-trace文件分析.md)
- [../ANR-trace覆盖清单.md](../ANR-trace覆盖清单.md)
- [../ANR-规范.md](../ANR-规范.md)
- [../DouYin/](../DouYin/)
- [../实例/ANR-CPU.md](../实例/ANR-CPU.md)、[../实例/ANR-负载过高.md](../实例/ANR-负载过高.md)、[../实例/ANR-内存.md](../实例/ANR-内存.md)
- [../MTK/swt/14.CPU.md](../MTK/swt/14.CPU.md)、[../MTK/swt/15.Low Memory.md](../MTK/swt/15.Low%20Memory.md)、[../MTK/swt/16.IO Check.md](../MTK/swt/16.IO%20Check.md)
