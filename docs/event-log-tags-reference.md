# EventLog 过滤配置参考

本文档汇总了用于过滤 EventLog trace 的所有关键标签。该主列表由 `anr_log_pattern_filter.py` 脚本使用，用于在 12 秒 ANR 窗口内识别相关日志。

## 1. 概述
为识别 ANR 发生前的相关系统事件，过滤器会追踪与 ActivityManager (AM)、WindowManager (WM)、电源管理 (Power) 以及 Input Dispatcher 相关的特定标签。

## 2. 按来源分类的抽取标签

### 2.1 ActivityManager (AM) 标签
*抽取自：`EventLog含义.md`、`am.md`*

**核心 ANR 与进程生命周期：**
- `am_anr`: ANR 分析的触发事件。
- `am_proc_start`: 进程启动/创建。
- `am_proc_bound`: 进程已绑定/挂接到 ActivityManager。
- `am_proc_died`: 进程死亡事件。
- `am_proc_bad`: 进程被标记为 bad。
- `am_proc_good`: 进程被标记为 good。
- `am_kill`: 进程被杀（OOM/原因）。

**内存、UID 与冻结状态：**
- `am_pre_boot`: 预启动（pre-boot）进程信息。
- `am_meminfo`: 内存使用快照。
- `am_mem_factor`: 系统内存压力因子变化。
- `am_pss`: 进程占用内存（PSS）更新。
- `am_uid_running`: UID 进入 running 状态。
- `am_uid_active`: UID 进入 active 状态。
- `am_uid_idle`: UID 进入 idle 状态。
- `am_uid_stopped`: UID 进入 stopped 状态。
- `am_freeze`: 进程冻结事件。
- `am_unfreeze`: 进程解冻事件。

**系统与用户会话事件：**
- `ssm_user_starting`: 用户切换/启动。
- `ssm_user_switching`: 用户切换。
- `ssm_user_unlocking`: 用户解锁。

### 2.2 WindowManager (WM) 标签
*抽取自：`vm.md`*

**任务与 Activity 窗口管理：**
- `wm_task_created`: Task 创建。
- `wm_task_to_front`: Task 移至前台。
- `wm_task_moved`: Task 位置变化。
- `wm_create_task`: Task 创建。
- `wm_create_activity`: ActivityRecord 创建。
- `wm_restart_activity`: 请求重启 Activity。
- `wm_resume_activity`: 请求 resume Activity。
- `wm_pause_activity`: 请求 pause Activity。
- `wm_remove_task`: Task 移除。
- `wm_finish_activity`: Activity 结束（WM 侧）。
- `wm_destroy_activity`: Activity 销毁（WM 侧）。
- `wm_new_intent`: 向 activity 投递新 intent。
- `wm_activity_launch_time`: Activity 启动耗时指标。
- `wm_failed_to_pause`: Activity pause 失败。
- `wm_add_to_stopping`: Activity 加入 stopping 列表/被置为不可见。
- `wm_set_resumed_activity`: Activity 状态转换。
- `wm_on_resume_called`: 观察到 App 侧 onResume 回调。
- `wm_on_paused_called`: 观察到 App 侧 onPause 回调。
- `wm_on_stop_called`: 观察到 App 侧 onStop 回调。
- `wm_on_top_resumed_gained_called`: 观察到 App 侧 top-resumed gained 回调。
- `wm_on_top_resumed_lost_called`: 观察到 App 侧 top-resumed lost 回调。
- `wm_focused_root_task`: 焦点变化。
- `wm_focus`: 焦点窗口变化。
- `wm_stop_activity`: Activity 停止。
- `wm_wallpaper_surface`: 壁纸 surface 可见性/状态变化。

### 2.3 电源与电池标签
*抽取自：`EventLog含义.md`*

**电源管理：**
- `battery_level`: 电压/温度/电量变化。
- `battery_status`: 充电/健康状态。
- `battery_discharge`: 放电速率/时长。
- `power_sleep_continuous`: 休眠请求/wake lock 状态。
- `power_screen_broadcast_send`: 屏幕状态转换。
- `power_screen_state`: 屏幕开/关状态。
- `power_partial_wake_state`: wake lock 获取/释放。

### 2.4 Dispatcher (Input Flinger) 标签
*抽取自：`dispatcher.md`*

**输入交互事件：**
- `input_interaction`: 基于窗口的交互。
- `input_focus`: 窗口焦点变化。
- `input_cancel`: 输入取消。

## 3. 在过滤中的用法
运行过滤器时：
1.  脚本将上述所有标签加载进一个 `HashSet`。
2.  对 12 秒窗口内的每一行日志，脚本执行 $O(1)$ 的包含检查：`if any(tag in line for tag in TagSet)`。
3.  这样可确保最终报告中只呈现高信号的诊断事件。
