from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from pathlib import Path

from anr_evidence import AiContextOptions, build_ai_context, build_ai_context_artifacts

ROOT = Path(__file__).resolve().parent.parent
ENV = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}


def _multi_anr_package() -> dict:
    return {
        'package_id': 'AICTX-01',
        'sources': {
            'event_log': {
                'path': 'events.log',
                'content': '\n'.join([
                    '04-12 10:00:01.000 wm_focus com.demo first focus',
                    '04-12 10:00:05.000 am_anr ANR in com.demo first',
                    '04-12 10:00:05.500 am_anr ANR in com.demo first',
                    '04-12 10:01:01.000 input_focus com.demo second focus',
                    '04-12 10:01:05.000 am_anr ANR in com.demo second',
                ]),
            },
            'trace': {
                'path': 'traces.txt',
                'content': '\n'.join([
                    '04-12 10:00:05.100 ----- pid 100 -----',
                    'Cmd line: com.demo',
                    'main tid=1 Native: waiting because no focused window',
                    '04-12 10:01:05.100 ----- pid 100 -----',
                    'Cmd line: com.demo',
                    'main tid=1 Native: input dispatching timeout',
                ]),
            },
            'logcat': {
                'path': 'logcat.txt',
                'content': '\n'.join([
                    '04-12 10:00:05.050 E InputDispatcher no focused window for com.demo',
                    '04-12 10:01:05.050 E InputDispatcher Input dispatching timed out for com.demo',
                ]),
            },
        },
    }


class AiContextTests(unittest.TestCase):
    def test_anr_to_ai_script_builds_artifacts_from_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = Path(tmpdir) / 'bugreport.zip'
            out_dir = Path(tmpdir) / 'context'
            with zipfile.ZipFile(archive_path, 'w') as archive:
                archive.writestr('data/anr/traces.txt', '04-12 10:00:05.100 ----- pid 100 -----\nCmd line: com.demo\nmain tid=1 Native input dispatching timeout\n')
                archive.writestr('events/events.txt', '04-12 10:00:05.000 am_anr ANR in com.demo: Input dispatching timed out\n')
                archive.writestr('logs/logcat.txt', '04-12 10:00:05.050 E InputDispatcher Input dispatching timed out for com.demo\n')

            completed = subprocess.run(
                [sys.executable, 'scripts/anr_to_ai.py', str(archive_path), '--package', 'com.demo', '--out-dir', str(out_dir)],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                env=ENV,
            )

            index = json.loads((out_dir / 'index.json').read_text(encoding='utf-8'))
            group_dir = out_dir / index['groups'][0]['id']
            self.assertTrue((group_dir / 'anr_analysis.md').exists())
            self.assertTrue((group_dir / 'logcat.txt').exists())
            self.assertIn('1 ANR analysis file(s)', completed.stdout)
            self.assertIn(str(ROOT / 'docs/anr-ai-analysis-guide.md'), completed.stdout)

    def test_anr_to_ai_defaults_context_under_input_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'bugreport'
            (root / 'data/anr').mkdir(parents=True)
            (root / 'logs').mkdir(parents=True)
            (root / 'events').mkdir(parents=True)
            (root / 'data/anr/traces.txt').write_text(
                '04-12 11:00:03.100 main tid=1 input dispatching timeout\n',
                encoding='utf-8',
            )
            (root / 'logs/logcat.txt').write_text(
                '04-12 11:00:03.050 E InputDispatcher Input dispatching timed out\n',
                encoding='utf-8',
            )
            (root / 'events/event.log').write_text(
                '04-12 11:00:03.000 am_anr ANR in com.demo: Input dispatching timed out\n',
                encoding='utf-8',
            )

            completed = subprocess.run(
                [sys.executable, str(ROOT / 'scripts/anr_to_ai.py'), str(root)],
                cwd=tmpdir,
                check=True,
                capture_output=True,
                text=True,
                env=ENV,
            )

            out = root / 'anr_ai_context'
            self.assertTrue((out / 'index.json').exists())
            self.assertTrue((out / 'anr-20260412-110003-000' / 'anr_analysis.md').exists())
            self.assertIn(str(out), completed.stdout)
            self.assertFalse((Path(tmpdir) / 'anr_ai_context').exists())

    def test_staged_anr_skill_docs_exist_and_reference_routes_to_them(self) -> None:
        skills_dir = ROOT / 'skills'
        expected = {
            'anr-trace-analysis.md': 'name: anr-trace-analysis',
            'anr-eventlog-analysis.md': 'name: anr-eventlog-analysis',
            'anr-logcat-analysis.md': 'name: anr-logcat-analysis',
            'anr-analysis.md': 'name: anr-analysis',
        }

        for filename, marker in expected.items():
            content = (skills_dir / filename).read_text(encoding='utf-8')
            self.assertIn(marker, content)
            self.assertIn('固定步骤', content)

        reference = (skills_dir / 'anr-reference.md').read_text(encoding='utf-8')
        self.assertIn('固定 AI 分析工作流', reference)
        self.assertIn('Trace AI 分析', reference)
        self.assertIn('EventLog AI 分析', reference)
        self.assertIn('Logcat AI 分析', reference)
        self.assertIn('最终 ANR AI 分析', reference)
        for filename in expected:
            self.assertIn(filename, reference)

    def test_core_builds_side_effect_free_result_with_progress_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = build_ai_context(_multi_anr_package(), AiContextOptions(out_dir=tmpdir, anr_type='input_dispatching_timeout'))

            self.assertFalse((Path(tmpdir) / 'cache.md').exists())
            self.assertEqual(len(result.groups), 2)
            self.assertEqual(result.strategy['anrType'], 'input_dispatching_timeout')
            self.assertIn('## anr-20260412-100005-000', result.cache_markdown)
            self.assertIn('当前分析分支: `input_dispatching_timeout`', result.ai_prompt_markdown)
            steps = [event['step'] for event in result.events]
            self.assertIn('source_loaded', steps)
            self.assertIn('anr_type_selected', steps)
            self.assertIn('grouped', steps)
            self.assertIn('trace_filtered', steps)
            self.assertIn('event_log_filtered', steps)
            self.assertIn('logcat_filtered', steps)
            self.assertIn('completeness_checked', steps)
            self.assertIn('cache_rendered', steps)
            self.assertIn('prompt_generated', steps)
            self.assertEqual(result.summary()['artifactPaths'], {})

    def test_multi_anr_builds_independent_contexts_and_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            summary = build_ai_context_artifacts(_multi_anr_package(), AiContextOptions(out_dir=tmpdir, anr_type='input_dispatching_timeout'))
            index_json = json.loads(Path(tmpdir, 'index.json').read_text(encoding='utf-8'))
            first_dir = Path(tmpdir, 'anr-20260412-100005-000')
            second_dir = Path(tmpdir, 'anr-20260412-100105-000')
            first_analysis = (first_dir / 'anr_analysis.md').read_text(encoding='utf-8')
            second_analysis = (second_dir / 'anr_analysis.md').read_text(encoding='utf-8')
            first_logcat_path = first_dir / 'logcat.txt'
            second_logcat_path = second_dir / 'logcat.txt'
            first_logcat = first_logcat_path.read_text(encoding='utf-8')
            second_logcat = second_logcat_path.read_text(encoding='utf-8')
            first_logcat_exists = first_logcat_path.exists()
            second_logcat_exists = second_logcat_path.exists()
            first_dir_exists = first_dir.is_dir()
            second_dir_exists = second_dir.is_dir()
            top_cache_exists = Path(tmpdir, 'cache.md').exists()
            top_prompt_exists = Path(tmpdir, 'ai_prompt.md').exists()
            top_summary_exists = Path(tmpdir, 'summary.json').exists()

        self.assertEqual(summary['groupCount'], 2)
        self.assertFalse(top_cache_exists)
        self.assertFalse(top_prompt_exists)
        self.assertFalse(top_summary_exists)
        self.assertEqual(index_json['groupCount'], 2)
        self.assertEqual(index_json['groups'][0]['analysisSlots']['trace'], 'pending')
        self.assertFalse(index_json['groups'][0]['analysisComplete'])
        self.assertEqual(summary['groups'][0]['artifactPaths']['analysis'], str(first_dir / 'anr_analysis.md'))
        self.assertEqual(summary['groups'][0]['artifactPaths']['logcat'], str(first_dir / 'logcat.txt'))
        self.assertTrue(first_dir_exists)
        self.assertTrue(second_dir_exists)
        self.assertTrue(first_logcat_exists)
        self.assertTrue(second_logcat_exists)
        self.assertIn('04-12 10:00:05.050 E InputDispatcher no focused window for com.demo', first_logcat)
        self.assertIn('04-12 10:01:05.050 E InputDispatcher Input dispatching timed out for com.demo', second_logcat)
        self.assertNotIn('04-12 10:00:05.050 E InputDispatcher no focused window for com.demo', first_analysis)
        self.assertIn('过滤后的 Logcat 已单独保存为：`logcat.txt`', first_analysis)
        self.assertIn('## anr-20260412-100005-000', first_analysis)
        self.assertNotIn('## anr-20260412-100105-000', first_analysis)
        self.assertIn('## anr-20260412-100105-000', second_analysis)
        self.assertNotIn('## anr-20260412-100005-000', second_analysis)
        self.assertIn('### Trace', first_analysis)
        self.assertIn('### EventLog', first_analysis)
        self.assertIn('### Logcat', first_analysis)
        self.assertNotIn('### AnrManager Diagnostic Block', first_analysis)
        self.assertEqual(summary['strategy']['anrType'], 'input_dispatching_timeout')
        self.assertTrue(first_analysis.startswith('# ANR AI Context Cache'))
        self.assertIn('## 分析位置指南', first_analysis)
        self.assertIn('#### AI Analysis — Trace 堆栈', first_analysis)
        self.assertIn('#### AI Analysis — EventLog 事件日志', first_analysis)
        self.assertIn('#### AI Analysis — Logcat / AnrManager', first_analysis)
        self.assertIn('#### AI Analysis — 最终 ANR 综合分析', first_analysis)
        self.assertIn('docs/anr-ai-analysis-guide.md', first_analysis)
        self.assertIn('ANR type strategy: `input_dispatching_timeout`', first_analysis)
        self.assertNotIn('# AI Prompt: Android ANR Root Cause Analysis', first_analysis)
        self.assertNotIn('当前分析分支: `input_dispatching_timeout`', first_analysis)
        self.assertNotIn('## 类型特化关注点', first_analysis)
        self.assertNotIn('## 四阶段强制分析流程', first_analysis)
        self.assertNotIn('## 证据与分析', first_analysis)
        self.assertNotIn('## 内联分析位置', first_analysis)
        self.assertNotIn('# ANR Inline Analysis Workspace', first_analysis)
        guide = (ROOT / 'docs/anr-ai-analysis-guide.md').read_text(encoding='utf-8')
        self.assertIn('## 分析原则', guide)
        self.assertIn('## 四阶段分析顺序', guide)
        self.assertIn('## Final ANR 固定输出', guide)
        self.assertIn('### 死锁检测', guide)
        self.assertIn('CPU >90% processes', guide)
        self.assertIn('## Trace 证据分析', first_analysis)
        self.assertIn('## EventLog 证据分析', first_analysis)
        self.assertIn('## Logcat 与 AnrManager 证据分析', first_analysis)
        self.assertNotIn('"sourceAnalyses"', first_analysis)
        self.assertIn('#### AI Analysis — Trace 堆栈', first_analysis)
        self.assertIn('#### AI Analysis — EventLog 事件日志', first_analysis)
        self.assertIn('#### AI Analysis — Logcat / AnrManager', first_analysis)
        self.assertIn('#### AI Analysis — 最终 ANR 综合分析', first_analysis)
        self.assertIn('AI_ANALYSIS_SLOT:trace', first_analysis)
        self.assertIn('AI_ANALYSIS_SLOT:eventlog', first_analysis)
        self.assertIn('AI_ANALYSIS_SLOT:logcat-anrmanager', first_analysis)
        self.assertIn('AI_ANALYSIS_SLOT:final-anr', first_analysis)
        self.assertEqual(first_analysis.count('<!-- AI_ANALYSIS_SLOT:trace -->'), 1)
        self.assertEqual(first_analysis.count('<!-- AI_ANALYSIS_SLOT:eventlog -->'), 1)
        self.assertEqual(first_analysis.count('<!-- AI_ANALYSIS_SLOT:logcat-anrmanager -->'), 1)
        self.assertEqual(first_analysis.count('<!-- AI_ANALYSIS_SLOT:final-anr -->'), 1)
        evidence_analysis = first_analysis
        self.assertLess(evidence_analysis.index('#### AI Analysis — Trace 堆栈'), evidence_analysis.index('#### AI Analysis — EventLog 事件日志'))
        self.assertLess(evidence_analysis.index('#### AI Analysis — EventLog 事件日志'), evidence_analysis.index('#### AI Analysis — Logcat / AnrManager'))
        self.assertLess(evidence_analysis.index('#### AI Analysis — Logcat / AnrManager'), evidence_analysis.index('#### AI Analysis — 最终 ANR 综合分析'))
        self.assertIn('### Trace 堆栈', first_analysis)
        self.assertIn('NATIVE_POLL_BUT_BUSY', guide)
        self.assertIn('MAIN_BINDER_WAIT_REPLY', guide)


    def test_artifact_builder_renders_each_group_without_monolithic_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            import anr_evidence.ai_context as ai_context_module
            with patch('anr_evidence.ai_context._render_cache_markdown', wraps=ai_context_module._render_cache_markdown) as render_cache:
                build_ai_context_artifacts(_multi_anr_package(), AiContextOptions(out_dir=tmpdir, anr_type='input_dispatching_timeout'))

        rendered_group_counts = [len(call.args[1]) for call in render_cache.call_args_list]
        self.assertTrue(rendered_group_counts)
        self.assertTrue(all(count == 1 for count in rendered_group_counts))

    def test_regeneration_preserves_filled_final_analysis_with_headings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            summary = build_ai_context_artifacts(_multi_anr_package(), AiContextOptions(out_dir=tmpdir, anr_type='input_dispatching_timeout'))
            analysis_path = Path(summary['groups'][0]['artifactPaths']['analysis'])
            content = analysis_path.read_text(encoding='utf-8')
            marker = '<!-- AI_ANALYSIS_SLOT:final-anr -->'
            start = content.index(marker) + len(marker)
            filled_final = '\n## Timeline\n- Filled final analysis survives regeneration.\n\n```json\n{"finalJudgment": false}\n```\n'
            analysis_path.write_text(content[:start] + filled_final, encoding='utf-8')

            regenerated = build_ai_context_artifacts(_multi_anr_package(), AiContextOptions(out_dir=tmpdir, anr_type='input_dispatching_timeout'))
            regenerated_content = analysis_path.read_text(encoding='utf-8')

        self.assertIn('## Timeline\n- Filled final analysis survives regeneration.', regenerated_content)
        self.assertEqual(regenerated['groups'][0]['analysisSlots']['final-anr'], 'filled')
        self.assertEqual(regenerated['groups'][0]['analysisSlots']['trace'], 'pending')
        self.assertFalse(regenerated['groups'][0]['analysisComplete'])

    def test_trace_cache_does_not_leak_per_group_fusion_evidence(self) -> None:
        package = {
            'package_id': 'AICTX-FUSION-LEAK',
            'sources': {
                'event_log': {
                    'path': 'events.log',
                    'content': '\n'.join([
                        '04-12 10:00:05.000 am_anr ANR in com.demo first',
                        '04-12 10:01:05.000 am_anr ANR in com.demo second',
                    ]),
                },
                # One trace section means both ANR anchors share the same
                # cached preprocessed result; only the second logcat window has
                # corroborating slow-binder evidence.
                'trace': {
                    'path': 'trace.txt',
                    'content': '\n'.join([
                        '04-12 10:00:05.100 ----- pid 100 -----',
                        'Cmd line: com.demo',
                        '"main" prio=5 tid=1 Native',
                        '  | sysTid=100',
                        '  | state=S schedstat=( 50000000 800000000 200 ) utm=2 stm=3 core=0 HZ=100',
                        '  native: #00 pc 0  /system/lib/libbinder.so (android::IPCThreadState::waitForResponse+8)',
                        '  at android.os.BinderProxy.transact(BinderProxy.java:550)',
                    ]),
                },
                'logcat': {
                    'path': 'logcat.txt',
                    'content': '\n'.join([
                        '04-12 10:00:05.000 E InputDispatcher first ANR for com.demo',
                        '04-12 10:01:04.900 W BinderProxy Slow operation: slow binder transaction took 5000ms for com.demo',
                        '04-12 10:01:05.000 E InputDispatcher second ANR for com.demo',
                    ]),
                },
            },
        }

        result = build_ai_context(package, AiContextOptions(package_name='com.demo', logcat_before_seconds=5, logcat_after_seconds=1))
        first_hint = next(h for h in result.groups[0]['trace']['traceHints'] if h['id'] == 'MAIN_BINDER_WAIT_REPLY')
        second_hint = next(h for h in result.groups[1]['trace']['traceHints'] if h['id'] == 'MAIN_BINDER_WAIT_REPLY')

        self.assertEqual(first_hint['confidence'], 'strong')
        self.assertNotIn('confidencePromotedFrom', first_hint)
        self.assertFalse(first_hint.get('corroboratingEvidence'))
        self.assertEqual(second_hint['confidence'], 'critical')
        self.assertEqual(second_hint['confidencePromotedFrom'], 'strong')
        self.assertEqual(second_hint['corroboratingEvidence'][0]['source'], 'logcat')

    def test_trace_selection_prefers_matching_cmdline_block_for_package(self) -> None:
        package = {
            'package_id': 'AICTX-TRACE-PACKAGE',
            'sources': {
                'event_log': {
                    'path': 'events.log',
                    'content': '06-09 12:20:19.348  1302  9360 I am_anr  : [0,2703,com.demo,0,Input dispatching timed out (Application does not have a focused window).]',
                },
                'trace': {
                    'path': 'anr_2026-06-09-12-20-19-397',
                    'content': '\n'.join([
                        '----- pid 1198 at 2026-06-09 12:16:58.017274309+0800 -----',
                        'Cmd line: /system/bin/cameraserver',
                        'DALVIK THREADS (1):',
                        '"binder:1198_2" prio=5 tid=3 Waiting',
                        '  - waiting to lock <0x1> held by thread 4',
                        '  native: #00 pc 0 /system/lib64/libbinder.so (binder_thread_read+8)',
                        'DumpLatencyMs: 1.0',
                        '----- pid 2703 at 2026-06-09 12:20:19.324169628+0800 -----',
                        'Cmd line: com.demo',
                        "Build fingerprint: 'TCL/T852K_EEA/Avatar_Pro_NP:16/BP2A.250605.031.A3/D541:user/release-keys'",
                        "ABI: 'arm64'",
                        'DALVIK THREADS (2):',
                        '"main" prio=5 tid=1 Native',
                        '  | group="main" sCount=1 ucsCount=0 flags=1 obj=0x73297478 self=0xb4000077ee3e6010',
                        '  | sysTid=2703 nice=-10 cgrp=top-app sched=0/0 handle=0x7a9520a090',
                        '  native: #00 pc 000dfcc8  /apex/com.android.runtime/lib64/bionic/libc.so (__epoll_pwait+8)',
                        '  at android.os.MessageQueue.nativePollOnce(Native method)',
                        '  at android.os.Looper.loop(Looper.java:371)',
                        'DumpLatencyMs: 9.86069',
                        '"worker" prio=5 tid=2 Runnable',
                        '  at com.demo.Worker.run(Worker.java:1)',
                    ]),
                },
            },
        }

        result = build_ai_context(package, AiContextOptions(package_name='com.demo'))

        lines = result.groups[0]['trace']['lines']
        joined = '\n'.join(lines)
        self.assertIn('----- pid 2703 at 2026-06-09 12:20:19.324169628+0800 -----', joined)
        self.assertIn('Cmd line: com.demo', joined)
        self.assertIn('DumpLatencyMs: 9.86069', joined)
        self.assertNotIn('Cmd line: /system/bin/cameraserver', joined)
        self.assertNotIn('"worker" prio=5 tid=2 Runnable', joined)
        self.assertEqual(result.groups[0]['trace']['metadata']['processName'], 'com.demo')

    def test_package_filter_requires_matching_eventlog_am_anr(self) -> None:
        package = {
            'package_id': 'AICTX-PKG-MISS',
            'sources': {
                'event_log': {
                    'path': 'events.log',
                    'content': '04-12 10:00:05.000 am_anr ANR in com.other: Input dispatching timed out',
                },
                'trace': {
                    'path': 'traces.txt',
                    'content': '04-12 10:00:05.100 Cmd line: com.demo\nmain tid=1 input dispatching timeout',
                },
                'logcat': {
                    'path': 'logcat.txt',
                    'content': '04-12 10:00:05.050 E InputDispatcher timeout for com.demo',
                },
            },
        }

        result = build_ai_context(package, AiContextOptions(package_name='com.demo'))

        self.assertEqual(result.groups[0]['id'], 'anr-unanchored-20260412-100005-100')
        self.assertIsNone(result.groups[0]['anchor'])
        self.assertEqual(result.groups[0]['inferredAnrTime'], '04-12 10:00:05.100')
        self.assertEqual(result.groups[0]['inferredAnrTimeSource'], 'trace')
        self.assertFalse(result.groups[0]['fallbackUsed'])
        self.assertEqual(result.groups[0]['eventLog']['warnings'][0]['code'], 'target-am-anr-not-found')

        with tempfile.TemporaryDirectory() as tmpdir:
            summary = build_ai_context_artifacts(package, AiContextOptions(out_dir=tmpdir, package_name='com.demo'))
            self.assertEqual(summary['groups'][0]['id'], 'anr-unanchored-20260412-100005-100')
            analysis_path = Path(tmpdir) / 'anr-unanchored-20260412-100005-100' / 'anr_analysis.md'
            self.assertTrue(analysis_path.exists())
            analysis = analysis_path.read_text(encoding='utf-8')
            self.assertIn('## anr-unanchored-20260412-100005-100', analysis)
            self.assertIn('Inferred ANR time: `04-12 10:00:05.100`', analysis)

    def test_no_package_filter_infers_package_from_eventlog_anchor_for_logcat(self) -> None:
        package = {
            'package_id': 'AICTX-PKG-INFER',
            'sources': {
                'event_log': {
                    'path': 'events.log',
                    'content': '04-12 10:00:05.000 I am_anr: [0,100,com.demo,1,Input dispatching timed out]',
                },
                'trace': {
                    'path': 'traces.txt',
                    'content': '04-12 10:00:05.100 Cmd line: com.demo\nmain tid=1 input dispatching timeout',
                },
                'logcat': {
                    'path': 'logcat.txt',
                    'content': '\n'.join([
                        '04-12 10:00:05.001 I/AnrManager( 1377): ANR in com.other',
                        '04-12 10:00:05.002 I/AnrManager( 1377): dumpAnrDebugInfo end: ProcessRecord{200:com.other/u0a2}',
                        '04-12 10:00:05.003 I/AnrManager( 1377): ANR in com.demo',
                        '04-12 10:00:05.004 I/AnrManager( 1377): dumpAnrDebugInfo end: ProcessRecord{100:com.demo/u0a1}',
                    ]),
                },
            },
        }

        result = build_ai_context(package, AiContextOptions())
        group = result.groups[0]

        self.assertEqual(group['anchor']['packageName'], 'com.demo')
        self.assertIn('com.demo', group['anrManager']['anchor'])
        self.assertEqual(group['anrManager']['warnings'], [])

    def test_trace_structured_metadata_exposes_full_manual_reading_fields(self) -> None:
        package = {
            'package_id': 'AICTX-TRACE-DETAIL',
            'sources': {
                'event_log': {
                    'path': 'events.log',
                    'content': '04-12 10:00:05.000 am_anr ANR in com.demo Input dispatching timed out',
                },
                'trace': {
                    'path': 'data/anr/traces.txt',
                    'content': '\n'.join([
                        '04-12 10:00:05.100 ----- pid 100 -----',
                        'Cmd line: com.demo',
                        '"main" prio=5 tid=1 Blocked',
                        '  | group="main" sCount=1 ucsCount=0 flags=1 obj=0x739661c8 self=0xb40000786863bc00',
                        '  | sysTid=100 nice=-4 cgrp=foreground sched=1073741824/0 handle=0x792b0f24f8',
                        '  | state=S schedstat=( 3697190487 8713072491 186 ) utm=238 stm=131 core=7 HZ=100',
                        '  | held mutexes= "mutator lock"(shared held)',
                        '  at com.demo.MainActivity.onClick(MainActivity.java:42)',
                        '  - waiting to lock <0x0da8cd6b> (a java.lang.Object) held by thread 19',
                        '"worker" prio=5 tid=19 TimedWaiting',
                        '  | group="main" sCount=0 dsCount=0 obj=0x14200cc0 self=0xb4000078384b9400',
                        '  | sysTid=119 nice=0 cgrp=foreground sched=0/0 handle=0x77b961bcb0',
                        '  | state=S schedstat=( 1000 2000 3 ) utm=1 stm=2 core=0 HZ=100',
                        '  | held mutexes=',
                        '  - locked <0x0da8cd6b> (a java.lang.Object)',
                        '  at java.lang.Thread.sleep(Native method)',
                    ]),
                },
                'logcat': {
                    'path': 'logcat.txt',
                    'content': '04-12 10:00:05.050 E InputDispatcher Application is not responding: com.demo',
                },
            },
        }

        result = build_ai_context(package, AiContextOptions(package_name='com.demo'))
        metadata = result.groups[0]['trace']['metadata']
        cache = result.cache_markdown

        self.assertEqual(metadata['sourcePath'], 'data/anr/traces.txt')
        self.assertEqual(metadata['selectedSectionTimestamp'], '04-12 10:00:05.100')
        self.assertEqual(metadata['selectedSectionDeltaFromAnchorMs'], 100)
        self.assertEqual(metadata['mainThread']['threadName'], 'main')
        self.assertEqual(metadata['mainThread']['prio'], '5')
        self.assertEqual(metadata['mainThread']['artThreadState'], 'Blocked')
        self.assertEqual(metadata['mainThread']['javaThreadState'], 'BLOCKED')
        self.assertEqual(metadata['mainThread']['linuxState'], 'S')
        self.assertEqual(metadata['mainThread']['schedstatParsed']['runNs'], 3697190487)
        self.assertEqual(metadata['mainThread']['utm'], '238')
        self.assertEqual(metadata['mainThread']['stm'], '131')
        self.assertEqual(metadata['mainThread']['lockOwnerTid'], '19')
        self.assertEqual(metadata['ownerThread']['threadName'], 'worker')
        self.assertEqual(metadata['threadSummary']['ownerThreadTid'], '19')
        self.assertNotIn('#### Structured Metadata', cache)
        self.assertNotIn('"schedstatParsed"', cache)
        self.assertNotIn('"ownerThread"', cache)

    def test_unknown_strategy_uses_generic_windows_and_prompt_branch(self) -> None:
        package = {
            'package_id': 'AICTX-UNKNOWN',
            'sources': {
                'event_log': {
                    'path': 'events.log',
                    'content': '\n'.join([
                        '04-12 12:00:10.000 am_proc_died com.demo died',
                        '04-12 12:00:40.000 am_anr ANR in com.demo service timeout',
                    ]),
                },
                'trace': {'path': 'traces.txt', 'content': '04-12 12:00:40.100 main tid=1 waiting on service'},
                'logcat': {'path': 'logcat.txt', 'content': '04-12 12:00:35.000 E ActivityManager service timeout for com.demo'},
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            summary = build_ai_context_artifacts(package, AiContextOptions(out_dir=tmpdir, anr_type='future_service_timeout'))
            group_dir = Path(tmpdir, 'anr-20260412-120040-000')
            analysis = (group_dir / 'anr_analysis.md').read_text(encoding='utf-8')
            group_dir_exists = group_dir.is_dir()

        self.assertEqual(summary['strategy']['anrType'], 'unknown')
        self.assertTrue(group_dir_exists)
        self.assertEqual(summary['strategy']['eventBeforeSeconds'], 30)
        self.assertIn('am_proc_died', analysis)
        self.assertTrue(analysis.startswith('# ANR AI Context Cache'))
        self.assertIn('ANR type strategy: `unknown`', analysis)
        self.assertNotIn('当前分析分支: `unknown`', analysis)
        guide = (ROOT / 'docs/anr-ai-analysis-guide.md').read_text(encoding='utf-8')
        self.assertIn('不要把 `input_dispatching_timeout` 的结论套用于其他类型', guide)
        self.assertIn('#### AI Analysis — Trace 堆栈', analysis)
        self.assertIn('#### AI Analysis — 最终 ANR 综合分析', analysis)

    def test_event_log_cache_keeps_documented_tags_without_package_filtering_context(self) -> None:
        package = {
            'package_id': 'AICTX-EVENT-WINDOW',
            'sources': {
                'event_log': {
                    'path': 'events.log',
                    'content': '\n'.join([
                        '04-12 10:00:48.000 wm_task_created 123',
                        '04-12 10:00:49.000 wm_pause_activity com.demo',
                        '04-12 10:00:50.000 am_proc_start com.next',
                        '04-12 10:00:51.000 random_noise com.demo',
                        '04-12 10:01:00.000 am_anr ANR in com.demo',
                    ]),
                },
                'trace': {'path': 'trace.txt', 'content': '04-12 10:01:00.100 Cmd line: com.demo\nmain tid=1 Native'},
                'logcat': {'path': 'logcat.txt', 'content': '04-12 10:01:00.050 E InputDispatcher no focused window for com.demo'},
            },
        }

        result = build_ai_context(package, AiContextOptions(package_name='com.demo', anr_type='no_focus_window'))
        event_lines = result.groups[0]['eventLog']['lines']

        self.assertIn('04-12 10:00:48.000 wm_task_created 123', event_lines)
        self.assertIn('04-12 10:00:50.000 am_proc_start com.next', event_lines)
        self.assertNotIn('04-12 10:00:51.000 random_noise com.demo', event_lines)
        self.assertIn('04-12 10:01:00.000 am_anr ANR in com.demo', event_lines)

    def test_multi_anr_contexts_keep_distinct_anrmanager_blocks(self) -> None:
        package = {
            'package_id': 'AICTX-MULTI-ANRMANAGER',
            'sources': {
                'event_log': {
                    'path': 'events.log',
                    'content': '\n'.join([
                        '04-12 10:00:05.000 am_anr ANR in com.demo first',
                        '04-12 10:01:05.000 am_anr ANR in com.demo second',
                    ]),
                },
                'trace': {'path': 'trace.txt', 'content': '04-12 10:00:05.100 Cmd line: com.demo\n04-12 10:01:05.100 Cmd line: com.demo\n'},
                'logcat': {
                    'path': 'logcat.txt',
                    'content': '\n'.join([
                        '04-12 10:00:04.900 I/AnrManager( 1377): startAnrDump',
                        '04-12 10:00:05.000 I/AnrManager( 1377): ANR in com.demo',
                        '04-12 10:00:05.000 I/AnrManager( 1377): Reason: first reason',
                        '04-12 10:00:05.002 I/AnrManager( 1377): addErrorToDropBox app = ProcessRecord{100:com.demo/u0a1} mTracesFile = /data/anr/anr_first',
                        '04-12 10:00:05.003 I/AnrManager( 1377):  controller = null',
                        '04-12 10:01:04.900 I/AnrManager( 1377): startAnrDump',
                        '04-12 10:01:05.000 I/AnrManager( 1377): ANR in com.demo',
                        '04-12 10:01:05.000 I/AnrManager( 1377): Reason: second reason',
                        '04-12 10:01:05.002 I/AnrManager( 1377): addErrorToDropBox app = ProcessRecord{100:com.demo/u0a1} mTracesFile = /data/anr/anr_second',
                        '04-12 10:01:05.003 I/AnrManager( 1377):  controller = null',
                    ]),
                },
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            build_ai_context_artifacts(package, AiContextOptions(out_dir=tmpdir, package_name='com.demo'))
            first_analysis = Path(tmpdir, 'anr-20260412-100005-000', 'anr_analysis.md').read_text(encoding='utf-8')
            second_analysis = Path(tmpdir, 'anr-20260412-100105-000', 'anr_analysis.md').read_text(encoding='utf-8')

        self.assertIn('first reason', first_analysis)
        self.assertNotIn('second reason', first_analysis)
        self.assertIn('second reason', second_analysis)
        self.assertNotIn('first reason', second_analysis)

    def test_logcat_keeps_raw_twelve_seconds_before_anrmanager_block(self) -> None:
        package = {
            'package_id': 'AICTX-ANRMANAGER-PRE-CONTEXT',
            'sources': {
                'event_log': {
                    'path': 'events.log',
                    'content': '04-12 10:00:57.460 am_anr ANR in com.demo Input dispatching timed out',
                },
                'trace': {'path': 'trace.txt', 'content': '04-12 10:00:57.500 Cmd line: com.demo\nmain tid=1 Native'},
                'logcat': {
                    'path': 'logcat.txt',
                    'content': '\n'.join([
                        '04-12 10:00:44.999 D VendorNoise older than AnrManager pre-context window',
                        '04-12 10:00:45.463 D VendorNoise surface transition before dump',
                        '04-12 10:00:56.999 I LauncherShell recents animation callback before dump',
                        '04-12 10:00:57.463 I/AnrManager( 1377): startAnrDump',
                        '04-12 10:00:57.500 I/AnrManager( 1377): ANR in com.demo',
                        '04-12 10:00:57.501 I/AnrManager( 1377): Reason: Input dispatching timed out',
                        '04-12 10:00:57.502 I/AnrManager( 1377): dumpAnrDebugInfo end: AnrDumpRecord{ Input dispatching timed out ProcessRecord{100:com.demo/u0a1} }',
                        '04-12 10:00:57.503 I/AnrManager( 1377):  controller = null',
                    ]),
                },
            },
        }

        result = build_ai_context(package, AiContextOptions(package_name='com.demo', anr_type='input_dispatching_timeout'))
        logcat = result.groups[0]['logcat']
        logcat_lines = logcat['lines']

        self.assertIn('04-12 10:00:45.463 D VendorNoise surface transition before dump', logcat_lines)
        self.assertIn('04-12 10:00:56.999 I LauncherShell recents animation callback before dump', logcat_lines)
        self.assertNotIn('04-12 10:00:44.999 D VendorNoise older than AnrManager pre-context window', logcat_lines)
        self.assertEqual(logcat['metadata']['anrManagerPreContextSeconds'], 12)
        self.assertEqual(logcat['metadata']['anrManagerPreContextAnchor'], '04-12 10:00:57.463')
        self.assertEqual(logcat['metadata']['anrManagerPreContextRetainedLineCount'], 2)

    def test_empty_logcat_anchor_window_is_not_replaced_with_stale_fallback(self) -> None:
        package = {
            'package_id': 'AICTX-STALE-LOGCAT-FALLBACK',
            'sources': {
                'event_log': {
                    'path': 'events.log',
                    'content': '\n'.join([
                        '04-12 10:00:05.000 am_anr ANR in com.demo first',
                        '04-12 15:14:03.980 am_anr ANR in com.demo second',
                    ]),
                },
                'trace': {
                    'path': 'trace.txt',
                    'content': '\n'.join([
                        '04-12 10:00:05.100 ----- pid 100 -----',
                        'Cmd line: com.demo',
                        '"main" prio=5 tid=1 Native',
                        '04-12 15:14:04.000 ----- pid 101 -----',
                        'Cmd line: com.demo',
                        '"main" prio=5 tid=1 Native',
                    ]),
                },
                'logcat': {
                    'path': 'logcat.txt',
                    'content': '\n'.join([
                        '04-12 10:00:05.050 E InputDispatcher first ANR for com.demo',
                        '04-12 10:00:05.060 I/AnrManager( 1377): ANR in com.demo',
                    ]),
                },
            },
        }

        result = build_ai_context(package, AiContextOptions(package_name='com.demo'))

        first, second = result.groups
        self.assertIn('04-12 10:00:05.050 E InputDispatcher first ANR for com.demo', first['logcat']['lines'])
        self.assertEqual(second['logcat']['warnings'][0]['code'], 'empty-anchor-window')
        self.assertEqual(second['logcat']['lines'], [])
        self.assertFalse(second['completeness']['complete'])
        self.assertIn('logcat', second['completeness']['emptyFilteredSources'])

    def test_anrmanager_summary_surfaces_pressure_and_meminfo_follows_all_over_90_processes(self) -> None:
        package = {
            'package_id': 'AICTX-ANRMANAGER-LOAD',
            'sources': {
                'event_log': {
                    'path': 'events.log',
                    'content': '06-09 13:12:54.321 am_anr ANR in com.tcl.android.launcher Input dispatching timed out',
                },
                'trace': {
                    'path': 'trace.txt',
                    'content': '----- pid 9373 at 2026-06-09 13:12:54.300000000+0800 -----\nCmd line: com.tcl.android.launcher\n"main" prio=5 tid=1 Native\nDumpLatencyMs: 1.0',
                },
                'logcat': {
                    'path': 'logcat.txt',
                    'content': '\n'.join([
                        '06-09 13:12:54.319  1302  8310 I AnrManager: dumpStackTraces end!',
                        '06-09 13:12:54.321  1302  8310 I AnrManager: ANR in com.tcl.android.launcher, time=66595997',
                        '06-09 13:12:54.321  1302  8310 I AnrManager: Reason: Input dispatching timed out (37e4ea9 Taskbar is not responding. Waited 8000ms for MotionEvent).',
                        '06-09 13:12:54.321  1302  8310 I AnrManager: Load: 77.99 / 56.92 / 41.64',
                        '06-09 13:12:54.321  1302  8310 I AnrManager: ----- Output from /proc/pressure/memory -----',
                        '06-09 13:12:54.321  1302  8310 I AnrManager: some avg10=6.85 avg60=6.61 avg300=3.64 total=1851111472',
                        '06-09 13:12:54.321  1302  8310 I AnrManager: full avg10=4.21 avg60=3.82 avg300=2.02 total=1186497918',
                        '06-09 13:12:54.321  1302  8310 I AnrManager: ----- End output from /proc/pressure/memory -----',
                        '06-09 13:12:54.321  1302  8310 I AnrManager: ----- Output from /proc/pressure/cpu -----',
                        '06-09 13:12:54.321  1302  8310 I AnrManager: some avg10=23.57 avg60=31.69 avg300=34.72 total=13421719822',
                        '06-09 13:12:54.321  1302  8310 I AnrManager: full avg10=0.00 avg60=0.00 avg300=0.00 total=0',
                        '06-09 13:12:54.321  1302  8310 I AnrManager: ----- End output from /proc/pressure/cpu -----',
                        '06-09 13:12:54.321  1302  8310 I AnrManager: ----- Output from /proc/pressure/io -----',
                        '06-09 13:12:54.321  1302  8310 I AnrManager: some avg10=66.60 avg60=34.14 avg300=15.76 total=4664085786',
                        '06-09 13:12:54.321  1302  8310 I AnrManager: full avg10=46.89 avg60=21.10 avg300=8.00 total=2114058611',
                        '06-09 13:12:54.321  1302  8310 I AnrManager: ----- End output from /proc/pressure/io -----',
                        '06-09 13:12:54.321  1302  8310 I AnrManager:   99% 1001/com.heavy.one: 90% user + 9% kernel',
                        '06-09 13:12:54.321  1302  8310 I AnrManager:   98% 1002/com.heavy.two: 88% user + 10% kernel',
                        '06-09 13:12:54.321  1302  8310 I AnrManager:   97% 1003/com.heavy.three: 87% user + 10% kernel',
                        '06-09 13:12:54.321  1302  8310 I AnrManager:   96% 1004/com.heavy.four: 86% user + 10% kernel',
                        '06-09 13:12:54.321  1302  8310 I AnrManager:   95% 1005/com.heavy.five: 85% user + 10% kernel',
                        '06-09 13:12:54.321  1302  8310 I AnrManager:   94% 1006/com.heavy.sixth: 84% user + 10% kernel',
                        '06-09 13:12:54.321  1302  8310 I AnrManager:   93% 9373/com.tcl.android.launcher: 60% user + 33% kernel / faults: 1528 minor 35 major',
                        '06-09 13:12:54.321  1302  8310 I AnrManager: 99% TOTAL: 52% user + 42% kernel + 0.7% iowait + 3.1% irq + 1.2% softirq',
                        '06-09 13:12:54.321  1302  8310 I AnrManager: dumpAnrDebugInfo end: AnrDumpRecord{ Input dispatching timed out ProcessRecord{4491c11 9373:com.tcl.android.launcher/u0a89} IsCompleted:true }',
                    ]),
                },
                'meminfo': {
                    'path': 'System_log/meminfo.txt',
                    'content': '\n'.join([
                        '================================',
                        '2026-06-09 13:12:50',
                        '================================',
                        'Applications Memory Usage (in Kilobytes):',
                        'Total RSS by OOM adjustment:',
                        '    900,000K: Foreground',
                        '        160,000K: com.heavy.one (pid 1001)',
                        '        150,000K: com.heavy.two (pid 1002)',
                        '        140,000K: com.heavy.three (pid 1003)',
                        '        130,000K: com.heavy.four (pid 1004)',
                        '        120,000K: com.heavy.five (pid 1005)',
                        '         50,000K: com.heavy.sixth (pid 1006)',
                        '         40,000K: com.tcl.android.launcher (pid 9373)',
                        'Total PSS by OOM adjustment:',
                        '    700,000K: Foreground',
                        '        130,000K: com.heavy.one (pid 1001)',
                        '        120,000K: com.heavy.two (pid 1002)',
                        '        110,000K: com.heavy.three (pid 1003)',
                        '        100,000K: com.heavy.four (pid 1004)',
                        '         90,000K: com.heavy.five (pid 1005)',
                        '         30,000K: com.heavy.sixth (pid 1006)',
                        '         20,000K: com.tcl.android.launcher (pid 9373)',
                        'Total RAM: 1,000,000K (status low)',
                        ' Free RAM: 100,000K',
                        ' Used RAM: 900,000K',
                        ' Lost RAM: 0K',
                    ]),
                },
            },
        }

        result = build_ai_context(package, AiContextOptions(package_name='com.tcl.android.launcher', anr_type='input_dispatching_timeout'))
        rendered = result.cache_markdown

        self.assertIn('- Load: `77.99 / 56.92 / 41.64`', rendered)
        self.assertIn('- PSI cpu.some: avg10=`23.57`', rendered)
        self.assertIn('- PSI io.some: avg10=`66.6`', rendered)
        self.assertIn('- CPU >90% processes:', rendered)
        self.assertIn('`94.0%` pid=`1006` `com.heavy.sixth`', rendered)
        high_mem_section = rendered[rendered.index('### AnrManager top CPU process memory'):]
        self.assertIn('com.heavy.sixth', high_mem_section)
        self.assertIn('com.tcl.android.launcher', high_mem_section)

    def test_cli_build_ai_context_from_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'bugreport'
            out = Path(tmpdir) / 'out'
            (root / 'data/anr').mkdir(parents=True)
            (root / 'logs').mkdir(parents=True)
            (root / 'events').mkdir(parents=True)
            (root / 'data/anr/traces.txt').write_text('04-12 11:00:03.100 main tid=1 input dispatching timeout\n', encoding='utf-8')
            (root / 'logs/logcat.txt').write_text('04-12 11:00:03.050 E InputDispatcher Input dispatching timed out\n', encoding='utf-8')
            (root / 'events/event.log').write_text('04-12 11:00:03.000 am_anr ANR in com.demo: Input dispatching timed out\n', encoding='utf-8')

            completed = subprocess.run(
                [sys.executable, '-m', 'anr_evidence', '--build-ai-context', '--out-dir', str(out), str(root)],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                env=ENV,
            )

            group_dir = out / 'anr-20260412-110003-000'
            self.assertTrue((out / 'index.json').exists())
            self.assertTrue((group_dir / 'anr_analysis.md').exists())
            self.assertTrue((group_dir / 'logcat.txt').exists())
            self.assertFalse((group_dir / 'cache.md').exists())
            self.assertFalse((group_dir / 'ai_prompt.md').exists())
            self.assertFalse((group_dir / 'analysis.md').exists())
            self.assertFalse((group_dir / 'summary.json').exists())
            self.assertFalse((out / 'cache.md').exists())
            self.assertIn('"groupCount": 1', completed.stdout)
            analysis_text = (group_dir / 'anr_analysis.md').read_text(encoding='utf-8')
            self.assertIn('Input dispatching timed out', analysis_text)
            self.assertIn('logcat.txt', analysis_text)
            self.assertIn('04-12 11:00:03.050 E InputDispatcher Input dispatching timed out', (group_dir / 'logcat.txt').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
