---
name: anr-eventlog-analysis
description: ANR EventLog 专项分析 skill。Use when analyzing filtered EventLog evidence around am_anr, including pre-ANR lifecycle/window/input/process tags, delta-T sequencing, ANR anchor validation, and event-only gaps.
---

# ANR EventLog 分析 Skill

目标：只基于 EventLog 形成 ANR 前窗口的时间线和状态机解释。不要用 Trace/Logcat 直接下最终根因，只标记需要跨源验证的位置。

## 输入

- `### Anchor` 中的 ANR anchor。
- `### EventLog` 过滤后的 event lines。
- ANR type strategy 与 event window。

## 固定步骤

1. 找 `am_anr`：timestamp、pid/process、reason、是否与 Anchor 一致。
2. 按时间顺序列出 ANR 前窗口关键 tag，计算相对 `am_anr` 的 ΔT。
3. 给每条关键事件分类：进程、Activity 生命周期、窗口/焦点、输入、广播/服务/provider、内存/进程死亡、电源/系统状态。
4. 对 No Focus：检查 resume、relayout、draw/focus 相关 tag 是否能解释窗口未建立。
5. 对 Input timeout：检查 input_focus、Slow dispatch/delivery、前序生命周期/窗口变化是否支持输入未完成。
6. 标记 next app、system_server 或其它进程事件与目标 ANR 的关系；不要要求每条上下文都包含目标包名。
7. 输出 EventLog-only 结论：它支持的 ANR 类型/时间线、不能证明的阻塞点、需要 Trace/Logcat 补证。

## 输出格式

```markdown
#### AI Analysis — EventLog
- Anchor / am_anr: ...
- Pre-ANR sequence: ...
- State-machine interpretation: ...
- EventLog-only conclusion: ...
- Evidence gaps: ...
- Confidence: high|medium|low
```

## 保守规则

- EventLog 通常能证明“何时/何种类型触发”，不能单独证明 main thread 堆栈根因。
- 上下文事件不是目标包也可能重要，但必须说明关联路径。
- 缺少关键生命周期/窗口 tag 时，明确写缺口，不要补剧情。
