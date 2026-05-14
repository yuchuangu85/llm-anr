# EventLog Filtering Configuration Reference

This document consolidates all critical tags used for filtering EventLog traces. This master list is used by the `anr_log_pattern_filter.py` script to identify relevant logs within the 12s ANR window.

## 1. Overview
To identify relevant system events leading up to an ANR, the filter tracks specific tags related to ActivityManager (AM), WindowManager (WM), Power management, and Input Dispatcher.

## 2. Extracted Tags by Source

### 2.1 ActivityManager (AM) Tags
*Extracted from: `EventLog含义.md`, `am.md`*

**Core ANR & Process Lifecycle:**
- `am_anr`: The trigger event for ANR analysis.
- `am_proc_start`: Process start/creation.
- `am_proc_bound`: Process attached/bound to ActivityManager.
- `am_proc_died`: Process death events.
- `am_proc_bad`: Process marked as bad.
- `am_proc_good`: Process marked as good.
- `am_kill`: Process killing (OOM/reason).

**Memory, UID, and freeze state:**
- `am_pre_boot`: Pre-boot process information.
- `am_meminfo`: Memory usage snapshots.
- `am_mem_factor`: System memory pressure factor changes.
- `am_pss`: Process set size (PSS) updates.
- `am_uid_running`: UID enters running state.
- `am_uid_active`: UID enters active state.
- `am_uid_idle`: UID enters idle state.
- `am_uid_stopped`: UID enters stopped state.
- `am_freeze`: Process freeze event.
- `am_unfreeze`: Process unfreeze event.

**System & user session events:**
- `ssm_user_starting`: User switching / starting.
- `ssm_user_switching`: User switching.
- `ssm_user_unlocking`: User unlocking.

### 2.2 WindowManager (WM) Tags
*Extracted from: `vm.md`*

**Task & Activity Window Management:**
- `wm_task_created`: Task created.
- `wm_task_to_front`: Task moving to foreground.
- `wm_task_moved`: Task position changes.
- `wm_create_task`: Task creation.
- `wm_create_activity`: ActivityRecord created.
- `wm_restart_activity`: Activity restart requested.
- `wm_resume_activity`: Activity resume requested.
- `wm_pause_activity`: Activity pause requested.
- `wm_remove_task`: Task removal.
- `wm_finish_activity`: Activity finishing (WM side).
- `wm_destroy_activity`: Activity destruction (WM side).
- `wm_new_intent`: New intent delivery to activity.
- `wm_activity_launch_time`: Activity launch latency metrics.
- `wm_failed_to_pause`: Failure to pause activity.
- `wm_add_to_stopping`: Activity added to stopping list / made invisible.
- `wm_set_resumed_activity`: Activity state transition.
- `wm_on_resume_called`: App-side onResume callback observed.
- `wm_on_paused_called`: App-side onPause callback observed.
- `wm_on_stop_called`: App-side onStop callback observed.
- `wm_on_top_resumed_gained_called`: App-side top-resumed gained callback observed.
- `wm_on_top_resumed_lost_called`: App-side top-resumed lost callback observed.
- `wm_focused_root_task`: Focus changes.
- `wm_focus`: Focused window change.
- `wm_stop_activity`: Activity stopping.
- `wm_wallpaper_surface`: Wallpaper surface visibility/state changes.

### 2.3 Power & Battery Tags
*Extracted from: `EventLog含义.md`*

**Power Management:**
- `battery_level`: Voltage/Temperature/Level changes.
- `battery_status`: Charging/Health status.
- `battery_discharge`: Discharge rate/duration.
- `power_sleep_continuous`: Sleep request/wake lock status.
- `power_screen_broadcast_send`: Screen state transitions.
- `power_screen_state`: Screen on/off status.
- `power_partial_wake_state`: Wake lock acquisition/release.

### 2.4 Dispatcher (Input Flinger) Tags
*Extracted from: `dispatcher.md`*

**Input Interaction Events:**
- `input_interaction`: Window-based interaction.
- `input_focus`: Window focus change.
- `input_cancel`: Input cancellation.

## 3. Usage in Filtering
When running the filter:
1.  The script loads all tags listed above into a `HashSet`.
2.  For every log line in the 12s window, the script performs an $O(1)$ containment check: `if any(tag in line for tag in TagSet)`.
3.  This ensures only high-signal diagnostic events are presented in the final report.
