# Logcat 过滤配置参考

本文沿用 [EventLog 过滤配置参考](event-log-tags-reference.md) 的 AM、WM、电源和输入分类，整理 ANR 分析需要保留的 Logcat 信息，并区分现有程序规则与人工补充排查项。

## 1. 适用范围与来源区别

- `event-log-tags-reference.md` 中的 `am_*`、`wm_*`、`input_*` 等是 EventLog 标签；通过 `logcat -b events` 导出的文本仍属于 EventLog，不应误当成普通 Logcat tag。
- EventLog 主要提供结构化事件锚点；Logcat 提供触发原因、组件诊断、转储流程、负载和完成/恢复线索。两者通过时间、包名、PID/TID、窗口/连接、组件等身份交叉核验，不是一对一标签替换。
- 本文是维护说明，不是新的可执行配置。当前 Logcat 默认值来自 [log_filter.py](../anr_evidence/log_filter.py)，类型增量来自 [anr_strategy.py](../anr_evidence/anr_strategy.py)。程序不会自动加载本文的标签或建议关键词。
- 下文“补充定位项”只用于读取已采集的原始日志、增量分析或确定后续采集范围，不表示现有过滤器一定保留了它们。标签名称、日志开关及可用字段须以实际设备日志为准。

## 2. 按 EventLog 分类对应的 Logcat 信息

### 2.1 ActivityManager：ANR、进程、内存与用户会话

**EventLog 对照：** `am_anr`、`am_proc_start`、`am_proc_bound`、`am_proc_died`、`am_proc_bad`、`am_proc_good`、`am_kill`。

Logcat 重点读取 `ActivityManager`、`AnrManager`、`AndroidRuntime` 等实际出现的相关标签，以及 `ANR in`、`Reason:`、`not responding`、进程启动/退出/被杀原文。

必须保留的信息：

- ANR 原因全文、目标包名/进程、PID、组件身份及触发日志时间。
- 进程创建、绑定、退出、杀进程、重启的时间与原因，避免把重启后的 PID 接到原事故上。
- `startAnrDump`、`dumpAnrDebugInfo begin/end`、`addErrorToDropBox`、`tracesFile` / `mTracesFile`、`controller = ...` 等转储生命周期。
- 明确的恢复、取消、进程处置等原文；没有恢复日志时保持未知，不能由界面重新响应反推整个链路已经清理。

**内存/UID/冻结对照：** `am_pre_boot`、`am_meminfo`、`am_mem_factor`、`am_pss`、`am_uid_running/active/idle/stopped`、`am_freeze`、`am_unfreeze`。

补充定位项包括目标进程的 GC/内存压力、冻结/解冻、UID 状态与进程处置；这些词不是当前普通 Logcat 窗口的完整默认模式。优先结合 AnrManager 的 `Load`、CPU 统计窗口、`TOTAL`、`iowait`、CPU/IO/内存 PSI 和高负载进程列表，再对照 Meminfo、EventLog 和 Trace。单次高负载或缺少 GC 日志不能证明内存泄漏或排除内存问题。

**用户会话对照：** `ssm_user_starting`、`ssm_user_switching`、`ssm_user_unlocking`。

关注实际日志中的用户切换、解锁与目标进程/窗口生命周期。当前没有针对这三个标签的独立 Logcat 默认过滤项；应保留 EventLog 原文及同时间段原始上下文，不臆造普通 Logcat tag 映射。

### 2.2 WindowManager：任务、Activity、焦点与图形表面

**EventLog 对照：** `wm_task_created`、`wm_task_to_front`、`wm_task_moved`、`wm_create_task`、`wm_create_activity`、`wm_restart_activity`、`wm_resume_activity`、`wm_pause_activity`、`wm_remove_task`、`wm_finish_activity`、`wm_destroy_activity`、`wm_new_intent`。

同时关注 `wm_activity_launch_time`、`wm_failed_to_pause`、`wm_add_to_stopping`、`wm_set_resumed_activity`、`wm_on_resume_called`、`wm_on_paused_called`、`wm_on_stop_called`、`wm_on_top_resumed_gained_called`、`wm_on_top_resumed_lost_called`、`wm_focused_root_task`、`wm_focus`、`wm_stop_activity`、`wm_wallpaper_surface`。

Logcat 的对应排查范围为 `WindowManager`、`SurfaceFlinger`、`Choreographer` 以及设备实际记录的窗口/Activity/转场组件。保留：

- 目标 Activity/任务、display、窗口 token、焦点应用和实际输入焦点窗口身份。
- 窗口创建、attach/relayout、可见性/可获焦状态、绘制完成、焦点请求、原生焦点变化及窗口死亡。
- `finishDrawing`、`reportDrawFinished`、`NOT_VISIBLE`、`NOT_FOCUSABLE`、`Focus request`、`native newFocus` 等补充定位项的原文和时间。
- 图形表面显示/隐藏、帧延迟、渲染或 fence 等线索；这些必须用同窗口/图层、线程和时间关联，不能仅凭标签共现归因。

窗口绘制完成不等于获得焦点，焦点请求不等于原生焦点已经授予，Activity resume 也不等于窗口已经可接收输入。这里的补充定位项没有全部配置为普通窗口的默认关键词。

### 2.3 电源与电池

**EventLog 对照：** `battery_level`、`battery_status`、`battery_discharge`、`power_sleep_continuous`、`power_screen_broadcast_send`、`power_screen_state`、`power_partial_wake_state`。

需要核验屏幕开关、休眠/唤醒、唤醒锁获取/释放、充电和电量变化是否与本次 ANR 的窗口或进程状态相关，并记录时间、UID/进程、锁身份和原始原因。

这些事件不能直接转换成一组固定的普通 Logcat tag。当前 Logcat 默认模式没有专门的电源/电池关键词集合；先使用对应 EventLog、已保留的原始上下文及实际采集到的电源日志。只有时间相近不能证明电源状态导致 ANR。

### 2.4 输入分发

**EventLog 对照：** `input_interaction`、`input_focus`、`input_cancel`。

Logcat 主要关注 `InputDispatcher`、`WindowManager`、`AnrManager`。按证据区分两条路径：

- 无焦点窗口等待：`does not have a focused window`、`no focused window`、`no focus window`、`Waiting because no window has focus` 等信号，记录焦点应用、实际窗口、等待起点、有效阈值、到期复查及 reset/获焦情况。
- 已发布输入等待完成：`Input dispatching timed out`、`is not responding. Waited ...ms for ...`、`has not finished processing` 等信号，记录 reason 中的 KeyEvent/MotionEvent、action/keyCode、native seq、输入 eventId、device/source/display、连接/窗口、投递与截止时间、finish/FINISHED 和队列清理情况。

逐项区分 reason 描述的事件与真正最早到期条目。Java finish 返回、`handled=false`、单个 FINISHED 都不能证明连接恢复；未观察到的字段用 `null`，不能由 `am_anr - 5s` 补造起点。`FINISHED`、`waitQueue`、`native newFocus` 等本身并非覆盖所有入口的默认匹配关键词，需要核查原始上下文是否实际保留。

## 3. 当前已启用的普通 Logcat 过滤项

### 3.1 通用关键词

`LOGCAT_SIGNAL_PATTERNS` 当前为以下集合。匹配方式是对整行做不区分大小写的子串检查，不是严格 tag 匹配，也不是正则表达式：

```json
{
  "baselinePatterns": [
    "anr", "am_anr", "anrmanager", "inputdispatcher",
    "input dispatching timed out", "focused window", "activitymanager",
    "windowmanager", "broadcastqueue", "contentprovider", "system_server",
    "slow operation", "slow binder transaction", "timeout", "not responding"
  ],
  "systemContextTags": [
    "ActivityManager", "AndroidRuntime", "AnrManager", "BinderProxy",
    "Choreographer", "InputDispatcher", "SurfaceFlinger", "WindowManager"
  ],
  "aiContextWindowsSeconds": {
    "input_dispatching_timeout": {"before": 15, "after": 15},
    "no_focus_window": {"before": 15, "after": 15},
    "broadcast_timeout": {"before": 90, "after": 30},
    "service_timeout": {"before": 240, "after": 30},
    "content_provider_timeout": {"before": 60, "after": 30},
    "job_scheduler_timeout": {"before": 120, "after": 30},
    "system_watchdog_swt": {"before": 300, "after": 60},
    "unknown": {"before": 30, "after": 15}
  }
}
```

这些窗口是证据筛选范围，不是系统 ANR 超时阈值。EventLog 参考文档中的 12 秒前窗口也不能直接套为所有类型的 Logcat 窗口。

### 3.2 AI 上下文的类型策略

`scripts/anr_to_ai.py` 使用类型策略，和独立基础 Logcat 过滤器并不完全相同：

- `input_dispatching_timeout` / `no_focus_window` 使用独立的 `INPUT_LOGCAT_PATTERNS`，不是通用集合的简单超集。包含 `input dispatching timeout`、`no focused window`、`no focus window`、`waiting because the touched window has not finished processing` 等输入专项信号；没有继承通用集合中的全部普通性能模式。
- `broadcast_timeout`：通用集合加 `broadcastqueue`、`broadcast of intent`、`onreceive`、`goasync`、`finishreceiver`。
- `service_timeout`：通用集合加 `timeout executing service`、`executing service`、`foreground service`、`fgs`、`service timeout`。
- `content_provider_timeout`：通用集合加 `contentprovider`、`content provider`、`provider not responding`、`publishing content providers`。
- `job_scheduler_timeout`：通用集合加 `jobscheduler`、`jobservice`、`onstartjob`、`onstopjob`、`jobservicecontext`。
- `system_watchdog_swt`：通用集合加 `watchdog`、`swt`、`system_server`、`blocked in handler`、`monitor checker`。
- `unknown`：使用通用集合；不能因为其中有输入相关词就将事故判成输入超时。

匹配模式仅决定候选日志保留，不决定根因或事件执行成功。例如 `onReceive` 被提及不证明回调已开始或完成，`jobFinished` 也不等于 `onStartJob/onStopJob` 按时返回。

### 3.3 包名与系统上下文

AI 上下文的普通 Logcat 时间窗使用 `system_or_package`：一行既需要满足模式匹配，又需要包含目标包名或被识别为上面列出的系统上下文 tag。

- 系统上下文 tag 是包名筛选的例外，不是“无条件保留该 tag”的白名单。
- 只有 `Choreographer` 或 `SurfaceFlinger` tag、正文没有任何策略关键词的行，仍可能不进入普通过滤窗口。
- 当前系统 tag 识别针对空格分隔的 `级别 tag:` 等形态，不能声称支持所有 brief/vendor 格式。AnrManager 完整块有独立的标签识别路径。
- 当前包名条件按字符串包含检查，不等价于 PID/UID 或完整包名身份校验。被保留的系统行、其他组件或相似包名仍须人工核对归属。
- 独立 `anr_logcat_filter.py --package ...` 使用基础过滤的逐行包名条件，不带 AI 上下文的全部系统上下文例外。

## 4. AnrManager 完整块与原始前置上下文

AI 上下文不是只输出第 3 节的关键词命中行。最终 Logcat 由三部分合并：

1. 所选 AnrManager 块起始时间之前的原始 12 秒日志；无法使用块起点时尝试块锚点。范围为 `[起点 - 12s, 起点)`，仅保留可解析时间戳的行，不额外做关键词或逐行包名过滤。
2. 与当前包/锚点匹配的完整 AnrManager 诊断块。提取允许其他 tag 的日志穿插，不要求物理相邻；匹配阶段默认以块诊断锚点距当前 ANR 不超过 60 秒为限，不表示 ANR 的有效 timeout 是 60 秒。
3. 当前 ANR 类型策略的普通 Logcat 时间窗。

合并按上述顺序保留原文首次出现，当前实现按完整行内容去重，不承诺整体重新按时间排序。因此输出中存在不符合普通关键词或不含目标包名的行是预期行为；不能为了减少噪声删掉基线证据、完整转储或原始前置上下文。

AnrManager 块尽量覆盖实际出现的 `startAnrDump` / `dumpAnrDebugInfo begin`、堆栈采样、`ANR in`、`Reason`、负载/PSI/CPU 统计、`dumpAnrDebugInfo end`、`addErrorToDropBox` 和 `controller = ...`。日志没有记录的节点不补造；`ANR in`、转储起止和真实触发时刻分别说明。

## 5. 过滤后的输出信息

每个 ANR 保持独立的 `anr_ai_context/<group-id>/logcat.txt`，不将多组事故拼接为一条因果链。读取日志后，在现有 `#### AI Analysis — Logcat / AnrManager` 槽位写明：

1. 触发原因原文、目标身份、锚点与来源；区分普通正文提及 ANR 与真正的系统报告。
2. 关键时间线：组件/窗口/输入变化、实际触发、转储、完成/恢复或进程处置，附原始证据定位。
3. AnrManager 的 Load、CPU 时间窗口、TOTAL/iowait、PSI、目标包及外部高负载进程。
4. Meminfo/GC/LMK/OOM、锁/Binder/图形或调度的交叉证据；缺失则列出缺口，不能据此排除。
5. 证据质量：时间戳缺失、窗口截断、日志级别未开启、来源缺失、包名/PID不确定、同一窗口中的其他事故或时钟域问题。

全部类型的最终槽位继续给出“ANR 发生模型”和“模拟操作步骤”，模型区分 `observed` / `inferred` / `unknown`；input 另写“超时输入事件”。步骤保留前置条件、待补参数、预期观察和采集核验，标记 `executed=false`，不保证复现。

## 6. 使用方式与降级边界

生成完整 AI 工作区（包含上述三路合并）：

```bash
python3 scripts/anr_to_ai.py tests/fixtures/idt_01.json --out-dir /tmp/anr-logcat-context
```

对已有 Logcat/包输入只做基础时间窗过滤，默认锚点前后各 15 秒；指定 `--anchor` 才会使用该时间窗：

```bash
python3 scripts/anr_logcat_filter.py tests/fixtures/idt_01.json --anchor "04-12 11:00:03.000"
```

单独抽取匹配的 AnrManager 块，不包含普通时间窗和原始前置 12 秒：

```bash
python3 scripts/anr_logcat_filter.py path/to/logcat.txt --anchor "04-12 11:00:03.000" --package com.demo --anrmanager
```

按 EventLog 参考文件过滤 `events`，不是普通 Logcat：

```bash
python3 scripts/anr_log_pattern_filter.py path/to/events.txt --tags docs/event-log-tags-reference.md --package com.demo
```

基础 Logcat 过滤器未给锚点时，降级保留来源开头至多 25 行并发出 `missing-anchor`，不是自动恢复完整 ANR 窗口。已有有效锚点但没有窗口匹配时保留空结果与 `empty-anchor-window`，不能从其他时间段填充。AI 上下文有独立的锚点发现逻辑；指定目标包却未找到对应 EventLog `am_anr` 时，不应拿其他包的记录替代。

## 7. 实现与验证入口

- [基础过滤与 AnrManager 块提取](../anr_evidence/log_filter.py)：模式、包名条件、时间窗、降级、块选择和原始前置上下文。
- [Logcat 独立过滤入口](../anr_evidence/sources/logcat/filter.py)：基础时间窗和独立块抽取。
- [AI 上下文构建](../anr_evidence/ai_context.py)：类型策略、分组身份、三路合并及 `logcat.txt`。
- [ANR 类型策略](../anr_evidence/anr_strategy.py)：类型关键词和证据窗口。
- [Logcat 专项分析技能](../skills/anr-logcat-analysis.md)与[统一分析指南](anr-ai-analysis-guide.md)：分析顺序、负载核验和保守结论。

集合中的标签数量不改变匹配复杂度的事实：当前子串匹配需遍历模式并检查行内容，不能因为模式保存在集合中就声称整行过滤是 `O(1)`。本文不更改现有算法或对大型日志的内存行为作额外承诺。
