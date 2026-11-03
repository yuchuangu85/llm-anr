---
name: anr-eventlog-analysis
description: ANR EventLog 专项分析技能。用于分析 am_anr 附近经过过滤的 EventLog 证据，涵盖 ANR 前的生命周期/窗口/输入/进程标签、相对时间差排序、ANR 锚点校验，以及仅凭事件日志无法填补的证据缺口。
---

# ANR EventLog 分析技能

目标：只基于 EventLog 形成 ANR 前窗口的时间线和状态机解释。不要用 Trace/Logcat 直接下最终根因，只标记需要跨源验证的位置。

## 输入

- `### Anchor` 中的 ANR 锚点。
- `### EventLog` 过滤后的事件日志行。
- ANR 类型策略与事件时间窗口。

## 固定步骤

1. 找 `am_anr`：时间戳、PID/进程、原因、是否与 Anchor 一致。
2. 按时间顺序列出 ANR 前窗口关键标签，计算相对 `am_anr` 的 ΔT。
3. 给每条关键事件分类：进程、Activity 生命周期、窗口/焦点、输入、广播/服务/provider、内存/进程死亡、电源/系统状态。
4. 对无焦点窗口：检查 resume、relayout、绘制/焦点相关标签是否能解释窗口未建立。
5. 对输入超时：检查 input_focus、Slow dispatch/delivery、前序生命周期/窗口变化是否支持输入未完成。
6. 标记下一个应用、system_server 或其它进程事件与目标 ANR 的关系；不要要求每条上下文都包含目标包名。
7. 输出仅基于 EventLog 的结论：它支持的 ANR 类型/时间线、不能证明的阻塞点、需要 Trace/Logcat 补证。

## 输出格式

```markdown
#### AI Analysis — EventLog
- 锚点 / am_anr：...
- ANR 前事件序列：...
- 状态机解释：...
- 仅基于 EventLog 的结论：...
- 证据缺口：...
- 置信度：高|中|低
```

## 保守规则

- EventLog 通常能证明“何时/何种类型触发”，不能单独证明主线程堆栈根因。
- 上下文事件不是目标包也可能重要，但必须说明关联路径。
- 缺少关键生命周期/窗口标签时，明确写缺口，不要补剧情。
