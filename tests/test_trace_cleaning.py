from __future__ import annotations

from pathlib import Path
import unittest

from anr_evidence import extract_evidence_package, normalize_evidence_package, preprocess_trace_content


class TraceCleaningTests(unittest.TestCase):
    def test_standalone_preprocessor_returns_primary_thread_metadata(self) -> None:
        trace = "\n".join(
            [
                "04-12 10:00:05.100 ----- pid 100 at 2026-04-12 -----",
                "Cmd line: com.demo",
                "binder tid=9 waiting in binder",
                "main tid=1 Native: waiting because no focused window",
                "RenderThread tid=2 runnable",
            ]
        )
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:05.100")
        self.assertEqual(result["pid"], "100")
        self.assertEqual(result["processName"], "com.demo")
        self.assertEqual(result["primaryThread"]["threadName"], "main")
        self.assertEqual(result["primaryThread"]["threadRole"], "main")
        self.assertEqual(result["primaryThread"]["threadState"], "native_waiting")
        self.assertEqual(result["primaryThread"]["artThreadState"], "Native:")
        self.assertEqual(result["primaryThread"]["javaThreadState"], "NATIVE:")
        self.assertEqual(result["primaryThread"]["blockHint"], "focus_window_wait")
        self.assertIsNone(result["primaryThread"]["sysTid"])
        self.assertEqual(result["sectionCount"], 1)
        self.assertEqual(result["threadSummary"]["suspiciousThreadCount"], 2)
        self.assertEqual(result["threadSummary"]["dominantBlockHint"], "binder_wait")
        self.assertEqual(result["suspiciousThreads"][0]["threadName"], "main")

    def test_preprocessor_produces_finer_block_categories(self) -> None:
        trace = "\n".join(
            [
                "04-12 10:00:05.100 ----- pid 200 -----",
                "Cmd line: com.demo",
                "main tid=1 input dispatching timeout",
                "binder tid=2 waiting for reply on binder",
                "RenderThread tid=3 waiting to lock <0x01>",
                "Finalizer tid=4 futex_wait_queue_me",
                "Signal Catcher tid=5 epoll_wait",
            ]
        )
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:05.100")
        hints = {thread["threadName"]: thread["blockHint"] for thread in result["threads"]}
        self.assertEqual(hints["main"], "input_dispatch_wait")
        self.assertEqual(hints["binder"], "binder_reply_wait")
        self.assertEqual(hints["RenderThread"], "monitor_contention")
        self.assertEqual(hints["Finalizer"], "futex_wait")
        self.assertEqual(hints["Signal Catcher"], "native_poll_wait")
        self.assertEqual(result["threadSummary"]["dominantBlockHint"], "binder_reply_wait")

    def test_preprocessor_resolves_lock_owner_thread(self) -> None:
        trace = "\n".join(
            [
                "04-12 10:00:05.100 ----- pid 300 -----",
                "Cmd line: com.demo",
                '"main" prio=5 tid=1 Blocked',
                '  | group="main" sCount=1 dsCount=0 obj=0x1 self=0x2',
                '  | sysTid=300 nice=0 cgrp=foreground sched=0/0 handle=0x100',
                '  | state=S schedstat=( 1 2 3 ) utm=1 stm=2 core=0 HZ=100',
                '  - waiting to lock <0x0da8cd6b> (a com.android.server.wm.WindowManagerGlobalLock) held by thread 19',
                '  at android.os.Looper.loop(Looper.java:319)',
                '"android.anim" prio=5 tid=19 Runnable',
                '  | group="main" sCount=0 ucsCount=0 flags=2 obj=0x14200cc0 self=0xb4000078384b9400',
                '  | sysTid=1338 nice=-4 cgrp=top-app sched=1073741824/0 handle=0x77b961bcb0',
                '  | state=R schedstat=( 4196659130941 59469621932 3505320 ) utm=351658 stm=68007 core=0 HZ=100',
                '  | held mutexes= "mutator lock"(shared held)',
                '  native: #00 pc 00424b4c  /apex/com.android.art/lib64/libart.so (art::DumpNativeStack+108)',
                '  at android.view.SurfaceControl.nativeSetInputWindowInfo(Native method)',
            ]
        )
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:05.100")
        self.assertEqual(result["primaryThread"]["lockOwnerTid"], "19")
        self.assertEqual(result["ownerThread"]["threadName"], "android.anim")
        self.assertEqual(result["ownerThread"]["tid"], "19")
        self.assertEqual(result["ownerThread"]["sysTid"], "1338")
        self.assertEqual(result["ownerThread"]["threadState"], "runnable")
        self.assertTrue(result["threadSummary"]["lockContentionDetected"])
        self.assertEqual(result["threadSummary"]["ownerThreadTid"], "19")

    def test_preprocessor_builds_binder_wait_chain_summary(self) -> None:
        trace = "\n".join(
            [
                "04-12 10:00:05.100 ----- pid 400 -----",
                "Cmd line: com.demo",
                '"main" prio=5 tid=1 Native',
                '  | sysTid=400 nice=0 cgrp=foreground sched=0/0 handle=0x100',
                '  native: #00 pc 000205d0  /system/lib/libc.so (__ioctl+8)',
                '  native: #01 pc 0001e519  /system/lib/libbinder.so (android::IPCThreadState::talkWithDriver(bool)+140)',
                '  native: #02 pc 0001ec67  /system/lib/libbinder.so (android::IPCThreadState::waitForResponse+6)',
                '  at android.os.BinderProxy.transactNative(Native method)',
                '"binder:400_1" prio=5 tid=9 Native',
                '  | sysTid=409 nice=0 cgrp=apps sched=0/0 handle=0x200',
                '  native: #00 pc 000205d0  /system/lib/libc.so (__ioctl+8)',
                '  native: #01 pc 0001e519  /system/lib/libbinder.so (android::IPCThreadState::talkWithDriver(bool)+140)',
                '  native: #02 pc 0001ecfd  /system/lib/libbinder.so (android::IPCThreadState::joinThreadPool(bool)+48)',
            ]
        )
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:05.100")
        self.assertTrue(result["binderSummary"]["binderWaitChainDetected"])
        self.assertTrue(result["binderSummary"]["mainThreadBinderBlocked"])
        self.assertEqual(result["binderSummary"]["mainThreadBinderCallKind"], "binder_wait_reply")
        self.assertEqual(result["binderSummary"]["binderThreadCount"], 1)
        self.assertEqual(result["binderSummary"]["binderReplyWaitCount"], 1)
        self.assertEqual(result["binderSummary"]["binderThreadPoolCount"], 1)
        self.assertEqual(result["binderSummary"]["topBinderThreads"][0]["threadName"], "main")

    def test_preprocessor_builds_render_wait_chain_summary(self) -> None:
        trace = "\n".join(
            [
                "04-12 10:00:05.100 ----- pid 500 -----",
                "Cmd line: com.demo",
                '"main" prio=5 tid=1 Native',
                '  | sysTid=500 nice=0 cgrp=foreground sched=0/0 handle=0x100',
                '  at android.view.Choreographer.doFrame(Choreographer.java:999)',
                '  at android.view.ViewRootImpl.doTraversal(ViewRootImpl.java:1234)',
                '"RenderThread" prio=5 tid=11 Native',
                '  | sysTid=511 nice=-4 cgrp=top-app sched=0/0 handle=0x200',
                '  native: #00 pc 00011111  /system/lib64/libEGL.so (eglSwapBuffers+8)',
                '  gpu completion wait',
            ]
        )
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:05.100")
        self.assertTrue(result["renderSummary"]["renderWaitChainDetected"])
        self.assertTrue(result["renderSummary"]["mainThreadRenderBlocked"])
        self.assertEqual(result["renderSummary"]["mainThreadRenderCallKind"], "main_do_frame")
        self.assertEqual(result["renderSummary"]["renderThreadCount"], 1)
        self.assertEqual(result["renderSummary"]["renderGpuWaitCount"], 1)
        self.assertEqual(result["renderSummary"]["renderDoFrameCount"], 1)
        self.assertEqual(result["renderSummary"]["topRenderThreads"][0]["threadName"], "main")

    def test_preprocessor_builds_suspend_summary(self) -> None:
        trace = "\n".join(
            [
                "04-12 10:00:05.100 ----- pid 600 -----",
                "Cmd line: com.demo",
                '"main" prio=5 tid=1 SUSPENDED',
                '  | sysTid=600 dsCount=1',
                '"RenderThread" prio=5 tid=11 SUSPENDED',
                '  | sysTid=611',
                '"HeapTaskDaemon" prio=5 tid=12 VMWAIT',
                '  | sysTid=612',
                '"ReferenceQueueDaemon" prio=5 tid=13 VMWAIT',
                '  | sysTid=613',
            ]
        )
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:05.100")
        self.assertTrue(result["suspendSummary"]["stwPauseDetected"])
        self.assertTrue(result["suspendSummary"]["vmWaitClusterDetected"])
        self.assertTrue(result["suspendSummary"]["debuggerSuspicion"])
        self.assertEqual(result["suspendSummary"]["suspendedThreadCount"], 2)
        self.assertEqual(result["suspendSummary"]["vmWaitThreadCount"], 2)

    def test_preprocessor_builds_cpu_scheduler_summary(self) -> None:
        trace = "\n".join(
            [
                "04-12 10:00:05.100 ----- pid 700 -----",
                "Cmd line: com.demo",
                '"main" prio=5 tid=1 Runnable',
                '  | sysTid=700',
                '  | state=R schedstat=( 1000000000 5000000000 100 ) utm=10 stm=5 core=0 HZ=100',
                '"RenderThread" prio=5 tid=11 Runnable',
                '  | sysTid=711',
                '  | state=R schedstat=( 3000000000 1000000000 100 ) utm=30 stm=10 core=2 HZ=100',
            ]
        )
        result = preprocess_trace_content(trace, anchor_timestamp="04-12 10:00:05.100")
        self.assertTrue(result["cpuSummary"]["schedulerPressureDetected"])
        self.assertFalse(result["cpuSummary"]["cpuBusyExecutionDetected"])
        self.assertTrue(result["cpuSummary"]["mainThreadRunnableLike"])
        self.assertEqual(result["cpuSummary"]["mainThreadRunNs"], 1000000000)
        self.assertEqual(result["cpuSummary"]["mainThreadWaitNs"], 5000000000)
        self.assertEqual(result["cpuSummary"]["runnableThreadCount"], 2)

    def test_real_trace_sample_filters_main_thread_correctly(self) -> None:
        trace_path = Path("samples/replay/assets/anr001/anr_2026-01-09-13-57-58-287")
        if not trace_path.exists():
            self.skipTest(f"optional replay asset is missing: {trace_path}")
        trace = trace_path.read_text(encoding="utf-8", errors="replace")
        result = preprocess_trace_content(trace)
        self.assertEqual(result["processName"], "com.tcl.android.launcher")
        self.assertEqual(result["pid"], "32065")
        self.assertEqual(result["primaryThread"]["threadName"], "main")
        self.assertEqual(result["primaryThread"]["tid"], "1")
        self.assertTrue(result["primaryThread"]["isMainThread"])
        self.assertEqual(result["primaryThread"]["sysTid"], "32065")
        self.assertEqual(result["primaryThread"]["group"], "main")
        self.assertEqual(result["primaryThread"]["nice"], "0")
        self.assertEqual(result["primaryThread"]["cgrp"], "foreground")
        self.assertEqual(result["primaryThread"]["linuxState"], "S")
        self.assertEqual(result["primaryThread"]["blockHint"], "native_poll_wait")
        self.assertIn('__epoll_pwait', result["primaryThread"]["nativeTopFrame"])
        self.assertEqual(result["primaryThread"]["javaTopFrame"], 'at android.os.MessageQueue.nativePollOnce(Native method)')
        self.assertEqual(result["primaryThread"]["looperFrame"], 'at android.os.MessageQueue.nativePollOnce(Native method)')
        self.assertIn('"main" prio=5 tid=1 Native', result["compactedContent"])
        self.assertIn('android.os.Looper.loop(Looper.java:371)', result["primaryThread"]["rawBlock"])
        self.assertTrue(any(thread["threadName"] == "main" for thread in result["threads"]))
        self.assertTrue(result["threadSummary"]["mainThreadBlocked"])

    def test_trace_baseline_prefers_anchor_adjacent_relevant_section(self) -> None:
        package = {
            "package_id": "TRACE-CLEAN-01",
            "provided_type": None,
            "sources": {
                "event_log": {
                    "path": "events.log",
                    "content": "\n".join(
                        [
                            "04-12 10:00:03.000 am_proc_start Proc start",
                            "04-12 10:00:05.000 am_anr ANR in com.demo: No focused window",
                        ]
                    ),
                },
                "trace": {
                    "path": "traces.txt",
                    "content": "\n".join(
                        [
                            "04-12 09:58:00.000 ----- pid 10 -----",
                            "Cmd line: com.other",
                            "binder tid=9 waiting in binder",
                            "04-12 10:00:05.100 ----- pid 100 at 2026-04-12 -----",
                            "Cmd line: com.demo",
                            "main tid=1 Native: waiting because no focused window",
                            "RenderThread tid=2 waiting for GPU",
                        ]
                    ),
                },
                "logcat": {
                    "path": "logcat.txt",
                    "content": "04-12 10:00:05.050 E InputDispatcher Application is not responding: no focused window",
                },
                "kernel_log": {
                    "path": "kernel.txt",
                    "content": "04-12 10:00:06.000 sched: main thread stalled",
                },
            },
        }

        extracted = extract_evidence_package(package)
        trace_evidence = next(item for item in extracted["evidence"] if item["id"] == "trace_core")

        self.assertIn("Cmd line: com.demo", trace_evidence["content"])
        self.assertIn("main tid=1 Native: waiting because no focused window", trace_evidence["content"])
        self.assertNotIn("Cmd line: com.other", trace_evidence["content"])

    def test_trace_normalization_extracts_pid_and_block_hint(self) -> None:
        package = {
            "metadata": {
                "packageId": "TRACE-NORM-01",
                "phase": "phase1-evidence-extraction-mvp",
                "status": "complete",
            },
            "classification": {
                "detectedType": "no_focus_window",
                "supported": True,
                "confidence": 1.0,
                "fallbackMode": "none",
            },
            "anchors": {
                "primaryAnchor": {
                    "sourceKind": "trace",
                    "timestamp": "04-12 10:00:05.100",
                    "line": "04-12 10:00:05.100 ----- pid 100 at 2026-04-12 -----",
                },
                "secondaryAnchors": [],
                "normalizationWarnings": [],
            },
            "sources": {
                "trace": {
                    "available": True,
                    "readable": True,
                    "path": "traces.txt",
                    "retainedEvidenceCount": 1,
                    "retainedTiers": ["P0"],
                }
            },
            "evidence": [
                {
                    "id": "trace_core",
                    "label": "trace-baseline-context",
                    "sourceKind": "trace",
                    "tier": "P0",
                    "extractionMode": "baseline",
                    "content": "\n".join(
                        [
                            "04-12 10:00:05.100 ----- pid 100 at 2026-04-12 -----",
                            "Cmd line: com.demo",
                            "main tid=1 Native: waiting because no focused window",
                        ]
                    ),
                    "provenance": {
                        "sourceKind": "trace",
                        "sourcePath": "traces.txt",
                        "extractionRule": "trace-baseline",
                        "timeWindow": "full-trace-context",
                        "anchorUsed": {
                            "sourceKind": "trace",
                            "timestamp": "04-12 10:00:05.100",
                            "line": "04-12 10:00:05.100 ----- pid 100 at 2026-04-12 -----",
                        },
                        "tier": "P0",
                        "extractionMode": "baseline",
                        "warningFlags": [],
                    },
                }
            ],
            "warnings": [],
        }

        normalized = normalize_evidence_package(package)
        trace_fields = normalized["normalizedRecords"][0]["normalizedFields"]
        self.assertEqual(trace_fields["pid"], "100")
        self.assertEqual(trace_fields["threadName"], "main")
        self.assertEqual(trace_fields["threadState"], "native_waiting")
        self.assertEqual(trace_fields["blockHint"], "focus_window_wait")
        self.assertEqual(trace_fields["retainedThreadCount"], 1)
        self.assertEqual(trace_fields["suspiciousThreadCount"], 1)
        self.assertEqual(trace_fields["dominantBlockHint"], "focus_window_wait")


if __name__ == "__main__":
    unittest.main()
