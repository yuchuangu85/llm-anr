---
name: anr-classification
description: ANR 类型与责任线程分类。用于识别服务、广播、ContentProvider、输入分发、无焦点窗口、静默 ANR 和 Watchdog/SWT 的 ANR 类别、超时时限、触发日志模式、无响应线程、应用与系统的责任边界或快速修复类别。
---

# ANR 分类体系

先分“触发场景”，再分“直接阻塞线程”，最后分“根因责任边界”。分类必须来自 reason/log/trace，不要只凭进程名或最终 `ANR in` 行判断。

## 1. 场景分类

| ANR 类型 | 默认超时/条件 | 关键日志 | 首要线程 |
|---|---|---|---|
| 输入分发超时 | 约 5s 未处理输入 | `Input dispatching timed out`、`am_anr` | 主线程 |
| 无焦点窗口 | 约 5s 无焦点窗口 | `no window has focus`、`Application does not have a focused window` | 通常看启动/窗口链；主线程可能空闲 |
| 广播超时 | 前台 10s，后台 60s；Android 14+ CPU 饥饿可扩展 | `Broadcast of Intent`、`BroadcastQueue: Timeout` | `onReceive()` 所在线程；`goAsync()` 看工作线程 |
| 服务超时 | 前台 20s，后台 200s，含冷启动 | `Timeout executing service` | 主线程 |
| ContentProvider 发布超时 | 约 10s 未完成发布，常表现为启动失败/进程被杀 | `timeout publishing content providers` | 主线程/启动链 |
| ContentProvider 查询超时 | 由 `ContentProviderClient#setDetectNotResponding(timeoutMillis)` 指定 | provider not responding、`ContentProvider$Transport.*` | 远程 ContentProvider 的 Binder 线程或其启动主线程 |
| 静默 ANR | `isSilentANR=true` 不弹框 | AnrManager/ActivityManager 相关字段 | 按具体类型判断 |
| Watchdog/SWT/系统 ANR | 系统服务看门狗/SWT 超时 | Watchdog、SWT、system_server trace | system_server 关键线程 |

ContentProvider 有两个容易混淆的机制：启动发布超时通常不弹 ANR 对话框、不生成常规 ANR 转储，而是清理/杀掉进程；远程增删改查/查询慢只有在客户端显式设置无响应检测超时时限时才按提供程序无响应分析。

## 2. 成因分类

| 类别 | 典型证据 | 责任边界 |
|---|---|---|
| 主线程耗时 | 主线程 `Runnable` 或业务栈 > 超时，`Slow dispatch` | 多为应用侧 |
| 锁竞争/死锁 | `waiting to lock ... held by tid=N`、环形锁链 | 多为应用侧或 system_server 内部 |
| Binder 阻塞 | `BinderProxy.transactNative`、`IPCThreadState::waitForResponse` | 看对端；可能应用、系统或远端进程 |
| Binder 线程池耗尽 | 多个 Binder 线程忙、待处理事务 | 通常对端/系统资源问题 |
| 阻塞 I/O | 主线程文件/DB/SP/网络/`read/write/fsync` | 应用侧或存储压力共同导致 |
| Native/渲染阻塞 | `futex`、`dequeueBuffer`、`nSyncAndDrawFrame`、Vsync 等 | 可能应用、Framework、GPU/SF |
| CPU 过载 | CPU 使用率/负载高、目标线程可运行但未运行 | 可能是系统侧诱因或某进程大量占用 CPU |
| 内存压力 | `kswapd`、PSI 内存压力、LMK、低可用内存 | 多为系统侧/全局诱因，也可能应用泄漏触发 |
| IO 瓶颈 | `iowait`、`mmcqd`/`exe_cq`、主缺页次数高 | 多为系统/存储侧诱因 |
| 启动/焦点异常 | `wm_*`、`input_focus`、`am_process_start_timeout`、应用被杀/禁用 | 看目标应用和窗口链，不一定是报 ANR 的进程 |

## 3. 无响应线程速查

| 场景 | 应检查线程 |
|---|---|
| 输入分发 | ANR 进程主线程 |
| 无焦点窗口 | 启动目标 Activity 的主线程、窗口焦点链、WMS/输入系统/SF；主线程可能是 `nativePollOnce` |
| 同步 BroadcastReceiver | `onReceive()` 执行线程，默认主线程，除非注册时指定 Handler |
| 异步 BroadcastReceiver (`goAsync`) | 处理 `PendingResult` 的工作线程/线程池，确认 `finish()` |
| 服务超时 / 前台服务启动 | 主线程，含冷启动和生命周期方法 |
| ContentProvider 查询 | ContentProvider 的 Binder 线程；若需冷启动，再看其进程主线程 |
| JobScheduler `onStartJob/onStopJob` | 主线程 |
| 系统 Watchdog/SWT | system_server 中被看门狗监控的 Handler/锁/Binder 线程 |

## 4. 关键日志识别

| 关键字 | 用途 |
|---|---|
| `am_anr` | 定位 ANR 基准时间、PID、进程、原因 |
| `AnrManager` | 转储流程、CPU/负载/PSI/内存、trace 转储开始/结束 |
| `WindowManager: ANR in` | 输入分发/无焦点窗口真实触发附近信号 |
| `Slow dispatch` / `Slow delivery` / `Slow Looper` | 主线程消息处理/投递延迟 |
| `dvm_lock_sample` / `Long monitor contention` | Java 锁竞争 |
| `binder_sample` / binder transaction | 慢 Binder 调用或对端阻塞 |
| `lowmemorykiller` / `kswapd` / `pressure/memory` | 内存压力 |
| `iowait` / `mmcqd` / `exe_cq` | IO 压力 |
| `input_focus` / `wm_on_resume_called` / `wm_relayout_window` | 无焦点窗口分析 |

## 5. 分类输出格式

```text
ANR 类型：<输入|无焦点|广播|服务|内容提供程序|SWT>
触发证据：<am_anr/日志行/时间>
无响应线程：<主线程|Binder 线程|工作线程|system_server 线程>
直接阻塞点：<堆栈/状态/资源>
根因类别：<锁|Binder|I/O|CPU|内存|图形表面|启动|未知>
责任边界：<应用|远端应用|系统|混合|未知>
置信度：<高|中|低> + 缺失证据
```

## 回源阅读

- [../ANR-分类.md](../ANR-分类.md)
- [../Find the unresponsive thread    App quality.md](../Find%20the%20unresponsive%20thread%20%20%20%20App%20quality.md)
- [../Diagnose and fix ANRs    App quality.md](../Diagnose%20and%20fix%20ANRs%20%20%20%20App%20quality.md)
- [../机制/ANR-ContentProvider.md](../机制/ANR-ContentProvider.md)
- [../MTK/swt/6.确认trace有效性.md](../MTK/swt/6.确认trace有效性.md)
