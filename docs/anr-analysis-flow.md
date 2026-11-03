# ANR 分析全流程

> 本文档梳理从接收 bugreport 到输出最终分析报告的完整流程，用于后续流程优化评估。
>
> 最后更新: 2026-05-13

---

## 流程总览

```
输入 (bugreport / ZIP / TAR / JSON 测试夹具)
    │
    ▼
┌──────────────────────────────────────┐
│ 阶段 0: 预处理                        │
│ - 解压/提取 bugreport                 │
│ - 确定 ANR 类型和包名                  │
│ - 生成 AI 上下文                      │
└──────────────────┬───────────────────┘
                   ▼
┌──────────────────────────────────────┐
│ 阶段 1: 证据提取                      │
│ - Trace 解析 (trace_preprocessor)     │
│ - EventLog 提取 (extractor)           │
│ - Logcat/AnrManager 提取              │
│ - 时间归一化 (normalizer)             │
│ - 内存/进程信息提取                    │
└──────────────────┬───────────────────┘
                   ▼
┌──────────────────────────────────────┐
│ 阶段 2: 证据分析                      │
│ - 锚定 ANR 类型和真实时间              │
│ - 按类型分流分析                       │
│ - 调用栈阻塞点分析                    │
│ - 系统负载交叉验证 (CPU/内存/IO)        │
│ - Binder 对端追溯                      │
└──────────────────┬───────────────────┘
                   ▼
┌──────────────────────────────────────┐
│ 阶段 3: 根因推导                      │
│ - 候选根因链生成                       │
│ - 置信度评估                           │
│ - 证据缺口识别                         │
│ - 修复建议草案                         │
└──────────────────┬───────────────────┘
                   ▼
┌──────────────────────────────────────┐
│ 阶段 4: 报告输出                      │
│ - 时间线                             │
│ - 直接阻塞点                         │
│ - 候选根因链                         │
│ - 证据质量评估                       │
│ - 修复建议                           │
└──────────────────────────────────────┘
```

---

<a id="phase-0-预处理"></a>
## 阶段 0: 预处理

### 0.1 输入识别

| 输入格式 | 处理方式 |
|---------|---------|
| 目录 (含 trace/logcat/event_log) | 直接读取 |
| ZIP/TAR 压缩包 | `scripts/extract_bugreport.py` 解压 |
| JSON 测试夹具 | `load_package_from_fixture()` 加载 |

### 0.2 包名和类型确定

1. 如果用户提供了 `--package`，使用指定的包名
2. 否则从 `AnrManager` 块或 `am_anr` 行提取包名
3. ANR 类型: 从 `am_anr` 的原因字段推断 → `input_dispatching_timeout` / `no_focus_window` / 其他

<a id="03-ai-context-生成"></a>
### 0.3 AI 上下文生成

```bash
python3 scripts/anr_to_ai.py <input> [--package <pkg>] [--anr-type <type>]
```

产出:
- `anr_ai_context/index.json` — 目录索引，链接到所有 ANR 分组
- `anr_ai_context/<group-id>/anr_analysis.md` — 分析指令 + 过滤后证据 + 内联分析槽位（每个 ANR 一份）
- `anr_ai_context/<group-id>/logcat.txt` — 过滤后的完整 logcat（由 anr_analysis.md 引用）

### 0.4 MTK SWT 入口 (平台特化)

如果是 MTK 平台 SWT 问题:
1. 获取 db 文件
2. 解析 db → 确认 trace/log 有效性
3. 若找不到有效 trace，检查转储流程卡点
4. 参考 SWT 分析流程进行初步分析
5. 若为 Monkey/Monkey 测试，先查已知问题清单

---

<a id="phase-1-证据提取"></a>
## 阶段 1: 证据提取

### 1.1 证据源清单

| 证据源 | 来源文件 | 核心信息 | 关键程度 |
|--------|---------|---------|:---:|
| Trace (traces.txt) | `/data/anr/anr_*.txt` 或 `SWT_JBT_TRACES` | 各线程堆栈、状态、schedstat | ⭐⭐⭐ |
| EventLog | `events_log` 或 `SYS_ANDROID_EVENT_LOG` | `am_anr`、`am_*`、`wm_*` 等带时间戳的系统事件 | ⭐⭐⭐ |
| Logcat (main_log) | `main_log` 或 `SYS_ANDROID_LOG` | InputDispatcher、WindowManager、ActivityManager 日志 | ⭐⭐⭐ |
| AnrManager | logcat 中 `AnrManager:` 行 | CPU 负载、PSI 内存压力、平均负载、trace 转储生命周期 | ⭐⭐⭐ |
| 系统日志 | `system_log` | 系统级日志 | ⭐⭐ |
| 内核日志 | `kernel_log` | 内核日志、LMK 事件 | ⭐⭐ |
| Meminfo | bugreport dumpsys 或 `am_meminfo` | 内存使用、PSS/RSS | ⭐⭐ |
| DropBox | `data_app_anr@*.txt.gz` | ANR 归档信息 | ⭐ |

### 1.2 Trace 预处理

核心代码: `anr_evidence/trace_preprocessor.py`

**步骤**:
1. Trace 分段切分与压缩
2. 主线程优先识别（线程名为 `"main"` 或 `tid == 1`）
3. 线程基础字段提取:
   - `threadName`, `tid`, `sysTid`, `prio`, `daemon`, `group`
   - `sCount/dsCount/ucsCount`, `flags/obj/self`
   - `nice/cgrp/sched/handle`
   - `linuxState`, `schedstat/utm/stm/core/hz`
   - `heldMutexes`, `waitObject`, `lockOwnerTid`
4. 阻塞提示提取:
   - Binder 阻塞: `IPCThreadState::waitForResponse`
   - 锁竞争: `waiting to lock`, `locked`, `held by`
   - 原生轮询: `nativePollOnce`
   - Futex: `futex_wait`
   - GPU 等待: `dequeueBuffer`, `waitNextVsync`
5. 主线程关键帧: `mainThreadNativeTopFrame`, `mainThreadJavaTopFrame`, `mainThreadLooperFrame`

**产出**: 结构化的 trace 证据对象

### 1.3 EventLog 提取

核心代码: `anr_evidence/extractor.py`

**步骤**:
1. 读取 event_log 全文
2. 定位 `am_anr` 行 → 获取 PID、进程名、原因
3. 以 `am_anr` 时间为中心，提取 ±N 秒窗口内的关键事件:
   - `am_*`: `am_proc_start`, `am_proc_died`, `am_on_resume_called`, `am_pss`, `am_meminfo`
   - `wm_*`: `wm_on_create_called`, `wm_on_resume_called`, `wm_on_stop_called`
   - `input_*`: `input_focus`
   - `power/*`, `battery/*`, `ssm/*`
4. 计算每条事件的 ΔT (相对于 `am_anr` 时间)
5. 按时间排序

### 1.4 Logcat / AnrManager 提取

**步骤**:
1. 搜索 ANR 类型特定日志:
   - 输入事件: `Input event dispatching timed out`, `WindowManager: ANR in`
   - 广播: `Timeout of broadcast BroadcastRecord`
   - 服务: `Timeout executing service:`
2. 提取 AnrManager 转储生命周期: `startAnrDump` → `dumpStackTraces begin/end` → `ANR in` → `Completed ANR`
3. 提取 CPU 负载行（`ago` 和 `later` 两段时间）
4. 提取 PSI 内存压力信息
5. 提取平均负载行

### 1.5 时间归一化

核心代码: `anr_evidence/normalizer.py`

**要点**:
- Trace 文件头的 pid 时间戳: `----- pid XXXX at YYYY-MM-DD HH:MM:SS.µs+0800 -----`
- EventLog `am_anr` 时间: `MM-DD HH:MM:SS.ms`
- Logcat 时间: `MM-DD HH:MM:SS.ms PID TID`
- AnrManager 转储时间滞后于真实 ANR 时间
- **不以 `AnrManager: ANR in` 时间作为真实 ANR 发生时间**

---

<a id="phase-2-证据分析"></a>
## 阶段 2: 证据分析

### 2.1 锚定 ANR 类型和真实时间

**输入**: EventLog `am_anr` + 类型特定 logcat 行

| ANR 类型 | 真实时间日志 | 原因识别 |
|---------|------------|------------|
| 输入分发超时 | `WindowManager: Input event dispatching timed out` | `Input dispatching timed out` |
| 无焦点窗口 | `WindowManager: Input event dispatching timed out` + `no window has focus` | 同上但有 `no window has focus` |
| 广播超时 | `BroadcastQueue: Timeout of broadcast` | `broadcast timeout` |
| 服务超时 | `ActivityManager: Timeout executing service:` | `executing service` |
| ContentProvider 超时 | Provider 发布或远程调用超时 | `provider not responding` |

**注意**: `am_anr` 时间可能比真实 ANR 触发时间晚（受转储影响），需交叉比对类型特定日志。

### 2.2 按类型分流分析

```
                  ┌──────────────────────┐
                  │    ANR 类型判定        │
                  └──────────┬───────────┘
                             │
         ┌───────────────────┼───────────────────────┐
         ▼                   ▼                       ▼
  ┌──────────────┐   ┌──────────────┐   ┌──────────────────────┐
  │ 输入类 ANR   │   │ 组件类 ANR   │   │ 系统/SWT 类          │
  ├──────────────┤   ├──────────────┤   ├──────────────────────┤
  │ 分发         │   │ 广播         │   │ system_server Watchdog│
  │ 超时         │   │ 服务         │   │ Binder/AMS/WMS 锁链   │
  │ 无焦点窗口   │   │ CP           │   │ SWT DB 分析           │
  └──────┬───────┘   └──────┬───────┘   └──────────┬───────────┘
         │                  │                       │
         ▼                  ▼                       ▼
    看主线程 trace     看 onReceive/        看 system_server
    看输入状态         生命周期回调          关键线程 trace
    看焦点窗口         看 Binder 状态        看 Watchdog
    看 Binder/锁/IO    看 CPU/负载           看 Binder/AMS/WMS
```

<a id="221-input-dispatch-timeout"></a>
#### 2.2.1 输入分发超时

1. **主线程状态判定**: 从 trace 获取主线程状态
   - `Native` + `nativePollOnce`: 当前快照空闲/等待中，需进一步查看是真正空闲还是间歇性阻塞
   - `Blocked`: 锁等待 → 沿着 `waiting to lock` / `held by` 找持锁线程
   - `Runnable`: 业务代码耗时或 CPU 被抢占 → 查 schedstat、CPU 负载
   - `Native` + Binder: `IPCThreadState::waitForResponse` → 找 Binder 对端
   - `Native` + IO: `write`/`read`/`syscall` → IO 瓶颈
   - `Native` + Surface: `dequeueBuffer`/`waitNextVsync` → Surface/Buffer 问题

2. **系统负载检查**: 见 2.5 节

<a id="222-no-focus-window-三步检查"></a>
#### 2.2.2 无焦点窗口（三步检查）

```
步骤 1: wm_on_resume_called 是否早于 ANR 时间?
   │
   ├─ 否 → Activity 未完成恢复 → 应用相关 (启动/生命周期问题)
   │
   └─ 是 → 步骤 2: relayout window 是否在 ANR 前调用?
             │
             ├─ 否 → 应用相关 (未 relayout)
             │
             └─ 是 → 步骤 3: reportDrawFinished / finishDrawingWindow
                       是否在 ANR 前完成?
                         │
                         ├─ 是 → 应用已完成绘制
                         │       焦点丢失可能是 WMS/系统/其他窗口问题
                         │
                         └─ 否 → 绘制未完成
                                 偏应用侧渲染问题
```

<a id="223-broadcast-timeout"></a>
#### 2.2.3 广播超时

1. 从 trace 找 `onReceive()` 所在线程
2. 检查 `goAsync()` → `finish()` 调用链是否完整
3. 检查广播队列是否有堆积: 前一个接收者超时会阻塞后续
4. 前台广播 10s / 后台广播 60s 超时

<a id="224-service-timeout"></a>
#### 2.2.4 服务超时

1. 前台服务 20s / 后台服务 200s / FGS 5-10s
2. 检查生命周期回调: `onCreate` / `onStartCommand` / `onBind`
3. 检查冷启动耗时: ProcessRecord + Process.start 时间
4. `scheduleServiceTimeoutLocked` → `SERVICE_TIMEOUT_MSG` 机制

<a id="23-callstack-阻塞点分析"></a>
### 2.3 调用栈阻塞点分析

**线程状态与含义**:

| 状态 | Java 含义 | ANR 诊断含义 |
|-------|----------|-------------|
| `Runnable` | 正在运行/可运行 | 业务代码耗时或等待 CPU 调度 |
| `Blocked` | 等待对象锁 | 锁竞争/死锁 |
| `Waiting` | `Object.wait()` | 等待 `notify`，或 `LockSupport.park` |
| `TimedWaiting` | `sleep/wait(timeout)` | 超时等待 |
| `Native` | 执行 JNI/进入原生层 | 需区分: Binder/IO/futex/Surface/空闲 |
| `Sleeping` | `Thread.sleep()` | 主动睡眠 |
| `Suspended` | GC 或调试 | GC 暂停 |

**常见调用栈模式**:

| 模式 | 特征帧 | 诊断 |
|------|-------|------|
| 正常空闲 | `nativePollOnce` → `Looper.loop` | 当前快照空闲，需看时间窗口 |
| 锁阻塞 | `waiting to lock <0x...> held by tid=N` | 找 tid=N 的持锁线程 |
| Binder 客户端 | `IPCThreadState::waitForResponse` → `BinderProxy.transact` | 找 Binder 对端进程/线程 |
| 死锁 | 两个线程各自 `waiting to lock` 对方持有的锁 | 循环依赖 |
| IO 阻塞 | `write`/`read`/`fsync`/`msync` | IO 瓶颈 |
| Surface/Buffer | `dequeueBuffer`/`waitNextVsync`/`nSyncAndDrawFrame` | Vsync/Surface 问题 |
| Futex 等待 | `futex_wait` → `ConditionVariable::wait` | 底层同步等待 |

### 2.4 Binder 对端追溯

当主线程为 `Native` + `IPCThreadState::waitForResponse` 时，需要找到 Binder 服务端:

**方法一**: 从 `SYS_BINDER_INFO` 中搜索 `outgoing transaction`
**方法二**: 从进程表 `SYS_PROCESSES_AND_THREADS` 通过对端 `sysTid` 查找进程名
**方法三**: 搜索 Binder 线程池所有线程，看哪个在服务端执行对应方法

**找不到对端时**: 标记为证据缺口，不推测

### 2.5 系统负载交叉验证

```
CPU 负载 >= 95%?
   │
   ├─ 是 → iowait 最高?
   │        ├─ 是 → IO 负载 >= 70%?
   │        │        ├─ 是 → IO 瓶颈
   │        │        └─ 否 → 继续判断
   │        │
   │        └─ 否 → kswapd0 负载位于前三?
   │                 ├─ 是 → 内存压力
   │                 └─ 否 → mmcqd/exe_cq 负载位于前三?
   │                          ├─ 是 → IO 瓶颈
   │                          └─ 否 → CPU 第一名进程是否正常?
   │                                   ├─ 否 → CPU 抢占 (第一名进程)
   │                                   └─ 是 → 正常高负载
   │
   └─ 否 → 从调用栈分析
```

**归因路径**:

1. 先看 `TOTAL` 整体 CPU/IO 是否高
2. 再看负载最高的进程是否为目标包
3. **若目标包高负载**: 继续查该包 meminfo/`am_pss`、Java 堆/原生堆、GC、LMK/OOM、PSI 内存压力 → 判断是否内存泄漏/GC 抖动/OOM 放大 → 证据不足写缺口
4. **若其它进程高负载**: 查该进程的内存/IO/GC/LMK 证据 → 标记为外部系统压力 → 不能直接归因到目标应用

### 2.6 内存压力评估

| 信号 | 阈值 | 含义 |
|------|------|------|
| `kswapd0` CPU 占用位于前三 | - | 系统进行内存回收 |
| PSI 内存压力 `some/full` | `avg10 > 1.0` | 内存压力显著 |
| `Free memory until OOME` | < 50MB | 接近 OOM |
| `Cached + Free` RAM | < 350MB (4G) / 450MB (>4G) | 低内存 |
| LMK 杀进程 | 命中 `adj < 100` | 内存极其紧张 |

---

<a id="phase-3-根因推导"></a>
## 阶段 3: 根因推导

### 3.1 候选根因链构建

每条链结构:
```
触发类型 → 直接阻塞点 → 上游诱因 → 责任边界 → 证据强度

示例:
链 A (高置信): 输入分发超时 → main BLOCKED 等待 <lock>
→ 工作线程持锁执行 SharedPreferences IO >5s
→ 应用侧主线程锁竞争。

证据: am_anr 时间、main trace、held by tid、worker trace、Slow dispatch
缺口: 无业务代码上下文、无复现
```

### 3.2 置信度评估

| 级别 | 条件 |
|------|------|
| **高** | 所有环节均有直接证据，trace/日志/CPU 三方一致，Binder 对端已确认 |
| **中** | 主要环节有证据支撑，个别环节依赖推理或有证据缺口 |
| **低** | 证据不足或矛盾，仅基于经验推测 |

### 3.3 证据缺口识别

常见缺口:
- Binder 对端不可见（只有客户端 trace）
- trace 文件不包含目标进程
- 转储滞后导致 CPU 负载区间不匹配 ANR 时间
- 主线程快照为 `nativePollOnce` 但无法证明间歇性阻塞
- 无业务代码上下文

### 3.4 修复建议生成

按三个方向生成:
1. **应用侧**: 代码修改建议 (移出主线程、减少锁持有时间、优化 IO)
2. **系统侧**: 资源调整建议 (内存、CPU 调度)
3. **监控/复现**: 建议开启的监控、复现条件

---

<a id="phase-4-报告输出"></a>
## 阶段 4: 报告输出

### 4.1 报告结构

```markdown
# ANR 分析报告

## 1. 时间线
(按时间顺序列出关键事件，标注来源)

## 2. Trace 证据分析
- 堆栈文件信息: pid/process/时间/转储耗时
- 主线程详情: name/tid/sysTid/state/schedstat/utm/stm
- 阻塞点: 线程堆栈/锁对象/对端
- 死锁检测/堆栈提示命中情况
- 持锁者/对端线程证据

## 3. EventLog 证据分析
- am_anr 基准
- 关键事件 (am_*/wm_*/input_*) 带 ΔT
- 内存/进程事件
- 事件间因果关系

## 4. Logcat 与 AnrManager 证据分析
- ANR 触发日志
- 焦点/窗口/Surface 转移链
- AnrManager CPU/PSI/Load 数据
- 进程负载归因分析

## 5. 直接阻塞点
- 线程/堆栈/等待对象/对端/负载证据

## 6. 候选根因链
1. [高|中|低] ...

## 7. 证据质量评估
- 覆盖情况
- 缺口和矛盾
- 交叉验证结果
- 主要证据标识

## 8. 修复建议
- 应用侧
- 系统侧
- 监控/复现
```

### 4.2 输出原则

- `finalJudgment = false` — 仅提供分析结论，不做最终裁决
- `notRootCauseYet = true` — 可能只是近因而非根因
- `requiresHumanConfirmation = true` — 需人工确认
- 所有判断必须有证据来源和时间点
- 证据不足时输出候选链而非武断结论

---

## MTK SWT 分析流程 (平台扩展)

<a id="swt-vs-anr"></a>
### SWT 与 ANR 对比

| 项目 | ANR | SWT |
|------|-----|-----|
| 检测机制 | AMS 超时检测 | system_server `Watchdog`（看门狗） |
| 监控范围 | 应用进程主线程 | 系统核心线程 (android.fg/android.bg/android.io/ActivityManager 等) |
| 触发条件 | 组件超时 / 输入无响应 | 关键系统线程超过 60s 未响应 |
| 典型影响 | 单个应用 ANR 弹窗 | 整个系统卡死/重启 |
| 证据来源 | trace/logcat/event_log/AnrManager | SWT DB + JBT_TRACES + SYS_ANDROID_LOG |

### SWT 分析 6 步流程

```
步骤 1: 获取 DB → 步骤 2: 解析 DB (确认 trace 有效性)
    → 步骤 3: 参考 SWT/ANR 分析流程 + 调用栈分析进行初步分析
    → 步骤 4: 查阅常见 SWT/ANR 原因 确定方向
    → 步骤 5: 参考案例分享
    → 步骤 6: 仍无法解决 → 提交 eService
```

### 常见 SWT 类型

| 类型 | 识别特征 | 定位方向 |
|------|---------|---------|
| 死锁 | 被转储线程互相 `waiting to lock` | 找锁依赖环 |
| Binder 卡住 | Binder 线程 `IPCThreadState::waitForResponse` | 找对端 |
| 原生方法耗时 | 原生层 + .so 堆栈 | 找对应 .so 负责人 |
| CPU 高负载 | CPU 某一进程 > 80% | 找 CP 高负载原因 |
| 低内存 | `kswapd0` 位于前三 | 找内存泄漏/不足 |
| IO 卡住 | `mmcqd/exe_cq` ≥ 70% | 找 IO 热点进程 |
| 转储时间过长 | `dumpStackTraces` 耗时 > 60s | 找转储卡住进程 |
| SurfaceFlinger 卡住 | SF 线程阻塞 | 找 SF 阻塞原因 |

---

## 当前流程优化点 (待评估)

以下是文档化过程中识别的潜在优化方向:

### 证据提取阶段
1. **AnrManager 负载分析自动化**: 当前为 AI 手动解读 → 可预解析并输出结构化负载报告
2. **Meminfo 自动关联**: 当前手动对比 AnrManager 中负载最高的进程和 meminfo → 可自动匹配
3. **时间归一化统一**: 多来源时间格式不统一 → 可统一为 ΔT (相对 am_anr)

### 证据分析阶段
1. **调用栈模式匹配**: 当前依赖 AI 经验判断 → 可建立已知模式的规则引擎预处理
2. **Binder 对端搜索**: 手动在 trace 中搜索 → 可自动建立客户端与服务端映射
3. **无焦点窗口三步检查自动化**: 可编程判断 `onResume/relayout/finishDraw` 时序

### 根因推导阶段
1. **置信度量化**: 当前为主观评估 → 可基于证据覆盖度/一致性自动计算
2. **根因链模板**: 常见模式可预定义模板加速推导

### 报告输出阶段
1. **报告结构化**: 当前为 Markdown → 可增加机器可读的结构化输出 (JSON)
2. **证据引用追溯**: 每个结论可增加到源文件行号的引用
