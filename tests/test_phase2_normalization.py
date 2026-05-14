from __future__ import annotations

import json
import unittest
from pathlib import Path

from anr_evidence import extract_evidence_package, normalize_evidence_package
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


class Phase2NormalizationTests(unittest.TestCase):
    def test_all_phase1_fixtures_normalize(self) -> None:
        for fixture_name in FIXTURES:
            with self.subTest(fixture=fixture_name):
                upstream = extract_evidence_package(load_fixture(fixture_name))
                normalized = normalize_evidence_package(upstream)

                self.assertEqual(normalized['metadata']['phase'], 'phase2-evidence-normalization')
                self.assertEqual(normalized['metadata']['schemaVersion'], 'phase2-normalized-v1')
                self.assertEqual(normalized['metadata']['status'], upstream['metadata']['status'])
                self.assertEqual(normalized['classification'], upstream['classification'])
                self.assertEqual(normalized['anchors'], upstream['anchors'])
                self.assertIn('sourceSummaries', normalized)
                self.assertIn('normalizedRecords', normalized)
                self.assertGreaterEqual(len(normalized['normalizedRecords']), len(upstream['evidence']))

                for record in normalized['normalizedRecords']:
                    for field in ('recordId', 'sourceKind', 'recordType', 'tier', 'label', 'sourcePath', 'extractionMode', 'normalizedFields', 'contentLineCount', 'provenance', 'warnings'):
                        self.assertIn(field, record)
                    self.assertTrue(record.get('rawSnippet') or record.get('rawContentRef'))

    def test_source_specific_minimum_fields_exist(self) -> None:
        normalized = normalize_evidence_package(extract_evidence_package(load_fixture('nfw_01.json')))
        by_source = {}
        for record in normalized['normalizedRecords']:
            by_source.setdefault(record['sourceKind'], []).append(record)

        trace_record = next(record for record in by_source['trace'] if record['label'] == 'trace-baseline-context')
        trace_fields = trace_record['normalizedFields']
        self.assertIn('processName', trace_fields)
        self.assertIn('pid', trace_fields)
        self.assertIn('threadName', trace_fields)
        self.assertIn('threadRole', trace_fields)
        self.assertIn('threadState', trace_fields)
        self.assertIn('blockHint', trace_fields)
        self.assertIn('mainThreadCaptured', trace_fields)
        self.assertIn('mainThreadSysTid', trace_fields)
        self.assertIn('artThreadState', trace_fields)
        self.assertIn('javaThreadState', trace_fields)
        self.assertIn('group', trace_fields)
        self.assertIn('linuxState', trace_fields)
        self.assertIn('nice', trace_fields)
        self.assertIn('cgrp', trace_fields)
        self.assertIn('mainThreadNativeTopFrame', trace_fields)
        self.assertIn('mainThreadJavaTopFrame', trace_fields)
        self.assertIn('mainThreadLooperFrame', trace_fields)
        self.assertEqual(trace_fields['pid'], '100')
        self.assertEqual(trace_fields['blockHint'], 'focus_window_wait')
        self.assertTrue(trace_fields['mainThreadCaptured'])

        event_fields = by_source['event_log'][0]['normalizedFields']
        self.assertIn('eventTag', event_fields)
        self.assertIn('eventTimestamp', event_fields)
        self.assertIn('markerKind', event_fields)

        logcat_fields = by_source['logcat'][0]['normalizedFields']
        self.assertIn('timestamp', logcat_fields)
        self.assertIn('matchedSymptomCategory', logcat_fields)
        self.assertIn('lineRole', logcat_fields)
        self.assertIn('windowKind', logcat_fields)

        kernel_fields = by_source['kernel_log'][0]['normalizedFields']
        self.assertIn('timestamp', kernel_fields)
        self.assertIn('subsystemHint', kernel_fields)
        self.assertIn('matchedSymptomCategory', kernel_fields)
        self.assertIn('windowKind', kernel_fields)

    def test_record_types_are_more_specific(self) -> None:
        normalized = normalize_evidence_package(extract_evidence_package(load_fixture('nfw_01.json')))
        record_types = {(record['sourceKind'], record['recordType']) for record in normalized['normalizedRecords']}
        self.assertIn(('trace', 'trace_thread_context'), record_types)
        self.assertIn(('event_log', 'event_marker'), record_types)
        self.assertIn(('event_log', 'event_pre_window'), record_types)
        self.assertIn(('logcat', 'log_anchor_window'), record_types)
        self.assertIn(('kernel_log', 'kernel_anchor_window'), record_types)

    def test_anchor_and_fallback_semantics_are_preserved(self) -> None:
        ambiguous = normalize_evidence_package(extract_evidence_package(load_fixture('amb_01.json')))
        self.assertEqual(ambiguous['classification']['fallbackMode'], 'ambiguous_type')
        self.assertEqual(ambiguous['metadata']['status'], 'degraded')

        skewed = normalize_evidence_package(extract_evidence_package(load_fixture('clock_skew_01.json')))
        self.assertTrue(skewed['anchors']['secondaryAnchors'])
        mismatch_codes = {warning['code'] for warning in skewed['warnings']}
        self.assertIn('anchor-mismatch', mismatch_codes)
        for record in skewed['normalizedRecords']:
            if record['anchorRef'] is not None:
                self.assertEqual(record['anchorRef']['sourceKind'], 'event_log')

    def test_normalization_is_deterministic_for_same_fixture(self) -> None:
        first = normalize_evidence_package(extract_evidence_package(load_fixture('idt_01.json')))
        second = normalize_evidence_package(extract_evidence_package(load_fixture('idt_01.json')))
        self.assertEqual(
            json.dumps(first, sort_keys=True, ensure_ascii=False),
            json.dumps(second, sort_keys=True, ensure_ascii=False),
        )

    def test_lock_owner_thread_fields_are_exposed(self) -> None:
        trace = "\n".join([
            "04-12 10:00:05.100 ----- pid 300 -----",
            "Cmd line: com.demo",
            '"main" prio=5 tid=1 Blocked',
            '  | group="main" sCount=1 dsCount=0 obj=0x1 self=0x2',
            '  | sysTid=300 nice=0 cgrp=foreground sched=0/0 handle=0x100',
            '  - waiting to lock <0x0da8cd6b> (a com.android.server.wm.WindowManagerGlobalLock) held by thread 19',
            '"android.anim" prio=5 tid=19 Runnable',
            '  | sysTid=1338 nice=-4 cgrp=top-app sched=1073741824/0 handle=0x77b961bcb0',
            '  | state=R schedstat=( 4196659130941 59469621932 3505320 ) utm=351658 stm=68007 core=0 HZ=100',
            '  | held mutexes= "mutator lock"(shared held)',
        ])
        normalized = normalize_evidence_package({
            "metadata": {"packageId": "LOCK-OWNER-01", "phase": "phase1-evidence-extraction-mvp", "status": "complete"},
            "classification": {"detectedType": None, "supported": False, "confidence": 0.0, "fallbackMode": "unknown_type"},
            "anchors": {"primaryAnchor": None, "secondaryAnchors": [], "normalizationWarnings": []},
            "sources": {"trace": {"available": True, "readable": True, "path": "lock.txt", "retainedEvidenceCount": 1, "retainedTiers": ["P0"]}},
            "evidence": [{"id": "trace_core", "label": "trace-baseline-context", "sourceKind": "trace", "tier": "P0", "extractionMode": "baseline", "content": trace, "provenance": {"sourceKind": "trace", "sourcePath": "lock.txt", "extractionRule": "trace-baseline", "timeWindow": "full-trace-context", "anchorUsed": None, "tier": "P0", "extractionMode": "baseline", "warningFlags": []}}],
            "warnings": [],
        })
        trace_fields = normalized['normalizedRecords'][0]['normalizedFields']
        self.assertTrue(trace_fields['lockContentionDetected'])
        self.assertEqual(trace_fields['lockOwnerTid'], '19')
        self.assertEqual(trace_fields['lockOwnerThreadName'], 'android.anim')
        self.assertEqual(trace_fields['lockOwnerThreadSysTid'], '1338')
        self.assertEqual(trace_fields['lockOwnerThreadState'], 'runnable')

    def test_binder_wait_chain_fields_are_exposed(self) -> None:
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
        normalized = normalize_evidence_package({
            "metadata": {"packageId": "BINDER-01", "phase": "phase1-evidence-extraction-mvp", "status": "complete"},
            "classification": {"detectedType": None, "supported": False, "confidence": 0.0, "fallbackMode": "unknown_type"},
            "anchors": {"primaryAnchor": None, "secondaryAnchors": [], "normalizationWarnings": []},
            "sources": {"trace": {"available": True, "readable": True, "path": "binder.txt", "retainedEvidenceCount": 1, "retainedTiers": ["P0"]}},
            "evidence": [{"id": "trace_core", "label": "trace-baseline-context", "sourceKind": "trace", "tier": "P0", "extractionMode": "baseline", "content": trace, "provenance": {"sourceKind": "trace", "sourcePath": "binder.txt", "extractionRule": "trace-baseline", "timeWindow": "full-trace-context", "anchorUsed": None, "tier": "P0", "extractionMode": "baseline", "warningFlags": []}}],
            "warnings": [],
        })
        trace_fields = normalized['normalizedRecords'][0]['normalizedFields']
        self.assertTrue(trace_fields['binderWaitChainDetected'])
        self.assertTrue(trace_fields['mainThreadBinderBlocked'])
        self.assertEqual(trace_fields['mainThreadBinderCallKind'], 'binder_wait_reply')
        self.assertEqual(trace_fields['binderThreadCount'], 1)
        self.assertEqual(trace_fields['binderReplyWaitCount'], 1)
        self.assertEqual(trace_fields['binderThreadPoolCount'], 1)
        self.assertEqual(trace_fields['topBinderThreads'][0]['threadName'], 'main')

    def test_render_wait_chain_fields_are_exposed(self) -> None:
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
        normalized = normalize_evidence_package({
            "metadata": {"packageId": "RENDER-01", "phase": "phase1-evidence-extraction-mvp", "status": "complete"},
            "classification": {"detectedType": None, "supported": False, "confidence": 0.0, "fallbackMode": "unknown_type"},
            "anchors": {"primaryAnchor": None, "secondaryAnchors": [], "normalizationWarnings": []},
            "sources": {"trace": {"available": True, "readable": True, "path": "render.txt", "retainedEvidenceCount": 1, "retainedTiers": ["P0"]}},
            "evidence": [{"id": "trace_core", "label": "trace-baseline-context", "sourceKind": "trace", "tier": "P0", "extractionMode": "baseline", "content": trace, "provenance": {"sourceKind": "trace", "sourcePath": "render.txt", "extractionRule": "trace-baseline", "timeWindow": "full-trace-context", "anchorUsed": None, "tier": "P0", "extractionMode": "baseline", "warningFlags": []}}],
            "warnings": [],
        })
        trace_fields = normalized['normalizedRecords'][0]['normalizedFields']
        self.assertTrue(trace_fields['renderWaitChainDetected'])
        self.assertTrue(trace_fields['mainThreadRenderBlocked'])
        self.assertEqual(trace_fields['mainThreadRenderCallKind'], 'main_do_frame')
        self.assertEqual(trace_fields['renderThreadCount'], 1)
        self.assertEqual(trace_fields['renderGpuWaitCount'], 1)
        self.assertEqual(trace_fields['renderDoFrameCount'], 1)
        self.assertEqual(trace_fields['topRenderThreads'][0]['threadName'], 'main')

    def test_suspend_summary_fields_are_exposed(self) -> None:
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
        normalized = normalize_evidence_package({
            "metadata": {"packageId": "SUSPEND-01", "phase": "phase1-evidence-extraction-mvp", "status": "complete"},
            "classification": {"detectedType": None, "supported": False, "confidence": 0.0, "fallbackMode": "unknown_type"},
            "anchors": {"primaryAnchor": None, "secondaryAnchors": [], "normalizationWarnings": []},
            "sources": {"trace": {"available": True, "readable": True, "path": "suspend.txt", "retainedEvidenceCount": 1, "retainedTiers": ["P0"]}},
            "evidence": [{"id": "trace_core", "label": "trace-baseline-context", "sourceKind": "trace", "tier": "P0", "extractionMode": "baseline", "content": trace, "provenance": {"sourceKind": "trace", "sourcePath": "suspend.txt", "extractionRule": "trace-baseline", "timeWindow": "full-trace-context", "anchorUsed": None, "tier": "P0", "extractionMode": "baseline", "warningFlags": []}}],
            "warnings": [],
        })
        trace_fields = normalized['normalizedRecords'][0]['normalizedFields']
        self.assertTrue(trace_fields['stwPauseDetected'])
        self.assertTrue(trace_fields['vmWaitClusterDetected'])
        self.assertTrue(trace_fields['debuggerSuspicion'])
        self.assertEqual(trace_fields['suspendedThreadCount'], 2)
        self.assertEqual(trace_fields['vmWaitThreadCount'], 2)

    def test_cpu_summary_fields_are_exposed(self) -> None:
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
        normalized = normalize_evidence_package({
            "metadata": {"packageId": "CPU-01", "phase": "phase1-evidence-extraction-mvp", "status": "complete"},
            "classification": {"detectedType": None, "supported": False, "confidence": 0.0, "fallbackMode": "unknown_type"},
            "anchors": {"primaryAnchor": None, "secondaryAnchors": [], "normalizationWarnings": []},
            "sources": {"trace": {"available": True, "readable": True, "path": "cpu.txt", "retainedEvidenceCount": 1, "retainedTiers": ["P0"]}},
            "evidence": [{"id": "trace_core", "label": "trace-baseline-context", "sourceKind": "trace", "tier": "P0", "extractionMode": "baseline", "content": trace, "provenance": {"sourceKind": "trace", "sourcePath": "cpu.txt", "extractionRule": "trace-baseline", "timeWindow": "full-trace-context", "anchorUsed": None, "tier": "P0", "extractionMode": "baseline", "warningFlags": []}}],
            "warnings": [],
        })
        trace_fields = normalized['normalizedRecords'][0]['normalizedFields']
        self.assertTrue(trace_fields['schedulerPressureDetected'])
        self.assertFalse(trace_fields['cpuBusyExecutionDetected'])
        self.assertTrue(trace_fields['mainThreadRunnableLike'])
        self.assertEqual(trace_fields['mainThreadRunNs'], 1000000000)
        self.assertEqual(trace_fields['mainThreadWaitNs'], 5000000000)
        self.assertEqual(trace_fields['runnableThreadCount'], 2)


if __name__ == '__main__':
    unittest.main()
