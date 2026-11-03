---
name: anr-principle
description: ANR 触发机制、源码路径与版本差异。用于解释 ANR 为何触发、超时消息如何设置/取消/触发、trace/日志如何生成，或服务、广播、ContentProvider、输入分发、无焦点窗口、Android 10/13/14/15 和 MTK SWT 的机制差异。
---

# ANR 原理与触发机制

用本文件解释“系统为什么判定 ANR”。如果任务是分析具体 bugreport，先用 [anr-analysis.md](anr-analysis.md)，再按需回到这里校验机制。

## 1. 通用模型：埋雷、拆雷、爆雷

多数组件 ANR 都符合三段模型：

1. **埋雷**：系统发送延迟超时消息或在 InputDispatcher 中记录等待截止时间。
2. **拆雷**：组件/输入事件在超时前完成，移除超时消息或更新状态。
3. **爆雷**：超时仍未完成，进入 `appNotResponding`、杀进程、SWT/Watchdog 或静默 ANR 流程。

分析时要定位：哪个入口埋雷、哪个完成信号本应拆雷、为什么没有拆雷。

## 2. 服务 ANR

- 前台服务默认 20s，后台默认 200s，包含必要冷启动时间。
- 常见调用栈：`ActivityThread.handleCreateService`、`handleBindService`、`handleServiceArgs`。
- 机制：`ActiveServices` 在调度服务生命周期前发送 `SERVICE_TIMEOUT_MSG`，生命周期执行完成后移除。
- 根因常见于冷启动慢、生命周期方法主线程耗时、被前序广播/锁/Binder 阻塞。

回源：[../机制/ANR-Service.md](../机制/ANR-Service.md)、[../ANR原理代码分析.md](../ANR原理代码分析.md)

## 3. 广播 ANR

- 前台广播 10s，后台广播 60s；顺序广播队列整体超时可按 `2 × receiver 数 × timeout` 理解。
- `FLAG_RECEIVER_FOREGROUND=0x10000000` 表示前台广播短超时。
- 同步广播看 `onReceive()` 所在线程；`goAsync()` 看处理 `PendingResult` 的工作线程，并确认 `finish()`。
- Android 14+ 对 CPU 饥饿场景可能延长超时窗口，结论中需说明版本影响。

回源：[../机制/ANR-Broadcast.md](../机制/ANR-Broadcast.md)、[../机制/ANR-Broadcast2.md](../机制/ANR-Broadcast2.md)

## 4. 内容提供程序（ContentProvider）ANR

区分两个机制：

1. **提供程序发布超时**：应用启动并发布 ContentProvider 时埋 `CONTENT_PROVIDER_PUBLISH_TIMEOUT`，约 10s 未发布会清理/杀掉启动进程，常不弹常规 ANR 对话框、不产生普通 ANR 转储。
2. **提供程序无响应**：客户端通过 `ContentProviderClient#setDetectNotResponding(timeoutMillis)` 设置远程 ContentProvider 调用超时，增删改查/query/call/getType 等操作慢时触发；需看 ContentProvider 的 Binder 线程与是否冷启动。

因此，看到 ContentProvider 相关 ANR 时，不要笼统写“CP 默认 10s 查询超时”；必须说明是发布超时还是客户端自定义查询超时。

回源：[../机制/ANR-ContentProvider.md](../机制/ANR-ContentProvider.md)、[../ANR原理代码分析.md](../ANR原理代码分析.md)

## 5. 输入分发超时

- InputDispatcher 分发输入事件后等待目标窗口/应用处理，典型超时约 5s。
- 触发原因常见：无焦点窗口、窗口暂停、输入通道未注册/死亡/满、按键或触摸事件未处理完成。
- Android 10 与 Android 13 的差异重点在 InputDispatcher ANR 检测位置和 `AnrTracker`/dispatchOnce 流程变化；分析跨版本日志时要回看版本差异。
- Android 15 进一步改进 ANR 诊断字段时，应优先使用更明确的原因，而不是旧版经验兜底。

回源：[../ANR详细对比13&10.md](../ANR详细对比13&10.md)、[../机制/ANR-HasNoFocusWindow.md](../机制/ANR-HasNoFocusWindow.md)

## 6. 无焦点窗口

无焦点窗口是输入 ANR 的子类，但主线程不一定阻塞。核心是焦点窗口没有及时建立或切换：

1. Activity 是否恢复完成。
2. 是否调用 relayout 并向 WMS 提交窗口。
3. 是否完成绘制并由 WMS/InputFlinger/SF 更新焦点。
4. 目标应用是否启动超时、被杀/禁用/冻结，导致 Launcher 或前台应用背锅。

## 7. Trace 与日志生成

- `dumpStackTraces begin/end` 和 `Completed ANR` 反映 dump 成本，不等于 ANR 触发时间。
- `Signal Catcher`、trace 快照、DropBox、AnrManager CPU/负载是证据来源；转储长耗时可能污染后续日志。
- `utm/stm`、`schedstat`、`core`、`nice`、`state` 等字段用于解释线程是否真正运行、等待 CPU、等待锁或阻塞在原生代码/系统调用。

回源：[../机制/Trace产生过程.md](../机制/Trace产生过程.md)、[../ANR-trace文件分析.md](../ANR-trace文件分析.md)

## 8. SWT / 看门狗（Watchdog）

MTK SWT 是系统级看门狗类问题：重点检查 system_server、AMS/WMS、Binder、死锁、CPU/IO/低内存、转储流程。先确认 trace 有效性，再按 SWT 分析流程找最后卡住的线程/锁/对端。

回源：[../MTK/swt/](../MTK/swt/)、[../MTK/swt/7.SWT Analysis Flow.md](../MTK/swt/7.SWT%20Analysis%20Flow.md)

## 9. 机制解释输出要求

```text
触发路径：<类/方法/消息>
超时计时起点：<证据/来源>
超时取消条件：<预期完成路径>
超时触发原因：<未完成环节/阻塞点>
版本/平台说明：<Android/MTK 差异>
证据缺口：<缺失来源/日志/堆栈>
```
