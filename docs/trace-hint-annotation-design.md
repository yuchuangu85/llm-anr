<a id="trace-过滤--hint-注解设计分析"></a>
# Trace 过滤 + 提示注解设计分析

> 目标：在 trace.txt 过滤/裁剪的基础上，对每条保留下来的关键证据**附加机器可读的提示信息**，把"这一行/这一段意味着什么、属于哪一类 ANR 经验模式"显式告诉下游 AI，避免 AI 反复重新发明轮子并且降低误诊。
>
> **状态：本 RFC 的核心方案已分 6 个阶段落地（2026-05）。** 实施细节、当前已交付的提示 ID、对应代码位置与评测指标见 §10「实施映射表」；§6 已从"路线图"改写为"实施记录"。

---

## 1. 现状评估

### 1.1 已实现能力（`anr_evidence/trace_preprocessor.py`）

| 能力 | 实现位置 | 说明 |
|------|---------|------|
| 分段切分 + 锚点选择 | `split_trace_sections`, `_trace_section_rank` | 按 `am_anr` 时间戳挑最近的一段 |
| 线程块解析 | `_extract_thread_blocks`, `_parse_thread_header` | 名称（`name`）/ `tid` / `sysTid` / ART 状态 / `prio` / `daemon` |
| 调度信息 | `schedstatParsed`, `utm/stm/core/HZ` | 用于 CPU/调度判断 |
| 锁信息 | `_extract_lock_owner_tid`, `_extract_wait_object`, `_extract_held_mutexes` | `waiting to lock / waiting on / sleeping on` |
| 一级阻塞提示 | `_trace_block_hint` | `focus_window_wait` / `input_dispatch_wait` / `binder_*` / `monitor_contention` / `lock_contention` / `native_poll_wait` / `futex_wait` / `gpu_wait` / `native_wait` / `generic_wait_or_block` |
| 线程角色 | `_classify_thread_role` | main / binder / render / signal_catcher / jdwp / finalizer / reference_queue / worker |
| Binder 摘要 | `_build_binder_summary` | binder_wait_reply / binder_thread_pool / binder_backlog / binder_driver_io |
| 渲染摘要 | `_build_render_summary` | main_do_frame / egl_swap_wait / render_gpu_wait / render_thread_active |
| 挂起摘要 | `_build_suspend_summary` | STW 暂停 / `VMWAIT` 聚集 / 疑似调试器干扰 |
| CPU 摘要 | `_build_cpu_summary` | scheduler_pressure / cpu_busy_execution + 主线程等待/运行比 |
| 可疑线程评分 | `_thread_suspicion`, `_select_suspicious_threads` | 主线程 +2、`blockHint` +2、ANR 信号 +3 |
| 栈顶帧摘录 | `nativeTopFrame` / `javaTopFrame` / `looperFrame` / `binderDriverFrame` / `renderDriverFrame` | 用于提示词中的快速定位 |

### 1.2 输出给 AI 的现状（`anr_evidence/ai_context.py`）

`_trace_context()` 返回的字典：

```text
{
  "lines":   [compactedLines …],          ← 纯文本，不含 hint
  "warnings": [],
  "metadata": {
    "sectionCount", "selectedSectionIndex",
    "processName", "pid",
    "mainThread": { threadName/tid/sysTid/threadState/blockHint/
                    nativeTopFrame/javaTopFrame/looperFrame },
    "suspiciousThreadCount"
  }
}
```

`_render_cache_markdown()` 仅把 `compactedLines` 原样渲染到 Markdown ```text 代码块；元数据被以 `Metadata: <repr(dict)>` 一行塞进去，Binder/渲染/挂起/CPU 等摘要**完全没进提示词**，可疑线程列表也没进提示词。

<a id="13-痛点小结更新于-2026-05原-5-项痛点-status"></a>
### 1.3 痛点小结（更新于 2026-05，原 5 项痛点状态）

1. ✅ **已解决** — **AI 拿到的 trace 仍然是裸文本**：`### Deadlock Detection` / `### Trace Hints` / `### AnrManager Summary` 三个新小节直接在 `cache.md` 中输出结构化结论；行级 `▸ HINT[id, confidence]: …` 内联标注由 `_inject_hint_markers` 在 `ai_context.py` 中注入。
2. ✅ **已解决** — **没有"经验模式"层**：22 个提示 ID 已交付（见 §10），覆盖死锁/锁竞争、NativePollOnce 真假、Binder/SP/IO/DB/GC/渲染/网络主线程模式、AnrManager 派生模式；新增模式走 `pattern_catalog.py` 数据驱动入口，0 代码改动。
3. ✅ **已解决** — **提示与原始行没有挂钩**：`_inject_hint_markers` 在原始 `- waiting to lock <0x…>` 行下追加 `▸ HINT[…]:`，不修改原文本；ESS 元数据同步携带 `traceHints` 列表。
4. ✅ **已解决** — **没有矛盾/置信度标注**：每个提示强制 `confidence ∈ {weak, strong, critical}` + `severity ∈ {info, warning, critical}`；`NATIVE_POLL_BUT_BUSY` / `NATIVE_POLL_IDLE_LIKELY` / `NATIVE_POLL_AMBIGUOUS` 三选一互斥；跨源融合 (`cross_source_fusion.py`) 自动升级置信度并附 `corroboratingEvidence`。
5. ✅ **已解决** — **无可机器消费的模式定义**：`build_ai_context` 返回的 `groups[i].trace.traceHints` / `.deadlockHints` / `.lockGraph` 均是字典列表，可直接被 ESS / 多智能体 / `eval.py` 消费；`ai_prompt.md` 末尾的 "Required Output — Structured JSON Tail" 强制 AI 也按模式定义回包。

---

## 2. 全量 ANR 场景库（基于 wiki + 业界经验）

> 这是后续提示模式目录的输入。每条 = (类别, 触发判定, 典型片段, 推论强度, 后续动作)。所有片段均来自仓库 `wiki/`。

<a id="21-主线程阻塞类main-thread-blocked--waiting"></a>
### 2.1 主线程阻塞类（阻塞 / 等待）

| ID | 模式 | 检测信号（在 trace 内） | 推论 | 强度 |
|----|---------|----------------------|------|------|
| `LOCK_CONTENTION_BLOCKED` | 主线程被 Java 监视器阻塞 | 主线程状态 `Blocked/MONITOR` + `- waiting to lock <0x…> held by thread N` | 锁竞争，需要追踪持锁线程 | 强 |
| `LOCK_OWNER_SLEEPING` | 锁持有者主动睡眠 | 主线程 `waiting to lock` + 持锁者块含 `Thread.sleep` / `sleeping on` / `TimedWaiting` | 持锁后异步耗时（典型：SP / 数据库） | 强 |
| `LOCK_OWNER_BLOCKED` | 锁持有者也被另一线程阻塞（链式锁） | 主→A 等锁，A→B 等锁，B→…（持锁者链 ≥ 2 跳）但**未成环** | 链式阻塞（不是死锁，按链反向解） | 强 |
| `DEADLOCK_CYCLE` | 持锁者链构成环（多线程互锁） | 等锁链上出现重复 `tid`，且**所有节点处于 `Blocked/MONITOR`** | **死锁（`CONFIRMED`，已确认）**，满足互斥+占有等待+循环等待 | 极强 |
| `DEADLOCK_LIKELY` | 等锁环存在但状态不齐 | 锁图存在 SCC，但环上至少 1 个 tid 当前不在 `Blocked` | **疑似死锁**（采样瞬间未对齐，建议跨 trace 复核） | 强 |
| `DEADLOCK_SELF` | `owner == waiter`（自环） | `tid_x waiting to lock <0xA> held by tid_x` | 多见于错误重入 / 不匹配的解锁，需复核 | 中 |
| `CROSS_PROCESS_DEADLOCK_SUSPECTED` | 跨进程死锁线索 | 主线程 `binder_wait_reply` + 同 trace 另一线程持有服务端会回调本进程时需要的锁 | **疑似跨进程死锁**，单 trace 不可证；建议重新探查对端 | 弱 |
| `MAIN_OBJECT_WAIT` | 主线程 `Object.wait()` | `Waiting` + `- waiting on <0x…>` 且无超时 | 主线程显式等通知，常见于 WebView/GLThread | 中 |
| `MAIN_TIMED_WAIT` | 主线程 `wait(t)/sleep(t)` | `TimedWaiting/Sleeping` + 超时调用栈 | 主线程主动等，**设计问题** | 中 |
| `SP_APPLY_WAIT` | SP `QueuedWork.waitToFinish` 等待 | 栈中 `QueuedWork.waitToFinish` / `mcr.writtenToDiskLatch.await` | SP `apply` 同步落盘，主线程被阻塞 | 强 |
| `SP_LOAD_WAIT` | SP `awaitLoadedLocked` | 栈中 `SharedPreferencesImpl.awaitLoadedLocked` | SP 文件未加载完，读时阻塞 | 强 |

<a id="22-native-假性空闲类nativepollonce--barrier"></a>
### 2.2 原生层假性空闲类（NativePollOnce / 同步屏障）

| ID | 模式 | 检测信号 | 推论 | 强度 |
|----|---------|---------|------|------|
| `NATIVE_POLL_IDLE_LIKELY` | 真正空闲 | 主线程 `Native` + `epoll_pwait/nativePollOnce` + `schedstat.runNs` 极小 + 无可疑历史 | 大概率消息队列空闲，trace 是"替罪羊" | 弱 |
| `NATIVE_POLL_BUT_BUSY` | NativePollOnce 但实际有 CPU 消耗 | 主线程 `Native` + `nativePollOnce` + `utm+stm` 大 / `runNs` 显著 | **历史消息严重耗时** 或 **同步屏障假死**，需查 Looper 监控 | 强 |
| `BARRIER_STUCK` | 同步屏障长期未撤 | 同上 + 若有应用层 `MessageQueue` 监控提示 `nextPollTimeoutMillis=-1` | 屏障消息未被异步消息触发或未移除 | 强 |
| `MESSAGE_FLOOD` | 消息密集 | （需要应用侧消息监控）当前 trace 表征：主线程 NativePollOnce + `utm` 极高 + 待调度消息巨多 | 业务异常密集执行 | 中 |

### 2.3 Binder 卡顿类

| ID | 模式 | 检测信号 | 推论 | 强度 |
|----|---------|---------|------|------|
| `MAIN_BINDER_WAIT_REPLY` | 主线程 binder 调用挂起 | 主线程栈含 `IPCThreadState::waitForResponse` / `BinderProxy.transactNative` | 等服务端进程返回 | 强 |
| `BINDER_DRIVER_IO` | 阻塞在 binder ioctl | 栈含 `talkWithDriver` / `__ioctl` / `ioctl+` | 卡 binder 驱动层 | 强 |
| `BINDER_BACKLOG` | binder 线程池堆积 | 多个 `Binder:xxx_N` 在 `joinThreadPool/getAndExecuteCommand`，且对端拥塞 | 服务端忙不过来或被锁 | 中 |
| `BINDER_USED_UP` | 服务端 binder 用尽 | 栈含 `Binder.blockUntilThreadAvailable` / `Watchdog$BinderThreadMonitor.monitor` | 32 个 binder 全占满 | 强 |
| `CROSS_PROCESS_DEADLOCK` | 跨进程互等 | 主线程 binder 等服务端，服务端又通过 binder 回到本进程并被本进程主线程持锁阻塞 | 跨进程死锁（典型：NFC/抖音案例） | 极强 |
| `BINDER_TO_SYSTEM_SERVER` | 等待 system_server | binder 对端含 `system_server` 关键模块（PMS/AMS/WMS/SMS 等） | system_server 侧排查 | 中 |
| `BINDER_TO_VOLD` | 等待 vold | 栈含 `IVold$Stub$Proxy` / `StorageManagerService.monitor` | 存储/挂载子系统卡 | 中 |

### 2.4 渲染/UI 子系统类

| ID | 模式 | 检测信号 | 推论 | 强度 |
|----|---------|---------|------|------|
| `MAIN_DO_FRAME_LONG` | 主线程在 `doFrame` 中 | 主线程栈含 `Choreographer.doFrame` / `ViewRootImpl.performTraversals/draw` 且不在 binder | 单帧渲染耗时长 | 中 |
| `EGL_SWAP_WAIT` | 等 GPU 缓冲区交换 | 栈含 `eglSwapBuffers` / `nSetStopped` | GPU/Surface 慢 | 中 |
| `RENDER_THREAD_GPU_WAIT` | RenderThread 等 GPU | RenderThread 栈含 `DrawFrameTask::drawFrame` + `pthread_cond_wait` | GPU 后端阻塞 | 中 |
| `WAITING_FOR_BUFFER` | BLAST 队列满 | logcat 关键字 `BLASTBufferQueue: waiting for available buffer` + 主线程在 `nSetStopped` 或 `syncAndDrawFrame` | 帧缓冲被占满 | 强 |
| `SYNC_GROUP_TIMEOUT` | WMS 同步超时 | logcat `BLASTSyncEngine: Sync group N timeout` + `failed to waitNextVsync` | 窗口绘制未完成→焦点切换失败 | 强 |
| `SF_HANG` | SurfaceFlinger 卡 | logcat `SF hang Time` / `surfaceflinger hang` + 主线程在 `SurfaceControl.*` | SF 侧问题 | 强 |

### 2.5 GC / 虚拟机类

| ID | 模式 | 检测信号 | 推论 | 强度 |
|----|---------|---------|------|------|
| `STW_PAUSE` | STW 暂停 | `suspendedThreadCount/total ≥ 0.5` 且 ≥2 | GC STW 或 SignalCatcher 正在转储 | 中 |
| `SIGNAL_CATCHER_DUMPING` | SignalCatcher 占用 | SignalCatcher 状态 `RUNNABLE`，其余线程多为 `SUSPENDED` | 当前正被采 trace（多半是真 ANR 触发后的产物，**不要倒因为果**） | 强 |
| `GC_HEAP_PRESSURE` | GC 频繁 | HeapTaskDaemon CPU 占比异常（来自 AnrManager 的 CPU 使用率段） | 内存压力，参考 `low_memory` | 中 |
| `DEBUGGER_SUSPICION` | 调试干扰 | `dsCount > 0` 多线程 | 设备处于调试状态，结论需打折 | 弱 |

### 2.6 IO 类

| ID | 模式 | 检测信号 | 推论 | 强度 |
|----|---------|---------|------|------|
| `MAIN_FILE_IO` | 主线程 IO | 栈含 `libcore.io.Linux.writeBytes/readBytes` / `BlockGuardOs.write/read` / `FileOutputStream.write` | 主线程同步 IO | 强 |
| `MAIN_DB_IO` | 主线程数据库 | 栈含 `SQLiteConnection` / `SQLiteDatabase` / `query()` | 主线程数据库操作 | 强 |
| `STORAGE_MANAGER_WAIT` | StorageManager binder | 栈含 `IStorageManager$Stub$Proxy.mkdirs` / `StorageManagerService.waitForLatch` | 存储未挂载/超时（FUSE） | 中 |
| `IO_BOUND_HINT` | 整体 IO 压力（来自 logcat 旁证） | logcat `iowait` 高 / `kworker`/`mmcqd` 排名前 | 系统受 IO 限制 | 中 |

### 2.7 系统/性能背景类（来自 AnrManager 段，不严格属于 trace 但同窗口）

| ID | 模式 | 检测信号 | 推论 | 强度 |
|----|---------|---------|------|------|
| `LOW_MEMORY` | 低内存 | `lowmemorykiller` / `kswapd0` 高 / `onTrimMemory level=80` | 内存紧张可能放大 ANR | 中 |
| `CPU_PRESSURE_OTHER_PROCESS` | 他进程抢 CPU | AnrManager CPU 使用率中目标进程低、其他进程极高 | 外部资源抢占 | 中 |
| `THERMAL_THROTTLING` | 限频 | 内核 `set_adaptive_cpu_power_limit` / `Power/PPM` 非 0 | CPU 被限频 | 弱 |
| `DEX2OAT_RUNNING` | dex2oat 占用 | 进程列表含 `dex2oat` | 安装/优化期 ANR | 中 |
| `DUMP_TAKES_TOO_LONG` | 转储过长（产物） | `ActivityManager.dumpStackTraces` / `debuggerd_trigger_dump` | trace 期间 ANR 是转储自身导致 | 强 |
| `MONKEY_TEST_ARTIFACT` | Monkey 自伤 | 主线程栈含 `com.android.commands.monkey` | Monkey 测试自身写日志致 IO 受限 | 中 |

### 2.8 应用挂死/其它

| ID | 模式 | 检测信号 | 推论 | 强度 |
|----|---------|---------|------|------|
| `APP_KILLED_DURING_ANR` | 进程被杀 | logcat `am_kill` / `Process.killProcess` | ANR 演化成进程被杀 | 中 |
| `WEBVIEW_INIT_DEADLOCK` | WebView 初始化死锁 | 多线程栈含 `WebViewChromiumFactoryProvider.getStatics` + `Object.wait` | 主线程持锁 + 子线程等主线程消息 | 强 |
| `SUSPEND_BG_DEMOTE` | 应用后台降优先级 | `cgrp=bg_non_interactive` + 主线程 `Suspended` + `nice` 上升 | 应用被压后台后挂起 | 中 |

---

<a id="3-hint-注解-schema-设计"></a>
## 3. 提示注解模式设计

> 核心原则：**提示是结构化对象，独立成字段；同时在渲染给 AI 时，以 `▸ HINT[id, severity]: …` 内联标记附在引发提示的关键行旁边**。

### 3.1 数据模型

```
TraceHint
 ├─ id            : str   # 模式 ID，如 "DEADLOCK_CYCLE"
 ├─ category      : enum  # main_block | binder | render | gc | io | system | meta
 ├─ severity      : enum  # critical | warning | info
 ├─ confidence    : enum  # strong | medium | weak
 ├─ scope         : enum  # line | thread | section | global
 ├─ anchor        : { tid?: str, line_index?: int, source_kind: "trace" }
 ├─ evidence      : list[str]            # 触发该 hint 的原文行（用于 AI 反查）
 ├─ message       : str                  # 一句话面向 AI 的解释
 ├─ wiki_refs     : list[str]            # 相对路径，如 wiki/实例/ANR-死锁.md
 └─ next_actions  : list[str]            # AI 后续应做的事，如 "查 owner_tid=189 的栈"
```

### 3.2 在已有结构上的挂载点

`preprocess_trace_content()` 现有返回值新增三段：

```
{
  ...,
  "hints":              [TraceHint, ...],           # 全部 hint 平铺
  "hintIndexByTid":     {"1": ["DEADLOCK_CYCLE", "MAIN_BINDER_WAIT_REPLY"], …},
  "annotatedLines":     [{"line": "...", "hintIds": ["MAIN_DO_FRAME_LONG"]}, ...],
  "lockGraph":          {                            # 死锁专用
       "edges": [{"from_tid":"1","to_tid":"189","object":"0x0ca44263"}, ...],
       "cycles": [["1","189","170","27","189"]]
  }
}
```

<a id="33-渲染到-ai-prompt-的格式cachemd-trace-段"></a>
### 3.3 渲染到 AI 提示词的格式（`cache.md` Trace 段）

````markdown
### Trace
- 元数据：…
- 提示：
  - ⛔ CRITICAL  DEADLOCK_CYCLE     [strong]   chain=1→189→170→27→189   wiki:实例/ANR-死锁.md
  - ⚠️ WARNING  MAIN_BINDER_WAIT_REPLY [strong] tid=1                  wiki:MTK/swt/18.Binder Stuck.md
  - ℹ️ INFO     STW_PAUSE         [medium]   suspended=12/22         wiki:Find the unresponsive thread.md

```text
"main" prio=5 tid=1 Blocked
  ▸ HINT[MAIN_BINDER_WAIT_REPLY,strong]: 主线程在等 binder 返回；查对端进程
  ▸ HINT[DEADLOCK_CYCLE,strong]:        owner_tid=189 又在等 tid=170 的锁
  - waiting to lock <0x0ca44263> held by thread 189
  ▸ HINT[LOCK_OWNER_BLOCKED,strong]:   owner 链：1 → 189 → 170 → 27 → 189 (CYCLE)
  at com.android.server.pm.PackageManagerService.isPackageSuspendedForUser(PackageManagerService.java:17700)
…
```
````

要点：
- **一行只追加它直接触发的提示**，避免噪声；`section/global` 级提示单独走头部 `Hints:` 摘要。
- 用 `⛔ ⚠️ ℹ️` 严重度前缀 + `[strong/medium/weak]` 置信度，让 AI 区分"必须采纳"和"参考"。
- `wiki:` 引用让 AI 知道这是经过人工经验验证的模式，而不是它现编。

<a id="34-在-essevidence-slice-schema里的承接"></a>
### 3.4 在 ESS（证据切片模式）里的承接

`evidence_slice.py` 已经有 `EvidenceSlice`。建议给每条与 trace 相关的切片增加：

```
slice.metadata["traceHints"] = ["MAIN_BINDER_WAIT_REPLY", "DEADLOCK_CYCLE"]
slice.metadata["traceTid"]   = "1"
```

这样多智能体场景里，子智能体可以直接 `filter(hint=DEADLOCK_CYCLE)` 取证。

---

## 4. 算法设计

<a id="41-分层匹配器matcher"></a>
### 4.1 分层匹配器

定义一个匹配器基类，每个匹配器负责一类模式：

```
class HintMatcher:
    id: str
    category: str
    requires: tuple[str, ...]   # 依赖哪些已计算的字段：threads / lockGraph / etc.
    def match(self, ctx: TraceContext) -> Iterable[TraceHint]: ...
```

执行顺序固定为三层：

1. **L1 单线程模式**（基于已有的 `block_hint`、`binderCallKind`、`renderCallKind`）
   - 例：`MAIN_BINDER_WAIT_REPLY` = `isMainThread and binderCallKind == "binder_wait_reply"`
   - 例：`MAIN_FILE_IO` = 主线程栈包含 `libcore.io.Linux.write/readBytes`
2. **L2 跨线程关系模式**（依赖 L1 的 `lockOwnerTid` 与 `threads`）
   - **构建锁图**：`(tid_waiter, lock_obj) -> tid_owner`，复用现有 `_extract_lock_owner_tid`。
   - 跑 Tarjan / DFS 找环；找到 → `DEADLOCK_CYCLE`。
   - 链 ≥2 但无环 → `LOCK_OWNER_BLOCKED`。
   - 持锁者处于 `Sleeping/TimedWaiting` → `LOCK_OWNER_SLEEPING`。
3. **L3 全局/旁证模式**（依赖挂起/CPU 摘要，可选合并 logcat、AnrManager 段）
   - `STW_PAUSE`、`SIGNAL_CATCHER_DUMPING`、`SP_APPLY_WAIT`、`WEBVIEW_INIT_DEADLOCK`（多线程组合）
   - `WAITING_FOR_BUFFER`、`SYNC_GROUP_TIMEOUT`、`SF_HANG` 这些**主信号在 logcat**，但需要在 trace 主线程上挂提示，所以采取**双源融合**：匹配器同时接收 `package.sources.logcat.content`，命中关键字后回填到主线程提示。

<a id="42-pattern-catalog-数据结构"></a>
### 4.2 模式目录数据结构

考虑可维护性，把模式写成声明式 YAML/Python 字典（**不是硬编码大堆 `if`**）：

```python
PATTERN_CATALOG = [
    {
        "id": "MAIN_BINDER_WAIT_REPLY",
        "category": "binder",
        "severity": "warning",
        "confidence": "strong",
        "when": {
            "thread.isMainThread": True,
            "thread.binderCallKind": "binder_wait_reply",
        },
        "evidence_frames": ["IPCThreadState::waitForResponse", "BinderProxy.transactNative"],
        "message": "主线程正在等 binder 对端返回；定位对端进程/线程及阻塞点。",
        "wiki_refs": ["wiki/MTK/swt/18.Binder Stuck.md"],
        "next_actions": ["搜索 binder 对端 (FAQ22212)", "检查对端线程是否 D 态或锁等"],
    },
    {
        "id": "DEADLOCK_CYCLE",
        "scope": "global",
        "category": "main_block",
        "severity": "critical",
        "confidence": "strong",
        "when": {"lockGraph.cycle": True},
        "message": "线程间存在等锁环，符合死锁四要件。",
        "wiki_refs": ["wiki/实例/ANR-死锁.md", "wiki/MTK/swt/17.Deadlock.md"],
    },
    ...
]
```

匹配引擎按 `when` 组合做布尔判断；`evidence_frames` 用作行级注解（命中的栈帧旁边追加 `▸ HINT`）。

### 4.3 锁图构建伪代码

```
edges = []
for t in threads:
    if t.lockOwnerTid:
        edges.append((t.tid, t.lockOwnerTid, t.waitObject))
# Tarjan SCC
sccs = tarjan(edges)
cycles = [scc for scc in sccs if len(scc) > 1 or self_edge(scc)]

# 衍生 hint
if cycles:
    yield DEADLOCK_CYCLE(scope="global", evidence=…)
for waiter, owner, obj in edges:
    if owner_thread.threadState in {"sleeping","timed_waiting"}:
        yield LOCK_OWNER_SLEEPING(scope="thread", anchor=waiter)
    elif owner_thread.lockOwnerTid:    # owner 自己也在等锁
        yield LOCK_OWNER_BLOCKED(scope="thread", anchor=waiter)
```

<a id="431-死锁判定细则deadlock-detection-rules"></a>
### 4.3.1 死锁判定细则

死锁是 trace 唯一**可独立判定**的强结论之一（绝大多数其它结论都需要 logcat / EventLog 旁证）。下面给出从理论到代码可落地的完整判定流程。

#### A. 理论前提（Coffman 四要件）

| 条件 | trace 可观察？ | 说明 |
|------|--------------|------|
| 互斥 | 隐含 | Java `monitorenter` / `synchronized` 天然满足 |
| 占有并等待 | ✅ 直接观察 | `- locked <0xA>` 同时 `- waiting to lock <0xB>` |
| 不可剥夺 | 隐含 | JVM 监视器不可被抢占 |
| 循环等待 | ✅ 直接观察 | 锁图存在环 |

trace 能直接观察到 2 + 4 即可判定死锁，1 和 3 由监视器语义保证。

#### B. trace 中可作为「等」与「持」的证据

按可信度从高到低：

| 证据行 | 含义 | 是否带 `owner_tid` | 用于建图？ |
|--------|------|-----------------|-----------|
| `- waiting to lock <0xA> (a Foo) held by thread N` | 等待监视器，N 持有 | ✅ | ✅ 主要边来源 |
| `- locked <0xA> (a Foo)` | 当前线程持有该监视器 | — | ✅ 持锁者标记 |
| 线程状态 `Blocked` / `state=Monitor` | 阻塞在监视器 | — | ✅ 必备前提 |
| `- waiting on <0xA>` | `Object.wait()`，**不是争抢** | ❌ | ❌（需 `notify`） |
| `- sleeping on <0xA>` | `Thread.sleep` 或 `Object.wait(timeout)` | ❌ | ❌ |
| `- parking to wait for <0xA>` | `LockSupport.park` (j.u.c) | 通常 ❌ | ⚠️ 弱依据 |
| `held mutexes= "mutator lock"...` | ART 内部互斥 | — | ❌（噪声） |

**关键**：只有 `waiting to lock ... held by thread N` 这类带 `held by thread N` 的行能用来建图。`waiting on` / `parking` 没有持锁者，属于「逻辑等待」，不能直接判死锁。

#### C. 算法：锁图 + Tarjan SCC

```
输入：extract_trace_threads 返回的 threads[]（压缩前的完整线程数据）
输出：List[DeadlockHint]

# Step 1 — 索引
owner_of_lock = {}                  # lock_obj -> owning tid（来自 "- locked <0xA>"）
for t in threads:
    for lk in t.heldLocks:
        owner_of_lock[lk] = t.tid

# Step 2 — 建图（仅 Blocked/Monitor 状态线程贡献入度边）
G = DiGraph()
for t in threads:
    if t.state not in {"Blocked", "Monitor"}:
        continue
    for w in t.waitingLocks:        # 来自 "- waiting to lock <0xA> held by thread N"
        owner_tid = w.heldByTid or owner_of_lock.get(w.lock)
        if owner_tid and owner_tid != t.tid:
            G.add_edge(t.tid, owner_tid, key=w.lock)
        elif owner_tid == t.tid:
            yield DEADLOCK_SELF(tid=t.tid, lock=w.lock)

# Step 3 — 找环（Tarjan 强连通分量）
sccs = tarjan_scc(G)
for scc in sccs:
    if len(scc) == 1 and not G.has_edge(scc[0], scc[0]):
        continue                     # 单点无环，跳过
    states = {threads[tid].state for tid in scc}

    if states <= {"Blocked", "Monitor"}:
        yield DEADLOCK_CYCLE(
            tids=scc,
            edges=edges_in(scc),
            confidence="strong",
            severity="critical",
        )
    else:
        yield DEADLOCK_LIKELY(
            tids=scc,
            edges=edges_in(scc),
            confidence="medium",
            note="环上存在非 Blocked 线程，可能采样未对齐，建议跨 trace 复核",
        )

# Step 4 — 链式（无环但 ≥2 跳）
for tid in topological_order(G):
    chain = follow_owner_chain(tid, G, max_depth=8)
    if len(chain) >= 3:              # main -> A -> B
        yield LOCK_OWNER_BLOCKED(chain=chain)
```

#### D. 判定结果的强弱分级

| 提示 ID | 触发条件（精确） | 何时输出 | 置信度（`confidence`） |
|---------|----------------|---------|-----------|
| `DEADLOCK_CYCLE` | SCC 含 ≥2 节点，**且环上所有 `tid` 状态 ∈ {Blocked, Monitor}** | 一定输出 `critical` | strong |
| `DEADLOCK_LIKELY` | SCC 含 ≥2 节点，但有 `tid` 不在 `Blocked` | 输出 `warning` + 建议重新探查 | medium |
| `DEADLOCK_SELF` | `owner_tid == waiter_tid`（自环） | `warning` + 建议查重入实现 | medium |
| `LOCK_OWNER_BLOCKED` | 持锁者链 ≥ 2 跳但**无环** | 不是死锁，但是连锁阻塞 | strong |
| `LOCK_OWNER_SLEEPING` | 持锁者状态 ∈ {Sleeping, TimedWaiting} | 持锁打盹 | strong |
| `LOCK_CONTENTION_BLOCKED` | 仅一条等锁边、持锁者在 `Runnable / Native` | 普通锁竞争 | strong |
| `CROSS_PROCESS_DEADLOCK_SUSPECTED` | 主线程 `binder_wait_reply` + 同 trace 内对端线程被本进程线程持锁阻塞（典型见下） | 弱置信，提示去探查对端 | weak |

#### E. 反例（trace 看似死锁但**不能**判定为死锁）

落地时必须显式排除，否则提示会失真：

1. **采集副作用导致的 SUSPENDED**
   `Signal Catcher` 在转储期间所有修改堆的线程被 `Suspended`，看起来「全员阻塞」但**不是死锁**。
   排除方式：对 `state=Suspended` 的线程不参与建图；同时由 `_build_suspend_summary` 输出 `dumpInProgress=true` 旁证。

2. **`waiting on` / `parking` 无持锁者**
   这是**等通知**而非**等锁**，技术上不构成 Coffman 第 4 要件。
   `CountDownLatch.await`、`Future.get`、`Object.wait()` 永远等不到唤醒方，是**逻辑死锁**，应输出独立提示（如 `LOGICAL_WAIT_NEVER_NOTIFIED`），**不要混入 `DEADLOCK_CYCLE`**。

3. **原生层 / pthread 互斥锁死锁**
   `__lll_lock_wait` / `pthread_mutex_lock` 在 trace 中通常**没有 owner_tid 标注**。
   只能从原生堆栈推断，建议输出 `NATIVE_LOCK_SUSPECTED`（弱置信），不进 SCC。

4. **跨进程死锁**
   单进程 trace 看不到对端服务端持有的锁状态，**绝不能直接判 `DEADLOCK_CYCLE`**。
   只输出 `CROSS_PROCESS_DEADLOCK_SUSPECTED`，并在 `next_actions` 中要求重新探查对端进程 trace。

5. **持锁后睡眠 / 等待（占而不释）**
   这是**性能问题**或**设计问题**，不构成死锁。归类到 `LOCK_OWNER_SLEEPING`，`severity` 不要设为 `critical`。

6. **ART 内部锁噪声**
   `held mutexes= "mutator lock"(shared held)` / `"runtime shutdown lock"` 等是 ART 自身锁，**永远忽略**，不进 `owner_of_lock`。

#### F. 跨 trace 一致性（MTK SOP 推荐）

**单帧 trace 是「快照」，存在采样误差**。MTK 工程实践要求：

> 若同一次 ANR 抓到 ≥2 份 trace（间隔 1–3s），且锁图环路在两份 trace 中**节点集合一致**，则可定级为 `CONFIRMED_DEADLOCK`；只有 1 份 trace，最高定级为 `DEADLOCK_CYCLE`（置信度为 `strong`）— 字面意义已经成立，但工程上仍鼓励复核。

实现上：
- `replay/` 目录已支持多次转储比对；可在阶段 5 增加 `lock_graph_consistency_check(trace_a, trace_b)`，命中后把 `confidence` 升级为 `confirmed`，并在提示上注明 `consistency_passed=true`。

#### G. 旁证（提升置信度但非必需）

以下 logcat / 内核信号**与 trace 死锁结论一致即视为加权**，不一致则降级为 `DEADLOCK_LIKELY`：

- logcat: `Watchdog`、`BinderThreadMonitor.monitor` 输出「Blocked in monitor on …」
- 内核日志: `INFO: task xxx:NNN blocked for more than 120 seconds` (`hung_task`)
- AnrManager 段: `Reason: Input dispatching timed out (...)` + `CPU usage from … 100% load`

<a id="h-输出示例machine-readable"></a>
#### H. 输出示例（机器可读）

```jsonc
{
  "id": "DEADLOCK_CYCLE",
  "category": "main_block",
  "severity": "critical",
  "confidence": "strong",
  "scope": "global",
  "deadlock_evidence": {
    "tids": [1, 189, 170, 27],
    "edges": [
      {"waiter": 1,   "owner": 189, "lock": "0x0ca44263", "type": "MyManager"},
      {"waiter": 189, "owner": 170, "lock": "0x0ee7c7ea", "type": "ConfigStore"},
      {"waiter": 170, "owner": 27,  "lock": "0x0bd0df19", "type": "Cache"},
      {"waiter": 27,  "owner": 189, "lock": "0x0ca44263", "type": "MyManager"}
    ],
    "states": {"1": "Blocked", "189": "Blocked", "170": "Blocked", "27": "Blocked"}
  },
  "message": "检测到 4 线程死锁环 (1→189→170→27→189)，所有节点处于 Blocked。",
  "wiki_refs": ["wiki/实例/ANR-死锁.md", "wiki/MTK/swt/17.Deadlock.md"],
  "next_actions": [
    "查看 tid=189 持锁后的栈，定位 MyManager 与 ConfigStore 的加锁顺序冲突",
    "建议在另一份 trace（dump #2）上复核，命中后升级为 CONFIRMED"
  ]
}
```

#### I. 实现位置一览（落地清单）

| 改造点 | 文件 | 函数 | 说明 |
|--------|------|------|------|
| 抽取「持锁」 | `anr_evidence/trace_preprocessor.py` | `_extract_held_locks(block)` (新增) | 解析 `- locked <0xA>` |
| 抽取「等锁 + 持锁者」 | 同上 | `_extract_lock_owner_tid` (现存) | 已支持，复用 |
| 锁图 + Tarjan | 同上 | `_build_lock_graph(threads)` (新增) | 输出 `lockGraph` 字段 |
| 死锁提示生成 | 同上 | `_emit_deadlock_hints(lock_graph, threads)` (新增) | 按 §D 输出分级提示 |
| 必须在裁剪**之前**跑 | 同上 | `preprocess_trace_content` 调度顺序 | 见 §4.4 |
| AI 透传 | `anr_evidence/ai_context.py` | `_trace_context` | 新增 `hints` / `lockGraph` 字段 |
| cache.md 渲染 | 同上 | `_render_cache_markdown` | 头部 `## Deadlock Detection` 小节 |

<a id="44-与裁剪compaction的耦合"></a>
### 4.4 与裁剪的耦合

当前 `compact_trace_section` 只保留 4 个线程块。**提示阶段必须在裁剪之前完成**（在 `extract_trace_threads` 全量结果上跑），否则持锁者链/死锁环会丢边。然后把"提示命中的线程"反向加进 `_select_thread_blocks` 的优先级里，保证证据不被裁掉：

```
priority = (
    is_main,
    has_critical_hint,        # 新增：命中 critical hint 提前
    is_owner_in_lock_graph,   # 新增：在锁图中且被引用
    native_poll, binder, -signal_hits, name,
)
```

<a id="45-noise--反例处理"></a>
### 4.5 噪声 / 反例处理

- **`SIGNAL_CATCHER_DUMPING` 反例**：当多个线程都为 `SUSPENDED` 是因为正在被转储，应明确告诉 AI"这是采集副作用，不是 ANR 根因"，避免 AI 误判 STW。已有的 `_build_suspend_summary` 要拆出 `dumpInProgress` 字段。
- **`NATIVE_POLL_IDLE_LIKELY` 与 `NATIVE_POLL_BUT_BUSY` 对比**：以 `mainThreadRunNs / mainThreadWaitNs` 比为分水岭，给出二选一提示，**绝不同时给两个**；否则等于把判断又踢给 AI。
- **跨进程死锁**：单一 trace 通常只覆盖单进程；当 AnrManager 块有"另一个进程的 trace 路径"或 logcat 提示对端进程时，输出 `CROSS_PROCESS_DEADLOCK_SUSPECTED`（弱置信），让 AI 主动重新探查。

---

## 5. AI 集成

### 5.1 三处需要改

1. `trace_preprocessor.preprocess_trace_content()` 返回 `hints` 等新字段。
2. `ai_context._trace_context()` 把 `hints` / `lockGraph` 透传到分组字段。
3. `ai_context._render_cache_markdown()` / `_append_section()`：
   - 头部输出 `Hints:` 摘要；
   - 行级注解使用 `_inject_hint_markers(lines, annotatedLines)`，在原文下面追加 `▸ HINT[…]:` 行而**不改原行内容**（保证可被原始日志反查）。
4. `_render_ai_prompt()` 在 `Required Output` 之前插入：

```
## Trace Hints Cheatsheet
- "▸ HINT[id, strong]" 表示该行被结构化模式匹配，置信度高，请优先采纳。
- "▸ HINT[id, weak]"   表示提示线索仅供方向，不可作为唯一证据。
- 若证据与 hint 冲突，请在结论里明确写"证据 X 与 hint Y 冲突，原因…"。
- 若需要查另一个 tid 的栈或 logcat 行，请在 next-step 里列出，工具会重新 probe。
```

<a id="52-multi-agent-模式已有-ai_agentpy"></a>
### 5.2 多智能体模式（已有 `ai_agent.py`）

- 管理智能体在分发任务时直接读 `package.trace.hints`，按 `category` 分发：
  - `main_block` / `binder` → 堆栈/锁子智能体；
  - `gc` / `system` / `io` → CPU/内存子智能体；
  - `render` → 渲染子智能体。
- `ReProbeRequest` 增加 `trace_hint_id` 字段，子智能体可以请求"把所有 `owner_tid` 的完整回溯堆栈给我"等定向取数。

<a id="53-报告delivery"></a>
### 5.3 报告/交付

`reporter.py` / `root_cause.py` / `delivery.py` 现状是不依赖 LLM 的模板渲染，可以直接消费提示：
- 报告"直接阻塞点"小节自动填 `category=main_block` 的最高严重度提示；
- "候选根因链路"自动展开 `lockGraph.cycles`；
- "证据强弱"自动统计 `confidence` 直方图。

---

## 6. 实施记录（已落地）

> 原计划的阶段 1–5（按目录→锁图→提示词→融合→多智能体的顺序）已被 2026-05 的 6 个新阶段全量替代。下表是真实提交顺序，每个提交自带单测 + 真值测试夹具，可独立回滚。

| 实施阶段 | 提交 | 落地内容 | 测试增量 |
|------------|--------|---------|---------|
| **死锁检测基线**（前置工作） | `7af3f8d` | 锁图 + Tarjan SCC + 7 类死锁提示（`DEADLOCK_CYCLE` / `_LIKELY` / `_SELF` / `LOCK_OWNER_*` / `LOCK_CONTENTION_BLOCKED` / `CROSS_PROCESS_DEADLOCK_SUSPECTED`），跨 trace 一致性比对 (`consolidate_deadlock_across_traces`)，行级提示注解 | +23 个单元测试 |
| **阶段 1** — NativePollOnce 真假判定 | `af2990d` | 基于主线程 `schedstat` (`runNs`/`waitNs`) 把 70% 的"主线程在 nativePollOnce"假阳性切成 `NATIVE_POLL_BUT_BUSY`（`strong`）/ `NATIVE_POLL_IDLE_LIKELY`（`weak`）/ `NATIVE_POLL_AMBIGUOUS`，三者互斥 | +6 个单元测试 |
| **阶段 2** — AnrManager 结构化解析 | `a8570fa` | 新模块 `anrmanager_parser.py`：PSI `memory.some/full`、CPU 窗口与总占用、CPU 占用最高的进程、ANR 原因文本 → 结构化字段；派生 4 个 `system` 级提示：`SYSTEM_CPU_SATURATED` / `SYSTEM_IO_PRESSURE` / `SYSTEM_MEMORY_PRESSURE` / `ANR_REASON_CLASSIFIED`；cache.md 新增 `### AnrManager Summary` | +7 个单元测试 |
| **阶段 3** — 真值评测框架 | `862d664` | `anr_evidence/eval.py` + `scripts/run_eval.py` + `tests/fixtures/eval/` 8 个初始用例；适配提示的指标定义（`forbidden` 触发才计假阳性 `fp`，无声明的提示保持中性）；CI 强制 `pass_rate=1.0`，每个文档列明的提示 ID 必须有 ≥1 个测试夹具覆盖 | +5 条回归断言 |
| **阶段 4** — MAIN_* 主线程模式提示 | `b9251c9` | 一次性交付 7 条置信度为 `strong` 的模式：`MAIN_BINDER_WAIT_REPLY` / `MAIN_SP_APPLY_WAIT` / `MAIN_IO_BLOCKED` / `MAIN_DB_BLOCKED` / `MAIN_GC_PAUSED` / `MAIN_RENDER_WAIT_FENCE` / `MAIN_NETWORK_BLOCKED`；对应 4 个新评测测试夹具 | +10 个单元测试 + 4 个测试夹具 |
| **阶段 5** — 模式目录数据化 | `9f6492b` | 抽出 `pattern_catalog.py` 单独模块；引擎支持 `anyMatch`（OR，或）/ `allMatch`（AND，与）/ `notMatch`（NOT，非）复合谓词；模式校验测试；新增 2 条模式 (`MAIN_PROVIDER_QUERY` / `MAIN_WEBVIEW_LOAD`) **0 代码改动**，纯数据落地 | +6 个单元测试 |
| **阶段 6** — 跨源融合 + AI JSON 模式 | `ca97f71` | `cross_source_fusion.py`：10 条 logcat / 内核旁证规则把 trace 提示置信度升级 (`weak→strong→critical`)；`ai_prompt.md` 末尾追加 "Required Output — Structured JSON Tail" 17 字段 JSON 输出契约，强制 AI 引用真实出现过的提示 ID | +9 个单元测试 + 1 个测试夹具 |

### 当前指标（2026-05）

| 指标 | 数值 |
|------|------|
| 已交付提示 ID | **22** |
| 评测语料 | **13 个用例** / 15 个提示 ID / 全部 P=R=F1=1.0 |
| 单测总数 | **249**（0 个回归问题，`compileall` 通过） |
| AI 端结构化输出 | `### Deadlock Detection` + `### Trace Hints` + `### AnrManager Summary` + 行内 `▸ HINT[…]` + JSON 尾部 |

### 与原 §6 路线图的对应关系

| 原计划 | 实际落地 |
|--------|---------|
| 阶段 1 目录与模式 | 阶段 5（目录抽离 + 引擎）+ 全 6 个阶段持续填充 |
| 阶段 2 锁图 | 前置工作提交 `7af3f8d`（在本次 6 个阶段之前已落地） |
| 阶段 3 AI 提示词改造 | 贯穿阶段 1-6（每个阶段都同步更新速查表 + 渲染） |
| 阶段 4 双源融合与噪声治理 | 阶段 6（跨源融合）+ 阶段 1（NativePollOnce 互斥消歧） |
| 阶段 5 多智能体 / 回放 | 部分落地：JSON 尾部提供多智能体调度入口；回放比对待续 |

### 仍未落地（下一阶段候选）
- 原生崩溃 / tombstone 联合分析
- Systrace / perfetto 接入
- 历史趋势 / 同类 ANR 聚合统计
- 隐私脱敏 / 客户脱密
- UI / 可视化（提示时间轴 / 锁图渲染）
- 多智能体 `ReProbeRequest` 的提示 ID 路由

---

## 7. 风险与折中

| 风险 | 影响 | 缓解 |
|------|------|------|
| 模式维护成本 | 目录越来越大，规则膨胀 | 用声明式字典 + 单测覆盖；新增模式必须带正反例测试夹具 |
| 误报让 AI 走偏 | AI 过度信任提示 | 强制 `confidence` 字段；提示词速查表教 AI 处理冲突；`weak` 提示不参与决策权重 |
| 提示与原始行不一致 | 行被裁剪但提示仍提及 | 引擎在**裁剪前**运行；裁剪时反向加权命中行 |
| 跨源提示引入复杂度 | `trace_preprocessor` 越界访问 logcat | 双源匹配器单独放 `cross_source.py`，靠 `ai_context` 注入而不在 `trace_preprocessor` 内部直接读 logcat |
| 降低不带提示时的兼容性 | 老调用方期待原字典结构 | 新字段是**追加**式，不删除/重命名旧字段；对外 API 不变 |
| AI 厂商差异 | 部分模型不擅长解析 `▸ HINT` 行内标注 | 头部已有 `Hints:` 摘要，模型即使忽略行内标记也能拿到全局视图；ESS 元数据兜底 |

---

<a id="8-立刻可以做的-quick-win不属于上面-phase但价值高"></a>
## 8. 立刻可以做的快速改进（不属于上面阶段，但价值高）

1. **马上把现有的 `binderSummary` / `renderSummary` / `suspendSummary` / `cpuSummary` 渲进 `cache.md`**——这些已经计算出来但 AI 看不到，**零成本**。
2. **把 `suspiciousThreads` 的 `nativeTopFrame/javaTopFrame/blockHint` 列出来**——也是已有数据，告诉 AI "我已经帮你筛过了，这几个嫌疑最大"。
3. **`mainThread.lockOwnerTid` + 自动找持锁者块**——在提示词里以 `Owner thread of main`（主线程等待的持锁线程）小节呈现，等于给 AI 喂一份"主线程→持锁者→…"的小调用图。

这三个动作可以在阶段 1 之前 1~2 小时内完成，先把"AI 能看到 Python 已算出的东西"这一基础问题解掉。

---

## 9. 参考

- `anr_evidence/trace_preprocessor.py`（trace 解析 + 死锁/NativePollOnce/MAIN_* 提示生成）
- `anr_evidence/anrmanager_parser.py`（AnrManager 块结构化解析 + `system` 级派生提示）
- `anr_evidence/pattern_catalog.py`（数据驱动的主线程模式表 + 引擎）
- `anr_evidence/cross_source_fusion.py`（logcat/内核旁证 → 置信度升级）
- `anr_evidence/eval.py`（真值评测框架）
- `anr_evidence/ai_context.py`（提示词渲染、JSON 尾部契约、行级提示注入）
- `anr_evidence/anr_strategy.py`（按 ANR 类型的过滤策略）
- `tests/fixtures/eval/*.json`（13 个真值用例）
- `scripts/run_eval.py`（CLI：运行评测并打印 P/R/F1 表）
- `wiki/ANR-trace文件分析.md`（线程状态对照表）
- `wiki/实例/ANR-死锁.md`、`wiki/MTK/swt/17.Deadlock.md`（死锁）
- `wiki/MTK/swt/18.Binder Stuck.md`（binder 卡死）
- `wiki/DouYin/3.ANR 优化实践系列 - 实例剖析集锦.md`（NativePollOnce、IO、跨进程死锁）
- `wiki/DouYin/4.ANR 优化实践系列 - Barrier 导致主线程假死.md`
- `wiki/DouYin/5.ANR 优化实践系列 - 告别 SharedPreference 等待.md`
- `wiki/实例/ANR-Waiting for Available buffer.md`、`wiki/实例/ANR-Sync group timeout，failed to waitNextVsync.md`
- `wiki/MTK/swt/14.CPU.md` / `15.Low Memory.md` / `16.IO Check.md` / `21.SurfaceFlinger卡住.md` / `22.Dump时间过长.md` / `25.StorageManagerService卡住.md`

---

<a id="10-实施映射表hint-id--代码--eval-fixture"></a>
## 10. 实施映射表（提示 ID → 代码 + 评测测试夹具）

> 让"文档里的模式" 与"代码里的实现"形成可追踪的双向映射。任何新增提示都应同步更新本表 + 至少一个评测测试夹具。

### 10.1 死锁 / 锁竞争（trace_preprocessor.py）

| 提示 ID | 类别（`category`） | 置信度（`confidence`） | 实现函数 | 评测测试夹具 |
|---------|----------|-----------|---------|-------------|
| `DEADLOCK_CYCLE` | main_block | strong（经融合升为 critical） | `_emit_deadlock_hints` | `eval_deadlock_2thread.json` |
| `DEADLOCK_LIKELY` | main_block | weak | `_emit_deadlock_hints` | （文档预留，模式未触发） |
| `DEADLOCK_SELF` | main_block | weak | `_emit_deadlock_hints` | `eval_deadlock_self.json` |
| `LOCK_OWNER_BLOCKED` | main_block | strong | `_emit_deadlock_hints` | `eval_lock_owner_blocked_chain.json` |
| `LOCK_OWNER_SLEEPING` | main_block | strong | `_emit_deadlock_hints` | `eval_lock_owner_sleeping.json` |
| `LOCK_CONTENTION_BLOCKED` | main_block | strong | `_emit_deadlock_hints` | （文档预留） |
| `CROSS_PROCESS_DEADLOCK_SUSPECTED` | binder | weak | `_emit_deadlock_hints` | （文档预留：需 binder_wait_reply + 本地死锁组合） |
| `DEADLOCK_CYCLE_CONFIRMED` | main_block | critical | `consolidate_deadlock_across_traces` | （需 ≥2 份 trace 转储） |

### 10.2 NativePollOnce 真假判定（trace_preprocessor.py）

| 提示 ID | 类别（`category`） | 置信度（`confidence`） | 实现函数 | 评测测试夹具 |
|---------|----------|-----------|---------|-------------|
| `NATIVE_POLL_BUT_BUSY` | main_block | strong | `_emit_native_poll_hints` | `eval_native_poll_busy.json` |
| `NATIVE_POLL_IDLE_LIKELY` | main_block | weak | `_emit_native_poll_hints` | `eval_native_poll_idle.json` |
| `NATIVE_POLL_AMBIGUOUS` | main_block | weak | `_emit_native_poll_hints` | （边界覆盖在单元测试中） |

<a id="103-主线程模式-hintpattern_catalogmain_thread_pattern_catalog"></a>
### 10.3 主线程模式提示（pattern_catalog.MAIN_THREAD_PATTERN_CATALOG）

| 提示 ID | 类别（`category`） | 置信度（`confidence`） | 触发栈帧（部分） | 评测测试夹具 |
|---------|----------|-----------|-----------------|-------------|
| `MAIN_BINDER_WAIT_REPLY` | binder | strong | `IPCThreadState::waitForResponse`、`BinderProxy.transact` | `eval_main_binder_wait.json` + `eval_fusion_binder_critical.json` |
| `MAIN_SP_APPLY_WAIT` | sp | strong | `QueuedWork.waitToFinish`、`SharedPreferencesImpl$EditorImpl.commit` | `eval_main_sp_apply.json` |
| `MAIN_IO_BLOCKED` | io | strong | `FileInputStream.read`、`libc.so (read+...)` | （单元测试） |
| `MAIN_DB_BLOCKED` | io | strong | `SQLiteConnection.executeForLong`、`androidx.room.RoomDatabase` | `eval_main_db_blocked.json` |
| `MAIN_GC_PAUSED` | gc | strong | `art::gc::Heap::WaitForGcToComplete`、`Runtime.gc` | （单元测试） |
| `MAIN_RENDER_WAIT_FENCE` | render | strong | `HardwareRenderer.nativeSyncAndDrawFrame`、`waitForFences` | `eval_main_render_fence.json` |
| `MAIN_NETWORK_BLOCKED` | io | strong | `okhttp3.RealCall.execute`、`SocketInputStream.read` | （单元测试） |
| `MAIN_PROVIDER_QUERY` | binder | strong | `ContentResolver.query/insert/update` | （单元测试） |
| `MAIN_WEBVIEW_LOAD` | render | strong | `WebView.loadUrl`、`org.chromium.android_webview` | （单元测试） |
| `MAIN_DEX_LOADING` | io | strong | `dalvik.system.DexPathList`、`ClassLoader.loadClass` | （单元测试待补） |

<a id="104-anrmanager-派生-hintanrmanager_parserpy"></a>
### 10.4 AnrManager 派生提示（anrmanager_parser.py）

| 提示 ID | 类别（`category`） | 置信度（`confidence`） | 触发条件 | 评测测试夹具 |
|---------|----------|-----------|---------|-------------|
| `ANR_REASON_CLASSIFIED` | system | strong | `dumpAnrDebugInfo end: AnrDumpRecord{ <reason> ProcessRecord{...} }` 解析成功 | `eval_anrmanager_cpu_saturated.json` 等 |
| `SYSTEM_CPU_SATURATED` | system | strong | `cpuTotal.totalPct >= 90` | `eval_anrmanager_cpu_saturated.json` |
| `SYSTEM_IO_PRESSURE` | system | strong | `cpuTotal.iowaitPct >= 20` | `eval_anrmanager_io_pressure.json` |
| `SYSTEM_MEMORY_PRESSURE` | system | strong | PSI `memory.some.avg10 >= 20` | （单元测试） |

### 10.5 跨源融合规则（cross_source_fusion._CORROBORATION_RULES）

| 被升级提示 | 触发源 | 关键正则（部分） | 升级后置信度（`confidence`） |
|-------------|-------|-----------------|------------------|
| `MAIN_BINDER_WAIT_REPLY` | logcat | `slow binder transaction`、`Watchdog.*system_server` | strong → critical |
| `MAIN_GC_PAUSED` | logcat | `Background concurrent copying GC freed` | strong → critical |
| `MAIN_SP_APPLY_WAIT` | logcat | `Slow operation.*sp\.commit` | strong → critical |
| `MAIN_RENDER_WAIT_FENCE` | logcat | `Choreographer.*Skipped \d+ frames`、`fence timeout` | strong → critical |
| `NATIVE_POLL_BUT_BUSY` | logcat | `Choreographer.*Skipped`、`Slow Looper message` | strong → critical |
| `MAIN_DB_BLOCKED` | logcat | `Slow SQL query`、`SQLiteDatabase.*Slow operation` | strong → critical |
| `MAIN_NETWORK_BLOCKED` | logcat | `SocketTimeoutException`、`BlockGuard.*network` | strong → critical |
| `SYSTEM_IO_PRESSURE` | kernel_log | `hung_task`、`jbd2.*blocked`、`f2fs.*timeout` | strong → critical |
| `SYSTEM_MEMORY_PRESSURE` | logcat / kernel | `lowmemorykiller.*Killing`、`oom-killer:` | strong → critical |
| `DEADLOCK_CYCLE` | logcat | `Watchdog`、`system_server.*WAITED` | strong → critical |

<a id="106-新增-hint-的-pr-checklist"></a>
### 10.6 新增提示的 PR 检查清单

按以下顺序操作可让 CI 自动验收：

1. 在 `pattern_catalog.MAIN_THREAD_PATTERN_CATALOG` 追加一条字典（或在 `_emit_*_hints` 增加生成器，仅当需要非栈帧的复杂逻辑时）；
2. 在 `tests/fixtures/eval/` 新增至少一个 `eval_<hint>.json`（覆盖正例 + 至少一个 `forbidden` 反例）；
3. 在 `tests/test_eval_groundtruth.GroundTruthEvalTests.test_each_hint_id_has_at_least_one_case` 的 `documented_required_ids` 集合中补上新 ID；
4. 若该提示有 logcat / 内核旁证规律，在 `cross_source_fusion._CORROBORATION_RULES` 增加条目；
5. 若该提示是 AI 必须能引用的根因候选，在 `ai_context._render_ai_prompt` 的速查表中加 1 行说明；
6. `python3 scripts/run_eval.py` 须保持 `pass_rate=1.0` 且不引入新的假阳性 `fp`。
