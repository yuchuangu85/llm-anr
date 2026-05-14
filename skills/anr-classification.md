---
name: anr-classification
description: ANR 类型与责任线程分类。Use when identifying ANR category, timeout period, trigger log pattern, unresponsive thread, app-vs-system boundary, or quick remediation class for Service, Broadcast, ContentProvider, Input dispatch, No Focus Window, Silent ANR, and Watchdog/SWT.
---

# ANR 分类体系

先分“触发场景”，再分“直接阻塞线程”，最后分“根因责任边界”。分类必须来自 reason/log/trace，不要只凭进程名或最终 `ANR in` 行判断。

## 1. 场景分类

| ANR 类型 | 默认超时/条件 | 关键日志 | 首要线程 |
|---|---|---|---|
| Input dispatch timeout | 约 5s 未处理输入 | `Input dispatching timed out`、`am_anr` | 主线程 |
| No Focus Window | 约 5s 无焦点窗口 | `no window has focus`、`Application does not have a focused window` | 通常看启动/窗口链；主线程可能空闲 |
| Broadcast timeout | 前台 10s，后台 60s；Android 14+ CPU 饥饿可扩展 | `Broadcast of Intent`、`BroadcastQueue: Timeout` | `onReceive()` 所在线程；`goAsync()` 看工作线程 |
| Service timeout | 前台 20s，后台 200s，含冷启动 | `Timeout executing service` | 主线程 |
| ContentProvider publish timeout | publish 约 10s 未完成，常表现为启动失败/进程被杀 | `timeout publishing content providers` | 主线程/启动链 |
| ContentProvider query timeout | 由 `ContentProviderClient#setDetectNotResponding(timeoutMillis)` 指定 | provider not responding、`ContentProvider$Transport.*` | 远程 provider Binder 线程或其启动主线程 |
| Silent ANR | `isSilentANR=true` 不弹框 | AnrManager/ActivityManager 相关字段 | 按具体类型判断 |
| Watchdog/SWT/System ANR | 系统服务 watchdog/SWT 超时 | Watchdog、SWT、system_server trace | system_server 关键线程 |

ContentProvider 有两个容易混淆的机制：启动 publish timeout 通常不弹 ANR 对话框、不 dump 常规 ANR，而是清理/杀掉进程；远程 CRUD/query 慢只有在客户端显式设置 detect-not-responding timeout 时才按 provider not responding 分析。

## 2. 成因分类

| 类别 | 典型证据 | 责任边界 |
|---|---|---|
| 主线程耗时 | main `Runnable` 或业务栈 > 超时，`Slow dispatch` | 多为应用侧 |
| 锁竞争/死锁 | `waiting to lock ... held by tid=N`、环形锁链 | 多为应用侧或 system_server 内部 |
| Binder 阻塞 | `BinderProxy.transactNative`、`IPCThreadState::waitForResponse` | 看对端；可能应用、系统或远端进程 |
| Binder 线程池耗尽 | 多个 Binder 线程忙、pending transaction | 通常对端/系统资源问题 |
| 阻塞 I/O | 主线程文件/DB/SP/网络/`read/write/fsync` | 应用侧或存储压力共同导致 |
| Native/渲染阻塞 | `futex`、`dequeueBuffer`、`nSyncAndDrawFrame`、Vsync 等 | 可能应用、Framework、GPU/SF |
| CPU 过载 | CPU usage/Load 高、目标线程 runnable 但未运行 | 可能系统侧诱因或某进程 CPU hog |
| 内存压力 | `kswapd`、PSI memory、LMK、低可用内存 | 多为系统侧/全局诱因，也可能应用泄漏触发 |
| IO 瓶颈 | `iowait`、`mmcqd`/`exe_cq`、major fault 高 | 多为系统/存储侧诱因 |
| 启动/焦点异常 | `wm_*`、`input_focus`、`am_process_start_timeout`、应用被 kill/disabled | 看目标应用和窗口链，不一定是报 ANR 的进程 |

## 3. 无响应线程速查

| 场景 | 应检查线程 |
|---|---|
| Input dispatch | ANR 进程 main thread |
| No focused window | 启动目标 Activity main thread、窗口焦点链、WMS/Input/SF；main 可能是 `nativePollOnce` |
| 同步 BroadcastReceiver | `onReceive()` 执行线程，默认 main，除非注册时指定 Handler |
| 异步 BroadcastReceiver (`goAsync`) | 处理 `PendingResult` 的工作线程/线程池，确认 `finish()` |
| Service timeout / Foreground service start | main thread，含冷启动和生命周期方法 |
| ContentProvider query | provider 的 Binder 线程；若需冷启动，再看 provider 进程 main |
| JobScheduler `onStartJob/onStopJob` | main thread |
| System Watchdog/SWT | system_server 中被 watchdog 监控的 Handler/lock/Binder 线程 |

## 4. 关键日志识别

| 关键字 | 用途 |
|---|---|
| `am_anr` | 定位 ANR 基准时间、PID、process、reason |
| `AnrManager` | dump 流程、CPU/Load/PSI/内存、trace dump 开始/结束 |
| `WindowManager: ANR in` | Input/No Focus 真实触发附近信号 |
| `Slow dispatch` / `Slow delivery` / `Slow Looper` | 主线程消息处理/投递延迟 |
| `dvm_lock_sample` / `Long monitor contention` | Java 锁竞争 |
| `binder_sample` / binder transaction | 慢 Binder 调用或对端阻塞 |
| `lowmemorykiller` / `kswapd` / `pressure/memory` | 内存压力 |
| `iowait` / `mmcqd` / `exe_cq` | IO 压力 |
| `input_focus` / `wm_on_resume_called` / `wm_relayout_window` | No Focus Window 分析 |

## 5. 分类输出格式

```text
ANR type: <Input|No Focus|Broadcast|Service|ContentProvider|SWT>
Trigger evidence: <am_anr/log line/time>
Unresponsive thread: <main|binder|worker|system_server thread>
Direct blocker: <stack/state/resource>
Root-cause class: <lock|binder|io|cpu|memory|surface|startup|unknown>
Boundary: <app|remote app|system|mixed|unknown>
Confidence: <high|medium|low> + missing evidence
```

## 回源阅读

- [../ANR-分类.md](../ANR-分类.md)
- [../Find the unresponsive thread    App quality.md](../Find%20the%20unresponsive%20thread%20%20%20%20App%20quality.md)
- [../Diagnose and fix ANRs    App quality.md](../Diagnose%20and%20fix%20ANRs%20%20%20%20App%20quality.md)
- [../机制/ANR-ContentProvider.md](../机制/ANR-ContentProvider.md)
- [../MTK/swt/6.确认trace有效性.md](../MTK/swt/6.确认trace有效性.md)
