---
name: anr-root-cause
description: ANR 根因定位与案例模式。Use when matching trace/log evidence to root-cause chains such as main-thread work, lock/deadlock, Binder blocked or exhausted, CPU overload, memory pressure, IO bottleneck, Surface/Buffer/Vsync, startup/no-focus, app killed/disabled, freeze, Broadcast timeout, or MTK SWT.
---

# ANR 根因定位

根因定位要分层：**直接阻塞点**（线程当前卡在哪里）不一定等于 **根因**（为什么卡在那里）。先建立证据链，再给置信度。

## 1. 快速决策表

| 直接证据 | 可能根因 | 必要补充证据 |
|---|---|---|
| main `Runnable` + 业务栈 | 主线程耗时/复杂布局/大循环 | schedstat run time、Slow dispatch、CPU top |
| `waiting to lock ... held by tid=N` | 锁竞争 | 持锁线程堆栈、持锁耗时、是否业务锁 |
| 多线程互相 `held by` | 死锁 | 完整锁环 A→B→C→A |
| `BinderProxy.transactNative` / `waitForResponse` | Binder 对端慢/死锁/线程池耗尽 | binder info、对端进程/线程 trace |
| 多个 Binder 线程忙且 pending | Binder 线程池耗尽 | binder transaction 队列、server 是否都阻塞 |
| 主线程 `read/write/fsync/sqlite/SP` | 主线程 IO | IO 时间窗口、iowait/mmcqd、业务调用栈 |
| `kswapd`/PSI/LMK | 内存压力/泄漏 | meminfo、进程 RSS/heap、GC、被杀链路 |
| `mmcqd`/`exe_cq`/iowait 高 | IO 瓶颈 | ANR 前窗口、目标线程是否等待 IO |
| `dequeueBuffer` / `nSyncAndDrawFrame` | Surface/Buffer/GPU/SF 阻塞 | SurfaceFlinger、buffer queue、渲染日志 |
| `waitNextVsync` / `Sync group timeout` | Vsync/渲染同步超时 | Choreographer/SF/Input 证据 |
| main `nativePollOnce` + no focus | 焦点/启动链问题 | wm/input_focus/onResume/relayout/draw/start timeout |
| `am_process_start_timeout` / `am_kill` | 启动超时/应用被杀 | 目标应用启动日志、kill 原因、焦点转移 |

## 2. 锁与死锁

识别步骤：

1. 搜索主线程 `waiting to lock`、`waiting on`、`Blocked`。
2. 根据 `held by tid=N` 找持锁线程。
3. 看持锁线程是否在执行耗时业务、sleep、IO、Binder，或等待另一个锁。
4. 若形成环路，输出完整锁环和每个锁地址。

常用命令：

```bash
grep -n "waiting to lock" trace.txt
grep -n "locked <0x...>" trace.txt
grep -n "tid=<N>" trace.txt
```

典型模式：SharedPreferences/QueuedWork 持锁、WebView 初始化等待主线程、onServiceConnected AB-BA 死锁、system_server AMS/WMS/PMS 互锁。

## 3. Binder 阻塞

定位步骤：

1. 从 main 或关键线程找到 `BinderProxy.transactNative` / `IPCThreadState::waitForResponse`。
2. 用 `sysTid` 在 binder info/kernel log/SWT traces 中找 outgoing transaction。
3. 解析目标 pid/tid/service，回到对端 trace 看 server 在哪里卡住。
4. 若所有 binder 线程都忙，检查线程池耗尽和 pending transaction。
5. 只有找到对端慢点，才能把“Binder 阻塞”升级为“某服务/某进程根因”。

特殊模式：大数据 Binder transaction、通信失败、system_server 服务端锁竞争、provider query 慢。

## 4. CPU、内存、IO

- CPU 过载：目标线程 Runnable 但等待调度、CPU usage 高、Top 进程异常；若 top1 是业务进程且堆栈为业务计算，偏应用侧。
- AnrManager 负载归因顺序：先看 `TOTAL`/`iowait` 判断整体 CPU/IO；再看 Top 进程是否为目标包；目标包高负载需联动 meminfo/heap/GC/LMK/OOM 判断泄漏、内存抖动或 OOM；其它进程高负载则检查该进程内存/IO 并作为外部压力候选。缺少内存证据时不要直接下泄漏/OOM 结论。
- 内存压力：`kswapd0` Top、PSI memory 高、LMK 频繁、启动/fork/GC 变慢；需区分应用泄漏导致和全局压力导致。
- IO 瓶颈：`iowait`、`mmcqd/exe_cq`、major fault、主线程阻塞 IO；要证明压力发生在 ANR 前。

## 5. Surface / Buffer / Vsync

| 模式 | 关键栈/log | 判断要点 |
|---|---|---|
| SurfaceSyncer | `SurfaceSyncer`、sync group | 看 sync group 是否等待某 Surface 完成 |
| waitNextVsync | `failed to waitNextVsync`、`Sync group timeout` | 看 Choreographer/SF/Vsync 链路，不要只归咎业务 |
| Waiting for available buffer | `dequeueBuffer`、buffer queue 满 | 找谁占用 buffer、SF/GPU 是否卡住 |
| `nSyncAndDrawFrame` | RenderThread/HardwareRenderer | 结合 GPU/SF/渲染日志和主线程等待关系 |

## 6. Input / No Focus / 启动链

常见链路：

- 启动目标应用慢或被禁用/force-stop → 焦点离开 Launcher → 目标窗口未建立 → Launcher 报 No Focus ANR。
- App main 线程处理点击/绘制/生命周期 >5s → Input dispatch timeout。
- 进程 freeze 或低内存/CPU/IO 导致目标应用不能及时响应 → Input/No Focus 被放大。

No Focus 结论必须写清“报 ANR 的进程”和“导致焦点无法建立的目标进程”是否一致。

## 7. Broadcast / Service / Provider

- Broadcast：确认前台/后台 flag、是否 `goAsync()`、`finish()` 是否调用、工作线程池是否阻塞。
- Service：看冷启动和 lifecycle；主线程可能被前一组件或 Binder/锁拖住。
- Provider：区分 publish timeout 与客户端自定义 detect-not-responding；query 慢看 Binder 线程，publish 慢看启动主线程。

## 8. SWT / System ANR

MTK SWT 先确认 trace 有效性，再看最后卡住的 system_server 线程。常见分类：Deadlock、Binder Stuck、CPU、Low Memory、IO、Dex2oat、Native 方法耗时、SurfaceFlinger 卡住、dump 时间过长、StorageManagerService、AMS/WMS block。

## 9. 根因报告模板

```text
Direct blocker: <thread state + stack + resource>
Upstream cause: <lock holder|binder peer|CPU hog|memory/IO pressure|window chain>
Why it exceeds timeout: <time window and timeout>
Boundary: <app|remote app|system|mixed>
Confidence: <high|medium|low>
Missing evidence: <binder peer/trace/window logs/etc.>
```

## 回源案例

- 锁/死锁：[../实例/ANR-Locked.md](../实例/ANR-Locked.md)、[../实例/ANR-死锁.md](../实例/ANR-死锁.md)、[../MTK/swt/17.Deadlock.md](../MTK/swt/17.Deadlock.md)
- Binder：[../实例/ANR-Binder.md](../实例/ANR-Binder.md)、[../MTK/swt/18.Binder Stuck.md](../MTK/swt/18.Binder%20Stuck.md)
- CPU/IO/内存：[../实例/ANR-CPU.md](../实例/ANR-CPU.md)、[../实例/ANR-负载过高.md](../实例/ANR-负载过高.md)、[../实例/ANR-内存.md](../实例/ANR-内存.md)
- Input/No Focus：[../实例/ANR-Input.md](../实例/ANR-Input.md)、[../实例/ANR-Input dispatching.md](../实例/ANR-Input%20dispatching.md)、[../实例/ANR-应用被杀.md](../实例/ANR-应用被杀.md)
- Surface/Vsync/Buffer：[../实例/ANR-SurfaceSyncer.md](../实例/ANR-SurfaceSyncer.md)、[../实例/ANR-Sync group timeout，failed to waitNextVsync.md](../实例/ANR-Sync%20group%20timeout，failed%20to%20waitNextVsync.md)、[../实例/ANR-Waiting for Available buffer.md](../实例/ANR-Waiting%20for%20Available%20buffer.md)
- 主线程超时：[../实例/ANR-主线程超时.md](../实例/ANR-主线程超时.md)
