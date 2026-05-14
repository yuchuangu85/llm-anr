"""Tests for MAIN_* main-thread pattern hints."""

from __future__ import annotations

import unittest

from anr_evidence import preprocess_trace_content


def _hint(result: dict, hint_id: str) -> dict | None:
    for hint in result.get("traceHints", []) or []:
        if hint["id"] == hint_id:
            return hint
    return None


def _trace_with_main_stack(stack_lines: list[str]) -> str:
    header = [
        "04-12 10:00:05.100 ----- pid 100 -----",
        "Cmd line: com.demo",
        '"main" prio=5 tid=1 Native',
        '  | sysTid=100 nice=0',
        '  | state=S schedstat=( 100000000 200000000 50 ) utm=10 stm=10 core=0 HZ=100',
    ]
    return "\n".join(header + ["  " + line for line in stack_lines])


class MainThreadPatternHintTests(unittest.TestCase):
    def test_binder_wait_reply_detected(self) -> None:
        trace = _trace_with_main_stack([
            "native: #00 pc 0  /system/lib/libbinder.so (android::IPCThreadState::waitForResponse+8)",
            "at android.os.BinderProxy.transactNative(Native method)",
            "at android.os.BinderProxy.transact(BinderProxy.java:550)",
        ])
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:05.100")
        hint = _hint(result, "MAIN_BINDER_WAIT_REPLY")
        self.assertIsNotNone(hint)
        self.assertEqual(hint["category"], "binder")
        self.assertEqual(hint["confidence"], "strong")

    def test_sp_apply_wait_detected_via_queuedwork(self) -> None:
        trace = _trace_with_main_stack([
            "at android.app.QueuedWork.waitToFinish(QueuedWork.java:170)",
            "at android.app.ActivityThread.handleStopActivity(ActivityThread.java:5102)",
        ])
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:05.100")
        self.assertIsNotNone(_hint(result, "MAIN_SP_APPLY_WAIT"))

    def test_sp_apply_wait_detected_via_editorimpl_commit(self) -> None:
        trace = _trace_with_main_stack([
            "at android.app.SharedPreferencesImpl$EditorImpl.commit(SharedPreferencesImpl.java:407)",
            "at com.demo.Settings.save(Settings.java:42)",
        ])
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:05.100")
        self.assertIsNotNone(_hint(result, "MAIN_SP_APPLY_WAIT"))

    def test_io_blocked_detected_for_fileinputstream(self) -> None:
        trace = _trace_with_main_stack([
            "at java.io.FileInputStream.read(FileInputStream.java:233)",
            "at com.demo.Loader.load(Loader.java:30)",
        ])
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:05.100")
        self.assertIsNotNone(_hint(result, "MAIN_IO_BLOCKED"))

    def test_db_blocked_detected_for_sqlite(self) -> None:
        trace = _trace_with_main_stack([
            "at android.database.sqlite.SQLiteConnection.executeForLong(SQLiteConnection.java:600)",
            "at android.database.sqlite.SQLiteDatabase.update(SQLiteDatabase.java:1234)",
        ])
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:05.100")
        self.assertIsNotNone(_hint(result, "MAIN_DB_BLOCKED"))

    def test_gc_paused_detected(self) -> None:
        trace = _trace_with_main_stack([
            "native: #00 pc 0  /apex/com.android.art/lib64/libart.so (art::gc::Heap::WaitForGcToComplete+0)",
            "at java.lang.Object.wait(Native method)",
        ])
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:05.100")
        self.assertIsNotNone(_hint(result, "MAIN_GC_PAUSED"))

    def test_render_wait_fence_detected(self) -> None:
        trace = _trace_with_main_stack([
            "at android.graphics.HardwareRenderer.nativeSyncAndDrawFrame(Native method)",
            "at android.view.ViewRootImpl.performDraw(ViewRootImpl.java:5050)",
        ])
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:05.100")
        self.assertIsNotNone(_hint(result, "MAIN_RENDER_WAIT_FENCE"))

    def test_network_blocked_detected_for_okhttp(self) -> None:
        trace = _trace_with_main_stack([
            "at okhttp3.RealCall.execute(RealCall.kt:64)",
            "at com.demo.Api.fetch(Api.java:55)",
        ])
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:05.100")
        self.assertIsNotNone(_hint(result, "MAIN_NETWORK_BLOCKED"))

    def test_no_main_pattern_when_main_in_idle_loop(self) -> None:
        trace = _trace_with_main_stack([
            "native: #00 pc 00012345  /system/lib64/libc.so (__epoll_pwait+8)",
            "at android.os.MessageQueue.nativePollOnce(Native method)",
        ])
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:05.100")
        for hid in ("MAIN_BINDER_WAIT_REPLY", "MAIN_SP_APPLY_WAIT", "MAIN_IO_BLOCKED", "MAIN_DB_BLOCKED",
                    "MAIN_GC_PAUSED", "MAIN_RENDER_WAIT_FENCE", "MAIN_NETWORK_BLOCKED"):
            self.assertIsNone(_hint(result, hid), f"unexpected {hid} in nativePollOnce trace")

    def test_multiple_patterns_can_coexist(self) -> None:
        # SP commit on top of an IO syscall — both should fire
        trace = _trace_with_main_stack([
            "native: #00 pc 0  /system/lib/libc.so (fsync+0)",
            "at java.io.FileOutputStream.write(FileOutputStream.java:401)",
            "at android.app.SharedPreferencesImpl$EditorImpl.commit(SharedPreferencesImpl.java:407)",
        ])
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:05.100")
        self.assertIsNotNone(_hint(result, "MAIN_SP_APPLY_WAIT"))
        self.assertIsNotNone(_hint(result, "MAIN_IO_BLOCKED"))


if __name__ == "__main__":
    unittest.main()
