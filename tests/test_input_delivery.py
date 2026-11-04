from __future__ import annotations

import unittest
from copy import deepcopy
from unittest.mock import patch

from anr_evidence import (
    analyze_normalized_package,
    extract_evidence_package,
    generate_causal_draft,
    generate_remediation_drafts,
    generate_root_cause_report,
    normalize_evidence_package,
    render_analysis_report,
    render_final_delivery,
)
from anr_evidence.incident_scenario import build_incident_scenario, render_incident_sections
from anr_evidence.input_anr import build_input_anr, render_input_anr_sections
from tests.helpers import load_fixture


INPUT_FIXTURES = ('idt_01.json', 'nfw_01.json')
INPUT_HEADINGS = (
    '## 超时输入事件',
    '## ANR 发生模型',
    '## 模拟操作步骤',
)
INCIDENT_HEADINGS = (
    '## ANR 发生模型',
    '## 模拟操作步骤',
)
TRANSITIONS = (
    generate_causal_draft,
    generate_root_cause_report,
    generate_remediation_drafts,
)


def _phase2(fixture_name: str) -> dict:
    return normalize_evidence_package(extract_evidence_package(load_fixture(fixture_name)))


def _phases(fixture_name: str) -> list[dict]:
    phases = [analyze_normalized_package(_phase2(fixture_name))]
    for generate in TRANSITIONS:
        phases.append(generate(phases[-1]))
    return phases


class InputDeliveryTests(unittest.TestCase):
    def test_phase3_builds_input_anr_from_the_original_timeline(self) -> None:
        for fixture_name in INPUT_FIXTURES:
            with self.subTest(fixture=fixture_name):
                phase2 = _phase2(fixture_name)
                original = deepcopy(phase2)
                with patch('anr_evidence.analyzer.build_input_anr', wraps=build_input_anr) as build:
                    phase3 = analyze_normalized_package(phase2)
                build.assert_called_once_with(
                    phase2['classification']['detectedType'],
                    phase2['anchors']['primaryAnchor'],
                    phase3['timeline'],
                )
                self.assertIs(build.call_args.args[2], phase3['timeline'])
                self.assertEqual(phase2, original)
                self.assertIsNotNone(phase3['inputAnr'])
                self.assertEqual(phase3['inputAnr'], build_input_anr(*build.call_args.args))
                for entry in phase3['timeline']:
                    for field in ('entryId', 'rawLine', 'recordRef', 'sourceKind', 'sourcePath'):
                        self.assertIn(field, entry)

    def test_input_fixtures_pass_through_all_phases_deterministically(self) -> None:
        for fixture_name in INPUT_FIXTURES:
            with self.subTest(fixture=fixture_name):
                phases = _phases(fixture_name)
                self.assertEqual(phases, _phases(fixture_name))
                for phase in phases[1:]:
                    self.assertEqual(phase['inputAnr'], phases[0]['inputAnr'])
                self.assertFalse(phases[2]['metadata']['finalJudgment'])
                self.assertFalse(phases[3]['metadata']['finalAdvice'])
                self.assertTrue(all(chain['notRootCauseYet'] for chain in phases[1]['candidateChains']))
                self.assertTrue(all(draft['requiresHumanConfirmation'] for draft in phases[3]['remediationDrafts']))

    def test_each_transition_deepcopies_without_rebuilding_or_mutating(self) -> None:
        for fixture_name in INPUT_FIXTURES:
            package = analyze_normalized_package(_phase2(fixture_name))
            package['inputAnr']['extension'] = {'items': ['retained']}
            for generate in TRANSITIONS:
                with self.subTest(fixture=fixture_name, transition=generate.__name__):
                    original = deepcopy(package)
                    with patch(f'{generate.__module__}.build_input_anr') as build:
                        result = generate(package)
                    build.assert_not_called()
                    self.assertEqual(package, original)
                    self.assertEqual(result['inputAnr'], package['inputAnr'])
                    self.assertIsNot(result['inputAnr'], package['inputAnr'])
                    self.assertIsNot(result['inputAnr']['extension'], package['inputAnr']['extension'])
                    result['inputAnr']['extension']['items'].append('downstream-only')
                    self.assertEqual(package, original)
                    package = result

    def test_legacy_transitions_build_with_empty_observations(self) -> None:
        for fixture_name in INPUT_FIXTURES:
            for package, generate in zip(_phases(fixture_name), TRANSITIONS):
                with self.subTest(fixture=fixture_name, transition=generate.__name__):
                    del package['inputAnr']
                    original = deepcopy(package)
                    args = (package['classification']['detectedType'], package['anchors']['primaryAnchor'], [])
                    with patch(f'{generate.__module__}.build_input_anr', wraps=build_input_anr) as build:
                        result = generate(package)
                    build.assert_called_once_with(*args)
                    self.assertEqual(result['inputAnr'], build_input_anr(*args))
                    self.assertEqual(package, original)

    def test_report_preserves_input_sections_for_every_phase(self) -> None:
        for fixture_name in INPUT_FIXTURES:
            for package in _phases(fixture_name):
                with self.subTest(fixture=fixture_name, phase=package['metadata']['phase']):
                    original = deepcopy(package)
                    report = render_analysis_report(package)
                    self.assertEqual(package, original)
                    self.assertIn('\n'.join(render_input_anr_sections(package['inputAnr'])), report)
                    self.assertIn(
                        '\n'.join(render_incident_sections(package['incidentScenario'], package['inputAnr'])),
                        report,
                    )
                    for heading in INPUT_HEADINGS:
                        self.assertEqual(report.count(heading), 1)
                    for heading in ('## 1. 基本信息', '## 9. 证据时间线', '## 10. 全局限制与后续人工分析建议'):
                        self.assertIn(heading, report)

    def test_delivery_preserves_input_sections_and_existing_headings(self) -> None:
        for fixture_name in INPUT_FIXTURES:
            with self.subTest(fixture=fixture_name):
                package = _phases(fixture_name)[-1]
                original = deepcopy(package)
                delivery = render_final_delivery(package)
                self.assertEqual(package, original)
                self.assertIn('\n'.join(render_input_anr_sections(package['inputAnr'])), delivery)
                self.assertIn(
                    '\n'.join(render_incident_sections(package['incidentScenario'], package['inputAnr'])),
                    delivery,
                )
                for heading in INPUT_HEADINGS:
                    self.assertEqual(delivery.count(heading), 1)
                for heading in ('## 一、执行摘要', '## 五、主线程关键信息', '## 八、交付说明'):
                    self.assertIn(heading, delivery)

    def test_legacy_report_branches_use_empty_observation_fallback(self) -> None:
        fallback_modules = ('hypothesis', 'root_cause', 'remediation', 'reporter')
        for fixture_name in INPUT_FIXTURES:
            for package, module in zip(_phases(fixture_name), fallback_modules):
                with self.subTest(fixture=fixture_name, phase=package['metadata']['phase']):
                    del package['inputAnr']
                    original = deepcopy(package)
                    args = (package['classification']['detectedType'], package['anchors']['primaryAnchor'], [])
                    with patch(f'anr_evidence.{module}.build_input_anr', wraps=build_input_anr) as build:
                        report = render_analysis_report(package)
                    build.assert_called_once_with(*args)
                    self.assertIn(
                        '\n'.join(render_incident_sections(package['incidentScenario'], build_input_anr(*args))),
                        report,
                    )
                    self.assertEqual(package, original)

    def test_legacy_delivery_uses_empty_observation_fallback(self) -> None:
        for fixture_name in INPUT_FIXTURES:
            with self.subTest(fixture=fixture_name):
                package = _phases(fixture_name)[-1]
                del package['inputAnr']
                original = deepcopy(package)
                args = (package['classification']['detectedType'], package['anchors']['primaryAnchor'], [])
                with patch('anr_evidence.delivery.build_input_anr', wraps=build_input_anr) as build:
                    delivery = render_final_delivery(package)
                build.assert_called_once_with(*args)
                self.assertIn(
                    '\n'.join(render_incident_sections(package['incidentScenario'], build_input_anr(*args))),
                    delivery,
                )
                self.assertEqual(package, original)

    def test_legacy_input_without_anchor_still_renders_conservatively(self) -> None:
        for fixture_name in INPUT_FIXTURES:
            for package in _phases(fixture_name):
                with self.subTest(fixture=fixture_name, phase=package['metadata']['phase']):
                    del package['inputAnr']
                    package['anchors'] = {}
                    for key in ('evidenceCatalog', 'evidenceIdentity', 'evidenceRevision'):
                        package.pop(key, None)
                    original = deepcopy(package)
                    reports = [render_analysis_report(package)]
                    if package['metadata']['phase'] == 'phase7-remediation-draft':
                        reports.append(render_final_delivery(package))
                    for report in reports:
                        for heading in INPUT_HEADINGS:
                            self.assertIn(heading, report)
                    self.assertEqual(package, original)

    def test_legacy_scenario_transitions_build_from_available_context(self) -> None:
        for fixture_name in INPUT_FIXTURES:
            for package, generate in zip(_phases(fixture_name), TRANSITIONS):
                with self.subTest(fixture=fixture_name, phase=package['metadata']['phase']):
                    del package['incidentScenario']
                    original = deepcopy(package)
                    with patch(
                        f'{generate.__module__}.build_incident_scenario',
                        wraps=build_incident_scenario,
                    ) as build:
                        result = generate(package)
                    build.assert_called_once_with(
                        package['classification']['detectedType'],
                        package['anchors']['primaryAnchor'],
                        package.get('timeline', []),
                        input_anr=package.get('inputAnr'),
                    )
                    self.assertEqual(result['incidentScenario'], build_incident_scenario(*build.call_args.args, **build.call_args.kwargs))
                    self.assertEqual(package, original)

    def test_report_and_delivery_add_public_sections_for_empty_or_missing_scenario(self) -> None:
        for fixture_name in ('unk_01.json', 'idt_01.json'):
            for package in _phases(fixture_name):
                for value in (None, {}):
                    with self.subTest(fixture=fixture_name, phase=package['metadata']['phase'], value=value):
                        candidate = deepcopy(package)
                        candidate['incidentScenario'] = value
                        original = deepcopy(candidate)
                        report = render_analysis_report(candidate)
                        self.assertEqual(candidate, original)
                        for heading in INCIDENT_HEADINGS:
                            self.assertIn(heading, report)
                        if candidate['metadata']['phase'] == 'phase7-remediation-draft':
                            delivery = render_final_delivery(candidate)
                            for heading in INCIDENT_HEADINGS:
                                self.assertIn(heading, delivery)

    def test_unknown_non_input_has_public_sections_but_no_input_event_section(self) -> None:
        for package in _phases('unk_01.json'):
            for legacy in (False, True):
                with self.subTest(phase=package['metadata']['phase'], legacy=legacy):
                    candidate = deepcopy(package)
                    self.assertIsNone(candidate['inputAnr'])
                    if legacy:
                        del candidate['inputAnr']
                    original = deepcopy(candidate)
                    reports = [render_analysis_report(candidate)]
                    if candidate['metadata']['phase'] == 'phase7-remediation-draft':
                        reports.append(render_final_delivery(candidate))
                    for report in reports:
                        self.assertNotIn('## 超时输入事件', report)
                        for heading in INCIDENT_HEADINGS:
                            self.assertIn(heading, report)
                    self.assertEqual(candidate, original)

    def test_renderers_do_not_expose_input_payload_to_mutation(self) -> None:
        def mutate(scenario: dict, input_anr: dict | None = None) -> list[str]:
            input_anr['extension']['items'].append('renderer-only')
            return []

        for package in _phases('idt_01.json'):
            package['inputAnr']['extension'] = {'items': ['retained']}
            original = deepcopy(package)
            with self.subTest(phase=package['metadata']['phase']):
                with patch('anr_evidence.reporter.render_incident_sections', side_effect=mutate):
                    render_analysis_report(package)
                self.assertEqual(package, original)
                if package['metadata']['phase'] == 'phase7-remediation-draft':
                    with patch('anr_evidence.delivery.render_incident_sections', side_effect=mutate):
                        render_final_delivery(package)
                    self.assertEqual(package, original)

    def test_sparse_input_fixtures_do_not_invent_event_sequences(self) -> None:
        for fixture_name in INPUT_FIXTURES:
            for package in _phases(fixture_name):
                with self.subTest(fixture=fixture_name, phase=package['metadata']['phase']):
                    input_anr = package['inputAnr']
                    self.assertIsNone(input_anr['triggeringEvent'])
                    self.assertEqual(input_anr['eventIdentificationStatus'], 'event_unspecified')
                    for record in input_anr['reportedEvents']:
                        self.assertIsNone(record['identity']['nativeSeq'])
                        self.assertIsNone(record['identity']['reportedSeq'])


if __name__ == '__main__':
    unittest.main()
