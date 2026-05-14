from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
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
        self.assertTrue(first_dir_exists)
        self.assertTrue(second_dir_exists)
        self.assertIn('## anr-20260412-100005-000', first_analysis)
        self.assertNotIn('## anr-20260412-100105-000', first_analysis)
        self.assertIn('## anr-20260412-100105-000', second_analysis)
        self.assertNotIn('## anr-20260412-100005-000', second_analysis)
        self.assertIn('### Trace', first_analysis)
        self.assertIn('### EventLog', first_analysis)
        self.assertIn('### Logcat', first_analysis)
        self.assertNotIn('### AnrManager Diagnostic Block', first_analysis)
        self.assertEqual(summary['strategy']['anrType'], 'input_dispatching_timeout')
        self.assertIn('ANR type strategy: `input_dispatching_timeout`', first_analysis)
        self.assertIn('AI Prompt: Android ANR Root Cause Analysis', first_analysis)
        self.assertIn('当前分析分支: `input_dispatching_timeout`', first_analysis)
        self.assertIn('## 类型特化关注点', first_analysis)
        self.assertIn('## 四阶段强制分析流程', first_analysis)
        self.assertIn('`anr-trace-analysis`', first_analysis)
        self.assertIn('`anr-eventlog-analysis`', first_analysis)
        self.assertIn('`anr-logcat-analysis`', first_analysis)
        self.assertIn('`anr-analysis`', first_analysis)
        self.assertIn('### Trace 分析要求', first_analysis)
        self.assertIn('### EventLog 分析要求', first_analysis)
        self.assertIn('### Logcat 与 AnrManager 分析要求', first_analysis)
        self.assertIn('### Final ANR 固定步骤要求', first_analysis)
        self.assertIn('综合分析必须写回当前 `anr_analysis.md` 的 `#### AI Analysis — 最终 ANR 综合分析` 分析位', first_analysis)
        self.assertIn('`## 综合分析结论`', first_analysis)
        self.assertIn('不得只在聊天回复中输出', first_analysis)
        self.assertIn('先看 `CPU TOTAL`/`iowait` 判断整体 CPU 或 IO 是否高', first_analysis)
        self.assertIn('Total整体负载/IO → 目标包 Top 负载 → 高负载进程内存证据', first_analysis)
        self.assertIn('### Meminfo 目标/高负载跟进', first_analysis)
        self.assertIn('## Trace 证据分析', first_analysis)
        self.assertIn('## EventLog 证据分析', first_analysis)
        self.assertIn('## Logcat 与 AnrManager 证据分析', first_analysis)
        self.assertIn('"sourceAnalyses"', first_analysis)
        self.assertIn('## 证据与分析', first_analysis)
        self.assertIn('## 内联分析位置', first_analysis)
        self.assertNotIn('# ANR Inline Analysis Workspace', first_analysis)
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
        evidence_analysis = first_analysis[first_analysis.index('## 证据与分析'):]
        self.assertLess(evidence_analysis.index('#### AI Analysis — Trace 堆栈'), evidence_analysis.index('#### AI Analysis — EventLog 事件日志'))
        self.assertLess(evidence_analysis.index('#### AI Analysis — EventLog 事件日志'), evidence_analysis.index('#### AI Analysis — Logcat / AnrManager'))
        self.assertLess(evidence_analysis.index('#### AI Analysis — Logcat / AnrManager'), evidence_analysis.index('#### AI Analysis — 最终 ANR 综合分析'))
        self.assertIn('### Trace 字段解读检查表', first_analysis)
        self.assertIn('schedstat` 三元组按 runNs/waitNs/timeSlices 解读', first_analysis)
        self.assertIn('tid` 是 ART 线程标识，`sysTid` 才是 Linux 线程号', first_analysis)

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
        self.assertIn('当前分析分支: `unknown`', analysis)
        self.assertIn('不要把 input timeout 结论套用到其他类型', analysis)
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
            self.assertFalse((group_dir / 'cache.md').exists())
            self.assertFalse((group_dir / 'ai_prompt.md').exists())
            self.assertFalse((group_dir / 'analysis.md').exists())
            self.assertFalse((group_dir / 'summary.json').exists())
            self.assertFalse((out / 'cache.md').exists())
            self.assertIn('"groupCount": 1', completed.stdout)
            self.assertIn('Input dispatching timed out', (group_dir / 'anr_analysis.md').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
