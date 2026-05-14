from __future__ import annotations

import json
import unittest
from pathlib import Path

from anr_evidence import analyze_normalized_package, extract_evidence_package, normalize_evidence_package
from tests.helpers import load_fixture

FIXTURES = [
    'nfw_01.json',
    'idt_01.json',
    'unk_01.json',
    'amb_01.json',
    'miss_trace_01.json',
    'miss_kernel_01.json',
    'clock_skew_01.json',
    'noisy_01.json',
]


class Phase3AnalysisTests(unittest.TestCase):
    def test_all_phase2_fixtures_analyze(self) -> None:
        for fixture_name in FIXTURES:
            with self.subTest(fixture=fixture_name):
                phase1 = extract_evidence_package(load_fixture(fixture_name))
                phase2 = normalize_evidence_package(phase1)
                phase3 = analyze_normalized_package(phase2)

                self.assertEqual(phase3['metadata']['phase'], 'phase3-assisted-analysis')
                self.assertEqual(phase3['metadata']['schemaVersion'], 'phase3-analysis-v1')
                self.assertEqual(phase3['metadata']['status'], phase2['metadata']['status'])
                self.assertEqual(phase3['classification'], phase2['classification'])
                self.assertEqual(phase3['anchors'], phase2['anchors'])
                self.assertIn('signalSummary', phase3)
                self.assertIn('timeline', phase3)
                self.assertIn('findings', phase3)
                self.assertTrue(phase3['findings'])

    def test_findings_are_non_root_cause(self) -> None:
        phase3 = analyze_normalized_package(normalize_evidence_package(extract_evidence_package(load_fixture('nfw_01.json'))))
        messages = ' '.join(finding['message'].lower() for finding in phase3['findings'])
        self.assertIn('not a root-cause judgment', messages)
        self.assertNotIn('root cause is', messages)
        self.assertIn('suspicious thread', messages)

    def test_trace_insights_are_exposed_in_signal_summary_and_timeline(self) -> None:
        phase3 = analyze_normalized_package(normalize_evidence_package(extract_evidence_package(load_fixture('nfw_01.json'))))
        trace_insights = phase3['signalSummary']['traceInsights']
        self.assertGreaterEqual(trace_insights['suspiciousRecordCount'], 1)
        self.assertGreaterEqual(trace_insights['suspiciousThreadTotal'], 1)
        self.assertIn('focus_window_wait', trace_insights['dominantBlockHintCounts'])
        self.assertTrue(trace_insights['mainThread']['captured'])
        self.assertEqual(trace_insights['mainThread']['threadName'], 'main')
        self.assertEqual(trace_insights['mainThread']['threadRole'], 'main')
        suspicious_finding_keys = {finding['key'] for finding in phase3['findings'] if finding['type'] == 'trace'}
        self.assertIn('suspicious_threads', suspicious_finding_keys)
        self.assertIn('main_thread', suspicious_finding_keys)
        trace_timeline = [entry for entry in phase3['timeline'] if entry['sourceKind'] == 'trace']
        self.assertTrue(trace_timeline)
        self.assertEqual(trace_timeline[0]['dominantBlockHint'], 'focus_window_wait')
        self.assertEqual(trace_timeline[0]['suspiciousThreadCount'], 1)

    def test_input_insights_detect_no_focused_window_cross_source(self) -> None:
        phase3 = analyze_normalized_package(normalize_evidence_package(extract_evidence_package(load_fixture('nfw_01.json'))))
        input_insights = phase3['signalSummary']['inputInsights']
        self.assertTrue(input_insights['noFocusedWindowDetected'])
        self.assertTrue(input_insights['crossSourceInputConsistency'])
        self.assertEqual(input_insights['detectedFamily'], 'no_focused_window')
        self.assertTrue(input_insights['eventAmAnrInputDetected'])
        self.assertTrue(input_insights['logcatInputDispatcherDetected'])
        self.assertTrue(input_insights['traceInputDispatcherDetected'])
        input_finding_keys = {finding['key'] for finding in phase3['findings'] if finding['type'] == 'input'}
        self.assertIn('no_focused_window', input_finding_keys)
        self.assertIn('cross_source_confirmed', input_finding_keys)

    def test_input_insights_detect_dispatcher_wait_cross_source(self) -> None:
        phase3 = analyze_normalized_package(normalize_evidence_package(extract_evidence_package(load_fixture('idt_01.json'))))
        input_insights = phase3['signalSummary']['inputInsights']
        self.assertTrue(input_insights['inputWaitDetected'])
        self.assertTrue(input_insights['crossSourceInputConsistency'])
        self.assertEqual(input_insights['detectedFamily'], 'input_dispatch_wait')
        self.assertTrue(input_insights['eventAmAnrInputDetected'])
        self.assertTrue(input_insights['logcatInputDispatcherDetected'])
        self.assertTrue(input_insights['traceInputDispatcherDetected'])
        input_finding_keys = {finding['key'] for finding in phase3['findings'] if finding['type'] == 'input'}
        self.assertIn('dispatcher_wait_finish', input_finding_keys)
        self.assertIn('cross_source_confirmed', input_finding_keys)

    def test_fallback_and_missing_source_signals_are_preserved(self) -> None:
        ambiguous = analyze_normalized_package(normalize_evidence_package(extract_evidence_package(load_fixture('amb_01.json'))))
        finding_keys = {finding['key'] for finding in ambiguous['findings']}
        self.assertIn('ambiguous_type', finding_keys)

        partial = analyze_normalized_package(normalize_evidence_package(extract_evidence_package(load_fixture('miss_trace_01.json'))))
        coverage_findings = [finding for finding in partial['findings'] if finding['type'] == 'coverage']
        self.assertTrue(coverage_findings)
        self.assertIn('missing: trace', coverage_findings[0]['message'])

    def test_analysis_is_deterministic(self) -> None:
        first = analyze_normalized_package(normalize_evidence_package(extract_evidence_package(load_fixture('idt_01.json'))))
        second = analyze_normalized_package(normalize_evidence_package(extract_evidence_package(load_fixture('idt_01.json'))))
        self.assertEqual(
            json.dumps(first, sort_keys=True, ensure_ascii=False),
            json.dumps(second, sort_keys=True, ensure_ascii=False),
        )

    def test_lock_owner_thread_summary_is_exposed(self) -> None:
        trace = "\n".join([
            "04-12 10:00:05.100 ----- pid 300 -----",
            "Cmd line: com.demo",
            '"main" prio=5 tid=1 Blocked',
            '  | group="main" sCount=1 dsCount=0 obj=0x1 self=0x2',
            '  | sysTid=300 nice=0 cgrp=foreground sched=0/0 handle=0x100',
            '  - waiting to lock <0x0da8cd6b> (a com.android.server.wm.WindowManagerGlobalLock) held by thread 19',
            '  at android.os.Looper.loop(Looper.java:319)',
            '"android.anim" prio=5 tid=19 Runnable',
            '  | sysTid=1338 nice=-4 cgrp=top-app sched=1073741824/0 handle=0x77b961bcb0',
            '  | state=R schedstat=( 4196659130941 59469621932 3505320 ) utm=351658 stm=68007 core=0 HZ=100',
            '  | held mutexes= "mutator lock"(shared held)',
        ])
        phase3 = analyze_normalized_package(normalize_evidence_package({
            "metadata": {"packageId": "LOCK-OWNER-01", "phase": "phase1-evidence-extraction-mvp", "status": "complete"},
            "classification": {"detectedType": None, "supported": False, "confidence": 0.0, "fallbackMode": "unknown_type"},
            "anchors": {"primaryAnchor": None, "secondaryAnchors": [], "normalizationWarnings": []},
            "sources": {"trace": {"available": True, "readable": True, "path": "lock.txt", "retainedEvidenceCount": 1, "retainedTiers": ["P0"]}},
            "evidence": [{"id": "trace_core", "label": "trace-baseline-context", "sourceKind": "trace", "tier": "P0", "extractionMode": "baseline", "content": trace, "provenance": {"sourceKind": "trace", "sourcePath": "lock.txt", "extractionRule": "trace-baseline", "timeWindow": "full-trace-context", "anchorUsed": None, "tier": "P0", "extractionMode": "baseline", "warningFlags": []}}],
            "warnings": [],
        }))
        main_thread = phase3['signalSummary']['traceInsights']['mainThread']
        self.assertTrue(main_thread['lockContentionDetected'])
        self.assertEqual(main_thread['lockOwnerThreadName'], 'android.anim')
        self.assertEqual(main_thread['lockOwnerThreadSysTid'], '1338')
        trace_finding_keys = {finding['key'] for finding in phase3['findings'] if finding['type'] == 'trace'}
        self.assertIn('lock_owner', trace_finding_keys)

    def test_binder_wait_chain_summary_is_exposed(self) -> None:
        trace = "\n".join([
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
        ])
        phase3 = analyze_normalized_package(normalize_evidence_package({
            "metadata": {"packageId": "BINDER-01", "phase": "phase1-evidence-extraction-mvp", "status": "complete"},
            "classification": {"detectedType": None, "supported": False, "confidence": 0.0, "fallbackMode": "unknown_type"},
            "anchors": {"primaryAnchor": None, "secondaryAnchors": [], "normalizationWarnings": []},
            "sources": {"trace": {"available": True, "readable": True, "path": "binder.txt", "retainedEvidenceCount": 1, "retainedTiers": ["P0"]}},
            "evidence": [{"id": "trace_core", "label": "trace-baseline-context", "sourceKind": "trace", "tier": "P0", "extractionMode": "baseline", "content": trace, "provenance": {"sourceKind": "trace", "sourcePath": "binder.txt", "extractionRule": "trace-baseline", "timeWindow": "full-trace-context", "anchorUsed": None, "tier": "P0", "extractionMode": "baseline", "warningFlags": []}}],
            "warnings": [],
        }))
        binder_summary = phase3['signalSummary']['traceInsights']['binderSummary']
        self.assertTrue(binder_summary['binderWaitChainDetected'])
        self.assertTrue(binder_summary['mainThreadBinderBlocked'])
        self.assertEqual(binder_summary['mainThreadBinderCallKind'], 'binder_wait_reply')
        self.assertEqual(binder_summary['binderThreadCount'], 1)
        self.assertEqual(binder_summary['binderReplyWaitCount'], 1)
        trace_finding_keys = {finding['key'] for finding in phase3['findings'] if finding['type'] == 'trace'}
        self.assertIn('binder_chain', trace_finding_keys)

    def test_render_wait_chain_summary_is_exposed(self) -> None:
        trace = "\n".join([
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
        ])
        phase3 = analyze_normalized_package(normalize_evidence_package({
            "metadata": {"packageId": "RENDER-01", "phase": "phase1-evidence-extraction-mvp", "status": "complete"},
            "classification": {"detectedType": None, "supported": False, "confidence": 0.0, "fallbackMode": "unknown_type"},
            "anchors": {"primaryAnchor": None, "secondaryAnchors": [], "normalizationWarnings": []},
            "sources": {"trace": {"available": True, "readable": True, "path": "render.txt", "retainedEvidenceCount": 1, "retainedTiers": ["P0"]}},
            "evidence": [{"id": "trace_core", "label": "trace-baseline-context", "sourceKind": "trace", "tier": "P0", "extractionMode": "baseline", "content": trace, "provenance": {"sourceKind": "trace", "sourcePath": "render.txt", "extractionRule": "trace-baseline", "timeWindow": "full-trace-context", "anchorUsed": None, "tier": "P0", "extractionMode": "baseline", "warningFlags": []}}],
            "warnings": [],
        }))
        render_summary = phase3['signalSummary']['traceInsights']['renderSummary']
        self.assertTrue(render_summary['renderWaitChainDetected'])
        self.assertTrue(render_summary['mainThreadRenderBlocked'])
        self.assertEqual(render_summary['mainThreadRenderCallKind'], 'main_do_frame')
        self.assertEqual(render_summary['renderThreadCount'], 1)
        self.assertEqual(render_summary['renderGpuWaitCount'], 1)
        trace_finding_keys = {finding['key'] for finding in phase3['findings'] if finding['type'] == 'trace'}
        self.assertIn('render_chain', trace_finding_keys)

    def test_suspend_summary_is_exposed(self) -> None:
        trace = "\n".join([
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
        ])
        phase3 = analyze_normalized_package(normalize_evidence_package({
            "metadata": {"packageId": "SUSPEND-01", "phase": "phase1-evidence-extraction-mvp", "status": "complete"},
            "classification": {"detectedType": None, "supported": False, "confidence": 0.0, "fallbackMode": "unknown_type"},
            "anchors": {"primaryAnchor": None, "secondaryAnchors": [], "normalizationWarnings": []},
            "sources": {"trace": {"available": True, "readable": True, "path": "suspend.txt", "retainedEvidenceCount": 1, "retainedTiers": ["P0"]}},
            "evidence": [{"id": "trace_core", "label": "trace-baseline-context", "sourceKind": "trace", "tier": "P0", "extractionMode": "baseline", "content": trace, "provenance": {"sourceKind": "trace", "sourcePath": "suspend.txt", "extractionRule": "trace-baseline", "timeWindow": "full-trace-context", "anchorUsed": None, "tier": "P0", "extractionMode": "baseline", "warningFlags": []}}],
            "warnings": [],
        }))
        suspend_summary = phase3['signalSummary']['traceInsights']['suspendSummary']
        self.assertTrue(suspend_summary['stwPauseDetected'])
        self.assertTrue(suspend_summary['vmWaitClusterDetected'])
        self.assertTrue(suspend_summary['debuggerSuspicion'])
        self.assertEqual(suspend_summary['suspendedThreadCount'], 2)
        self.assertEqual(suspend_summary['vmWaitThreadCount'], 2)
        trace_finding_keys = {finding['key'] for finding in phase3['findings'] if finding['type'] == 'trace'}
        self.assertIn('gc_stw', trace_finding_keys)
        self.assertIn('vm_wait_cluster', trace_finding_keys)

    def test_cpu_summary_is_exposed(self) -> None:
        trace = "\n".join([
            "04-12 10:00:05.100 ----- pid 700 -----",
            "Cmd line: com.demo",
            '"main" prio=5 tid=1 Runnable',
            '  | sysTid=700',
            '  | state=R schedstat=( 1000000000 5000000000 100 ) utm=10 stm=5 core=0 HZ=100',
            '"RenderThread" prio=5 tid=11 Runnable',
            '  | sysTid=711',
            '  | state=R schedstat=( 3000000000 1000000000 100 ) utm=30 stm=10 core=2 HZ=100',
        ])
        phase3 = analyze_normalized_package(normalize_evidence_package({
            "metadata": {"packageId": "CPU-01", "phase": "phase1-evidence-extraction-mvp", "status": "complete"},
            "classification": {"detectedType": None, "supported": False, "confidence": 0.0, "fallbackMode": "unknown_type"},
            "anchors": {"primaryAnchor": None, "secondaryAnchors": [], "normalizationWarnings": []},
            "sources": {"trace": {"available": True, "readable": True, "path": "cpu.txt", "retainedEvidenceCount": 1, "retainedTiers": ["P0"]}},
            "evidence": [{"id": "trace_core", "label": "trace-baseline-context", "sourceKind": "trace", "tier": "P0", "extractionMode": "baseline", "content": trace, "provenance": {"sourceKind": "trace", "sourcePath": "cpu.txt", "extractionRule": "trace-baseline", "timeWindow": "full-trace-context", "anchorUsed": None, "tier": "P0", "extractionMode": "baseline", "warningFlags": []}}],
            "warnings": [],
        }))
        cpu_summary = phase3['signalSummary']['traceInsights']['cpuSummary']
        self.assertTrue(cpu_summary['schedulerPressureDetected'])
        self.assertFalse(cpu_summary['cpuBusyExecutionDetected'])
        self.assertTrue(cpu_summary['mainThreadRunnableLike'])
        self.assertEqual(cpu_summary['mainThreadRunNs'], 1000000000)
        self.assertEqual(cpu_summary['mainThreadWaitNs'], 5000000000)
        trace_finding_keys = {finding['key'] for finding in phase3['findings'] if finding['type'] == 'trace'}
        self.assertIn('scheduler_pressure', trace_finding_keys)


if __name__ == '__main__':
    unittest.main()
