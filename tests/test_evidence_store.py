"""Original-byte provenance and optional Agent citation acceptance regressions."""
import base64
import copy
from datetime import datetime
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from anr_evidence import AiContextOptions, build_ai_context, build_ai_context_artifacts
from anr_evidence.ai_agent import AgentConfig, run_ai_agent_analysis
from anr_evidence.evidence_slice import (
    attach_store_references, build_evidence_slices, read_ess_jsonl,
)
from anr_evidence.evidence_store import EvidenceStore, EvidenceStoreError
from anr_evidence.evidence_store_export import export_evidence_store
from anr_evidence.loaders import load_package_from_path
from anr_evidence.loaders.package import build_package_from_entries
from tests.test_agent_safety import config, report, response


LINE = '04-12 10:00:05.000 am_anr: [0,1234,com.demo,0,Input dispatching timed out]'
RAW = (LINE + '\r\n').encode()


def captured_package(raw=RAW):
    return {'package_id': 'synthetic', 'sources': {'event_log': {
        'path': 'events.txt', 'content': raw.decode(), 'originalArtifacts': [{
            'path': 'events.txt', 'originalBase64': base64.b64encode(raw).decode(),
            'origin': {'type': 'file', 'path': '/synthetic/events.txt'},
        }],
    }}}


class StoreTests(unittest.TestCase):
    def test_byte_ranges_hash_identity_and_scopes(self):
        with EvidenceStore(':memory:') as store:
            raw = '中文\r\nlast'.encode()
            store.add_group('g')
            artifact = store.add_artifact(raw, source_kind='logcat', logical_path='log.txt',
                                          origin={'type': 'file', 'path': '/log.txt'})
            store.attach_artifact('g', artifact)
            span = store.add_span('g', artifact, 0, 8)
            self.assertEqual(store.read_span(span, group_id='g', source_kind='logcat')['content'], '中文\r\n'.encode())
            for start, end in ((-1, 2), (0, 100), (2, 1), (True, 2)):
                with self.subTest(start=start, end=end), self.assertRaises(EvidenceStoreError):
                    store.add_span('g', artifact, start, end)
            for group, source in (('other', 'logcat'), ('g', 'trace')):
                with self.assertRaises(EvidenceStoreError):
                    store.read_span(span, group_id=group, source_kind=source)
            store.connection.execute('UPDATE artifacts SET content=? WHERE id=?', (b'changed', artifact))
            with self.assertRaisesRegex(EvidenceStoreError, 'changed'):
                store.read_span(span, group_id='g', source_kind='logcat')

    def test_export_unique_ambiguous_missing_and_transformed_lines(self):
        package = captured_package('中文\r\nsame\nsame\nlast'.encode())
        groups = [{'id': 'g', 'eventLog': {'lines': ['中文', 'same', 'last', 'transformed']}}]
        with tempfile.TemporaryDirectory() as tmp:
            exported = export_evidence_store(package, groups, Path(tmp))
            rows = exported['groups']['g']
            self.assertEqual([r['status'] for r in rows], ['resolved', 'unresolved', 'resolved', 'unresolved'])
            self.assertEqual(rows[1]['reason'], 'ambiguous_original_occurrence')
            with EvidenceStore(exported['path']) as store:
                slices = attach_store_references(build_evidence_slices(groups), store, exported['groups'])
                self.assertEqual([bool(s.evidence_ref) for s in slices], [True, False, True, False])
                self.assertEqual(store.read_span(rows[0]['spanId'], group_id='g', source_kind='event_log')['content'], '中文\r\n'.encode())
                self.assertEqual(store.read_span(rows[2]['spanId'], group_id='g', source_kind='event_log')['content'], b'last')
                # Sidecar coordinates are not authority: canonical store values win.
                rows[0]['startByte'] = 999
                bound = attach_store_references(build_evidence_slices(groups), store, exported['groups'])
                self.assertEqual(bound[0].evidence_ref['startByte'], 0)
                rows[0]['sourceKind'] = 'trace'
                failed = attach_store_references(slices, store, exported['groups'])[0]
                self.assertIsNone(failed.evidence_ref)
                self.assertEqual(failed.evidence_ref_error, 'reference_integrity_check_failed')
                self.assertEqual(failed.content, '中文')

    def test_directory_zip_tar_capture_and_artifact_writer(self):
        for kind in ('directory', 'zip', 'tar'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                if kind == 'directory':
                    input_path = root / 'input'
                    input_path.mkdir()
                    (input_path / 'events.txt').write_bytes(RAW)
                elif kind == 'zip':
                    input_path = root / 'input.zip'
                    with zipfile.ZipFile(input_path, 'w') as archive:
                        archive.writestr('events.txt', RAW)
                else:
                    input_path = root / 'input.tar'
                    with tarfile.open(input_path, 'w') as archive:
                        member = tarfile.TarInfo('events.txt')
                        member.size = len(RAW)
                        archive.addfile(member, io.BytesIO(RAW))
                package = load_package_from_path(input_path, capture_originals=True)
                original = package['sources']['event_log']['originalArtifacts'][0]
                self.assertEqual(base64.b64decode(original['originalBase64']), RAW)
                self.assertEqual(original['origin']['type'], 'file' if kind == 'directory' else 'archive_member')
                legacy = load_package_from_path(input_path)
                self.assertEqual(package['sources']['event_log']['content'], legacy['sources']['event_log']['content'])
                self.assertNotIn('originalArtifacts', legacy['sources']['event_log'])
                index = build_ai_context_artifacts(package, AiContextOptions(
                    out_dir=root / 'output', build_evidence_store=True, build_ess=True))
                self.assertTrue(Path(index['artifactPaths']['evidenceStore']).is_file())
                self.assertTrue(index['groups'])
                paths = index['groups'][0]['artifactPaths']
                self.assertTrue(all(s.evidence_ref is None for s in read_ess_jsonl(Path(paths['evidenceSlices']))))
                sidecar = json.loads(Path(paths['evidenceRefs']).read_text())
                with EvidenceStore(index['artifactPaths']['evidenceStore']) as store:
                    slices = read_ess_jsonl(Path(paths['evidenceSlices']), store=store,
                        references={sidecar['groupId']: sidecar['references']})
                self.assertTrue(any(s.evidence_ref for s in slices))
                analysis = Path(paths['analysis']).read_text()
                self.assertEqual(sum(line.startswith('#### AI Analysis — ') for line in analysis.splitlines()), 4)
                self.assertTrue(Path(paths['logcat']).is_file())

    def test_mixed_archive_and_loose_capture_keeps_only_selected_provenance(self):
        archive_event = (LINE + "\r\narchive-superseded\r\n").encode()
        loose_event = (LINE + "\r\nloose-selected\r\n").encode()
        archive_trace = (
            "----- pid 1234 at 2026-04-12 10:00:05.100000000+0000 -----\r\n"
            "Cmd line: com.demo\r\n"
            '"main" prio=5 tid=1 Native\r\n'
            "  at android.os.BinderProxy.transactNative(Native method)\r\n"
        ).encode()
        archive_logcat = (
            "04-12 10:00:05.050 E InputDispatcher: Input dispatching timed out for com.demo\r\n"
        ).encode()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "mixed"
            root.mkdir()
            archive_path = root / "partial.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("events/archive_events.txt", archive_event)
                archive.writestr("data/anr/traces.txt", archive_trace)
                archive.writestr("logs/logcat.txt", archive_logcat)
            (root / "events.txt").write_bytes(loose_event)

            package = load_package_from_path(root, package_name="com.demo", capture_originals=True)
            legacy = load_package_from_path(root, package_name="com.demo")
            self.assertEqual(set(package["sources"]), set(legacy["sources"]))
            for source_kind in package["sources"]:
                self.assertEqual(package["sources"][source_kind]["content"],
                                 legacy["sources"][source_kind]["content"])
                self.assertNotIn("originalArtifacts", legacy["sources"][source_kind])
            self.assertIn("loose-selected", package["sources"]["event_log"]["content"])
            self.assertNotIn("archive-superseded", package["sources"]["event_log"]["content"])
            event_origins = [item["origin"] for item in package["sources"]["event_log"]["originalArtifacts"]]
            self.assertEqual({origin["type"] for origin in event_origins}, {"file"})
            self.assertEqual({Path(origin["path"]).name for origin in event_origins}, {"events.txt"})
            for source_kind in ("trace", "logcat"):
                origins = [item["origin"] for item in package["sources"][source_kind]["originalArtifacts"]]
                self.assertTrue(origins)
                self.assertEqual({origin["type"] for origin in origins}, {"archive_member"})
                self.assertEqual({origin["container"] for origin in origins}, {str(archive_path.absolute())})

            index = build_ai_context_artifacts(package, AiContextOptions(
                out_dir=Path(tmp) / "output", package_name="com.demo",
                build_evidence_store=True, build_ess=True,
            ))
            group = index["groups"][0]
            sidecar = json.loads(Path(group["artifactPaths"]["evidenceRefs"]).read_text())
            resolved = [row for row in sidecar["references"] if row["status"] == "resolved"]
            self.assertTrue(resolved)
            with EvidenceStore(index["artifactPaths"]["evidenceStore"]) as store:
                readbacks = [store.read_span(row["spanId"], group_id=group["id"],
                                             source_kind=row["sourceKind"]) for row in resolved]
            event_readbacks = [row for row in readbacks if row["sourceKind"] == "event_log"]
            self.assertTrue(event_readbacks)
            self.assertEqual({row["origin"]["type"] for row in event_readbacks}, {"file"})
            archive_readbacks = [row for row in readbacks if row["sourceKind"] in ("trace", "logcat")]
            self.assertTrue(archive_readbacks)
            self.assertEqual({row["origin"]["type"] for row in archive_readbacks}, {"archive_member"})
            self.assertNotIn("archive-superseded", "".join(row["content"].decode() for row in readbacks))

    def test_repeated_generation_disables_store_and_ess_without_losing_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "output"
            first = build_ai_context_artifacts(captured_package(), AiContextOptions(
                out_dir=out, build_evidence_store=True, build_ess=True,
            ))
            group_id = first["groups"][0]["id"]
            group_dir = out / group_id
            analysis_path = group_dir / "anr_analysis.md"
            analysis = analysis_path.read_text()
            marker = "<!-- AI_ANALYSIS_SLOT:trace -->"
            start = analysis.index(marker) + len(marker)
            end = analysis.index("\n### EventLog 事件日志", start)
            user_analysis = "\nUSER-WRITTEN TRACE ANALYSIS\n"
            analysis_path.write_text(analysis[:start] + user_analysis + analysis[end:])
            for suffix in ("-wal", "-shm"):
                (out / ("evidence.sqlite3" + suffix)).write_text("stale")

            second = build_ai_context_artifacts(captured_package(), AiContextOptions(out_dir=out))
            self.assertNotIn("evidenceStore", second["artifactPaths"])
            self.assertFalse(any((out / ("evidence.sqlite3" + suffix)).exists()
                                 for suffix in ("", "-wal", "-shm")))
            self.assertFalse((group_dir / "evidence_refs.json").exists())
            self.assertFalse((group_dir / "evidence.jsonl").exists())
            group_paths = second["groups"][0]["artifactPaths"]
            self.assertNotIn("evidenceRefs", group_paths)
            self.assertNotIn("evidenceSlices", group_paths)
            regenerated = analysis_path.read_text()
            self.assertIn("USER-WRITTEN TRACE ANALYSIS", regenerated)
            self.assertEqual(sum(line.startswith("#### AI Analysis — ")
                                 for line in regenerated.splitlines()), 4)
            self.assertTrue((group_dir / "logcat.txt").is_file())

    def test_failed_workspace_publication_preserves_prior_generation_and_retry_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "output"
            first = build_ai_context_artifacts(captured_package(), AiContextOptions(
                out_dir=out, build_evidence_store=True, build_ess=True,
            ))
            first_index = (out / "index.json").read_bytes()
            first_analysis = Path(first["groups"][0]["artifactPaths"]["analysis"]).read_bytes()
            first_store = (out / "evidence.sqlite3").read_bytes()
            real_replace = os.replace
            calls = 0

            def fail_stage_publication(source, destination):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("simulated publication failure")
                return real_replace(source, destination)

            with patch("anr_evidence.ai_context.os.replace", side_effect=fail_stage_publication):
                with self.assertRaisesRegex(OSError, "simulated publication failure"):
                    build_ai_context_artifacts(captured_package(), AiContextOptions(out_dir=out))

            self.assertEqual((out / "index.json").read_bytes(), first_index)
            self.assertEqual(Path(first["groups"][0]["artifactPaths"]["analysis"]).read_bytes(), first_analysis)
            self.assertEqual((out / "evidence.sqlite3").read_bytes(), first_store)
            self.assertTrue(Path(first["groups"][0]["artifactPaths"]["evidenceRefs"]).is_file())
            self.assertTrue(Path(first["groups"][0]["artifactPaths"]["evidenceSlices"]).is_file())

            retry = build_ai_context_artifacts(captured_package(), AiContextOptions(out_dir=out))
            self.assertNotIn("evidenceStore", retry["artifactPaths"])
            self.assertFalse((out / "evidence.sqlite3").exists())
            self.assertFalse((out / retry["groups"][0]["id"] / "evidence_refs.json").exists())
            self.assertFalse((out / retry["groups"][0]["id"] / "evidence.jsonl").exists())

    def test_duplicate_groups_and_invalid_encoding_fail_closed(self):
        package = captured_package()
        package['sources']['event_log']['originalArtifacts'][0]['originalBase64'] = base64.b64encode(b'\xff').decode()
        group = {'id': 'g', 'eventLog': {'lines': ['\ufffd']}}
        with tempfile.TemporaryDirectory() as tmp:
            result = export_evidence_store(package, [group], Path(tmp))
            self.assertEqual(result['groups']['g'][0]['status'], 'unresolved')
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                export_evidence_store(package, [group, group], Path(tmp))

    def test_duplicate_physical_sources_remain_ambiguous_after_text_dedup(self):
        for kind in ('directory', 'zip'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                if kind == 'directory':
                    target = root / 'input'
                    target.mkdir()
                    (target / 'events.txt').write_bytes(RAW)
                    (target / 'events_copy.txt').write_bytes(RAW.replace(b'\r\n', b'\n'))
                else:
                    target = root / 'input.zip'
                    with zipfile.ZipFile(target, 'w') as archive:
                        archive.writestr('events.txt', RAW)
                        archive.writestr('events_copy.txt', RAW)
                package = load_package_from_path(target, capture_originals=True)
                self.assertEqual(len(package['sources']['event_log']['originalArtifacts']), 2)
                exported = export_evidence_store(package, [{'id': 'g', 'eventLog': {'lines': [LINE]}}], root)
                self.assertEqual(exported['groups']['g'][0]['reason'], 'ambiguous_original_occurrence')

    def test_time_excluded_shards_cannot_supply_view_references(self):
        entries = []
        for name, content in (
            ('events_04_12_10_00_00.txt', LINE + '\nselected-only'),
            ('events_04_12_10_01_00.txt', LINE + '\nexcluded-only'),
        ):
            entries.append(dict(path=name, content=content, readable=True,
                originalBase64=base64.b64encode(content.encode()).decode(),
                origin={'type': 'file', 'path': '/synthetic/' + name}))
        package = build_package_from_entries('test', entries, event_anchor_dt=datetime(2026, 4, 12, 10, 0, 5))
        self.assertNotIn('excluded-only', package['sources']['event_log']['content'])
        self.assertEqual(len(package['sources']['event_log']['originalArtifacts']), 1)
        with tempfile.TemporaryDirectory() as tmp:
            exported = export_evidence_store(package, [dict(id='g', eventLog={'lines': [LINE, 'excluded-only']})], Path(tmp))
            self.assertEqual([r['status'] for r in exported['groups']['g']], ['resolved', 'unresolved'])


class AgentReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.package = captured_package()
        self.groups = build_ai_context(self.package, AiContextOptions()).groups
        self.exported = export_evidence_store(self.package, self.groups, Path(self.tmp.name))
        self.store = EvidenceStore(self.exported['path'])
        self.addCleanup(self.store.close)
        self.group = self.groups[0]['id']
        self.span = next(r['spanId'] for r in self.exported['groups'][self.group] if r['status'] == 'resolved')

    def candidate_report(self):
        value = report()
        value['candidateChains'] = [dict(title='Candidate', rank=1, score=20,
            confidenceLevel='low', signalCategory='input_dispatch_timeout', rationale='Trigger only',
            limitations=['No blocker'], evidenceRefs=[self.span], groupId=self.group, notRootCauseYet=True)]
        value['candidateConclusions'] = [dict(rank=1, confidenceLevel='low', statement='Tentative trigger',
            signalCategory='input_dispatch_timeout', whyNotFinal=['No blocker'], unresolvedQuestions=['Why?'],
            tentative=True, groupId=self.group, evidenceRefs=[self.span])]
        return value

    def run_report(self, value, *, side_effect=None, iterations=1, provider=None):
        cfg = provider or config(outbound_policy='synthetic_only')
        original = copy.deepcopy(self.package)
        with patch('urllib.request.urlopen', return_value=response(json.dumps(value)), side_effect=side_effect) as http:
            result = run_ai_agent_analysis(self.package, provider_config=cfg,
                agent_config=AgentConfig(provider=cfg, max_iterations=iterations),
                evidence_store=self.store, evidence_references=self.exported['groups'])
        self.assertEqual(self.package, original)
        return result, http

    def test_verified_catalog_reaches_http_and_report_is_accepted(self):
        result, http = self.run_report(self.candidate_report())
        self.assertEqual(result.validation_status, 'references_verified_causality_unverified')
        self.assertEqual(result.errors, [])
        self.assertEqual(result.integrated_report, self.candidate_report())
        payload = json.loads(http.call_args.args[0].data)
        serialized = json.dumps(payload)
        self.assertIn(self.span, serialized)
        self.assertIn('Store-reference mode', serialized)
        self.assertNotIn('/synthetic/events.txt', serialized)
        self.assertNotIn('originalBase64', serialized)

    def test_forged_cross_group_missing_refs_and_high_confidence_rejected(self):
        for field in ('candidateChains', 'candidateConclusions'):
            for key, value in (('groupId', 'other'), ('groupId', []), ('evidenceRefs', ['span-fake']),
                               ('evidenceRefs', []), ('evidenceRefs', [LINE]), ('confidenceLevel', 'high')):
                with self.subTest(field=field, key=key, value=value):
                    candidate = self.candidate_report()
                    candidate[field][0][key] = value
                    result, _ = self.run_report(candidate)
                    self.assertEqual(result.integrated_report, {})
                    self.assertIsNone(result.final_turn)
                    self.assertEqual(result.validation_status, 'not_accepted')
                    self.assertTrue(result.errors)

    def test_store_changed_during_model_request_is_rejected(self):
        def changed(*args, **kwargs):
            self.store.connection.execute('UPDATE artifacts SET content=?', (b'tampered',))
            return response(json.dumps(self.candidate_report()))
        result, _ = self.run_report(self.candidate_report(), side_effect=changed)
        self.assertEqual(result.integrated_report, {})
        self.assertTrue(any('readback' in error for error in result.errors))

    def test_reprobe_drops_unoffered_refs_and_rebinds_retained_refs(self):
        for sources, accepted in ((['event_log'], True), (['logcat'], False)):
            with self.subTest(sources=sources):
                first = report()
                first['reProbeRequest'] = dict(beforeSeconds=3, afterSeconds=3,
                    sourceKinds=sources, additionalTags=[], reason='Narrow window')
                replies = [response('{}')] * 3 + [response(json.dumps(first))]
                replies += [response('{}')] * 3 + [response(json.dumps(self.candidate_report()))]
                result, http = self.run_report(first, side_effect=replies, iterations=2)
                self.assertEqual(http.call_count, 8)
                self.assertEqual(bool(result.integrated_report), accepted)

    def test_store_does_not_bypass_outbound_deny(self):
        from anr_evidence.ai_agent import LlmRequestError
        with self.assertRaises(LlmRequestError), patch('urllib.request.urlopen') as http:
            self.run_report(self.candidate_report(), provider=config())
        http.assert_not_called()

    def test_paired_arguments_required_before_http(self):
        with patch('urllib.request.urlopen') as http, self.assertRaises(ValueError):
            run_ai_agent_analysis(self.package, provider_config=config(), evidence_store=self.store)
        http.assert_not_called()

    def test_invalid_sidecar_preserves_evidence_but_stops_before_http(self):
        for update in ({'sourceKind': 'trace'}, {'spanId': 'missing'}, {'spanId': []}):
            with self.subTest(update=update):
                references = copy.deepcopy(self.exported['groups'])
                row = next(r for r in references[self.group] if r['status'] == 'resolved')
                row.update(update)
                cfg = config(outbound_policy='synthetic_only')
                with patch('urllib.request.urlopen') as http:
                    result = run_ai_agent_analysis(self.package, provider_config=cfg,
                        evidence_store=self.store, evidence_references=references)
                http.assert_not_called()
                self.assertEqual(result.validation_status, 'not_accepted')
                self.assertTrue(any('integrity' in error for error in result.errors))
