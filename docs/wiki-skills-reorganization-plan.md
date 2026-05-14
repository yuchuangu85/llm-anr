# ANR Wiki → Skill 文件重组计划

> **实施日期**: 2026-05-13
> **状态**: 已完成

## 现状

wiki 目录下共有 ~70 个 .md 文件，分布在：

| 目录/文件 | 数量 | 内容 |
|-----------|------|------|
| 顶层 .md | 18 | ANR 基础、分类、流程、规范、trace 分析、时间对齐、版本对比等 |
| 机制/ | 6 | Service/Broadcast/ContentProvider/NoFocusWindow 触发机制 + Trace 生成 |
| 实例/ | 15 | 按 root cause 分类的实例：Input/Lock/Binder/CPU/内存/死锁等 |
| MTK/swt/ | 27 | MTK 平台 SWT/ANR 分析全流程（从 db 解析到各类问题定位） |
| DouYin/ | 5 | 抖音 ANR 优化实践（原理、监控、案例、Barrier、SP） |
| media/ | ~20 | 图片/流程图（非 .md，不纳入 skill） |

## 目标

将分散的 wiki 文件按**分析 ANR 时的实际使用场景**组织成 6 个 skill 文件，每个 skill 独立加载，覆盖 ANR 分析全链路。

---

## 6 个 Skill 设计

### Skill 1: anr-principle — ANR 原理与触发机制 (771 行)

**用途**: 回答 "ANR 是怎么产生的？每种类型的超时机制是什么？"

**纳入文件**:
- `ANR基础知识.md` — Linux 进程状态、trace 字段含义、线程状态
- `Android ANR 系列 1 ：理解 Android ANR 设计思想.md` — 设计思想
- `ANR原理代码分析.md` — 源码级分析
- `ANR详细对比13&10.md` — Android 10/13 机制差异
- `机制/` 全部 6 个文件:
  - `ANR-Service.md`
  - `ANR-Broadcast.md` + `ANR-Broadcast2.md`
  - `ANR-ContentProvider.md`
  - `ANR-HasNoFocusWindow.md`
  - `Trace产生过程.md`

### Skill 2: anr-classification — ANR 分类体系 (503 行)

**用途**: 回答 "这个 ANR 属于什么类型？对应什么超时？有哪些典型特征？"

**纳入文件**:
- `ANR-分类.md` — 场景分类 + 成因分类 + 常见模式
- `ANR关键字.md` — logcat/event log 关键字段速查
- `Find the unresponsive thread App quality.md` — 各 ANR 类型对应的无响应线程
- `Diagnose and fix ANRs App quality.md` — 官方分类与修复指南

### Skill 3: anr-analysis — ANR 分析流程 (688 行)

**用途**: 回答 "拿到一个 ANR 后，按什么步骤分析？每一步看什么？"

**纳入文件**:
- `ANR-分析流程.md` — 主流程图（mermaid flowcharts）
- `ANR-规范.md` — Log 规范、关键日志要素、CPU loading 解读、Check Flow
- `ANR分析.md` — 完整分析指南
- `Android ANR 系列 2 ：ANR 分析套路和关键 Log 介绍.md` — 分析套路
- `ANR时间问题.md` — ANR 时间对齐方法
- `MTK/swt/` 全部 27 个文件（归入"MTK 平台扩展流程"子章节）

### Skill 4: anr-root-cause — ANR 根因定位 (724 行)

**用途**: 回答 "主线程卡在哪了？是等锁、Binder 阻塞、Native 耗时还是死锁？"

**纳入文件**:
- `实例/` 全部 15 个文件:
  - `ANR-Input.md` + `ANR-Input dispatching.md` — Input 事件分发 ANR
  - `ANR-死锁.md` + `ANR-Locked.md` — 锁问题
  - `ANR-Binder.md` — Binder 阻塞
  - `ANR-主线程超时.md` + `ANR-应用超时.md` + `ANR-应用被杀.md`
  - `ANR-SurfaceSyncer.md` — Surface 同步问题
  - `ANR-Sync group timeout.md` — Vsync 超时
  - `ANR-Waiting for Available buffer.md` — Buffer 耗尽
  - `ANR-CPU.md` + `ANR-负载过高.md` + `ANR-内存.md` + `ANR-内存泄漏.md`
- `Android ANR 系列 3 ：ANR 案例分享.md` — 综合案例

### Skill 5: anr-load — ANR 负载与性能评估 (618 行)

**用途**: 回答 "系统负载是否异常？CPU/内存/IO 有没有瓶颈？"

**纳入文件**:
- `ANR-trace文件分析.md` — trace 字段深度解析
- `ANR-trace覆盖清单.md` — 当前工具覆盖范围
- `ANR监控.md` — WatchDog 实现
- `DouYin/` 全部 6 个文件（归入"业界实践"子章节）:
  - `1.ANR 优化实践系列 - 设计原理及影响因素.md`
  - `2.ANR 优化实践系列 - 监控工具与分析思路.md`
  - `3.ANR 优化实践系列 - 实例剖析集锦.md`
  - `4.ANR 优化实践系列 - Barrier 导致主线程假死.md`
  - `5.ANR 优化实践系列 - 告别 SharedPreference 等待.md`
  - `抖音 ANR 自动归因平台建设实践.md`

### Skill 6: anr-reference — ANR 速查手册 (175 行)

**用途**: 快速索引 + 外部参考链接汇总（当前 README.md 的升级版）

**纳入文件**:
- `README.md` — 外部链接索引（重组为分类速查）
- 各 skill 的交叉引用锚点

---

## Skill 使用场景映射

| 分析阶段 | 加载的 Skill |
|---------|-------------|
| 1. 确认 ANR 类型 | `anr-classification` |
| 2. 理解触发机制 | `anr-principle` |
| 3. 按流程分析 | `anr-analysis` |
| 4. 定位根因 | `anr-root-cause` |
| 5. 评估系统负载 | `anr-load` |
| 6. 查找外部参考 | `anr-reference` |

---

## 实施结果

6 个 skill 文件创建于 `skills/`，总计 3,479 行：

```
skills/
├── anr-principle.md       771 行
├── anr-root-cause.md      724 行
├── anr-analysis.md   688 行
├── anr-load.md            618 行
├── anr-classification.md  503 行
└── anr-reference.md       175 行
```

`CLAUDE.md` 已更新 ANR Analysis Workflow 章节，添加了 skill 加载指引。

---

## 不纳入 skill 的内容

- `media/` 目录（图片/流程图，作为资源被 skill 引用）
- `.DS_Store`（系统文件）
