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
from anr_evidence.anr_strategy import ANR_TYPE_STRATEGIES
from anr_evidence.incident_scenario import build_incident_scenario, render_incident_sections
from tests.helpers import load_fixture


PUBLIC_HEADINGS = ('## ANR 发生模型', '## 模拟操作步骤')
INPUT_HEADING = '## 超时输入事件'
INPUT_TYPES = {'input_dispatching_timeout', 'no_focus_window'}
ANR_TYPES = (*ANR_TYPE_STRATEGIES, 'custom_timeout_type', '', None)
TRANSITIONS = (generate_causal_draft, generate_root_cause_report, generate_remediation_drafts)


def _phase2(fixture_name: str) -> dict:
    return normalize_evidence_package(extract_evidence_package(load_fixture(fixture_name)))


def _phases(fixture_name: str) -> list[dict]:
    phases = [analyze_normalized_package(_phase2(fixture_name))]
    for transition in TRANSITIONS:
        phases.append(transition(phases[-1]))
    return phases


def _phases_for_type(anr_type: str | None) -> list[dict]:
    phase2 = _phase2('unk_01.json')
    phase2['classification']['detectedType'] = anr_type
    phases = [analyze_normalized_package(phase2)]
    for transition in TRANSITIONS:
        phases.append(transition(phases[-1]))
    return phases


class IncidentDeliveryTests(unittest.TestCase):
    def assert_sections(self, report: str, is_input: bool) -> None:
        for heading in PUBLIC_HEADINGS:
            self.assertEqual(report.count(heading), 1)
        self.assertEqual(report.count(INPUT_HEADING), 1 if is_input else 0)

    def test_every_strategy_type_and_custom_fallback_work_end_to_end(self) -> None:
        for anr_type in ANR_TYPES:
            phases = _phases_for_type(anr_type)
            for package in phases:
                with self.subTest(anr_type=anr_type, phase=package['metadata']['phase']):
                    original = deepcopy(package)
                    scenario = package['incidentScenario']
                    self.assertTrue(scenario['anrModel'])
                    self.assertTrue(scenario['simulationSteps'])
                    self.assertEqual(scenario, phases[0]['incidentScenario'])
                    self.assertFalse(scenario['finalJudgment'])
                    self.assertTrue(scenario['notRootCauseYet'])
                    self.assertTrue(scenario['requiresHumanConfirmation'])
                    self.assertTrue(all(not step['executed'] for step in scenario['simulationSteps']))
                    reports = [render_analysis_report(package)]
                    if package['metadata']['phase'] == 'phase7-remediation-draft':
                        reports.append(render_final_delivery(package))
                    for report in reports:
                        self.assert_sections(report, anr_type in INPUT_TYPES)
                        self.assertIn('\n'.join(render_incident_sections(scenario, package['inputAnr'])), report)
                    self.assertEqual(package, original)

    def test_phase3_builds_scenario_with_original_timeline_and_input_payload(self) -> None:
        for fixture_name in ('idt_01.json', 'nfw_01.json', 'unk_01.json'):
            with self.subTest(fixture=fixture_name):
                phase2 = _phase2(fixture_name)
                original = deepcopy(phase2)
                with patch('anr_evidence.analyzer.build_incident_scenario', wraps=build_incident_scenario) as build:
                    phase3 = analyze_normalized_package(phase2)
                build.assert_called_once_with(
                    phase2['classification']['detectedType'],
                    phase2['anchors']['primaryAnchor'],
                    phase3['timeline'],
                    input_anr=phase3['inputAnr'],
                )
                self.assertIs(build.call_args.args[2], phase3['timeline'])
                self.assertEqual(phase3['incidentScenario'], build_incident_scenario(*build.call_args.args, **build.call_args.kwargs))
                self.assertEqual(phase2, original)

    def test_unknown_and_custom_types_use_conservative_fallback(self) -> None:
        for anr_type in ('unknown', 'custom_timeout_type', '', None):
            with self.subTest(anr_type=anr_type):
                scenario = build_incident_scenario(anr_type, None, [])
                sections = render_incident_sections(scenario)
                self.assertEqual(scenario['anrType'], anr_type or 'unknown')
                self.assertTrue(scenario['anrModel'])
                self.assertTrue(scenario['simulationSteps'])
                for heading in PUBLIC_HEADINGS:
                    self.assertIn(heading, sections)
                self.assertNotIn(INPUT_HEADING, sections)

    def test_input_scenario_reuses_input_model_and_keeps_all_input_sections(self) -> None:
        phase3 = _phases('idt_01.json')[0]
        scenario = build_incident_scenario(
            phase3['classification']['detectedType'],
            phase3['anchors']['primaryAnchor'],
            phase3['timeline'],
            input_anr=phase3['inputAnr'],
        )
        self.assertEqual(scenario['anrModel'], phase3['inputAnr']['anrModel'])
        self.assertEqual(scenario['simulationSteps'], phase3['inputAnr']['simulationSteps'])
        sections = render_incident_sections(scenario, phase3['inputAnr'])
        for heading in (*PUBLIC_HEADINGS, INPUT_HEADING):
            self.assertIn(heading, sections)

    def test_all_phase_outputs_carry_incident_scenario_without_mutation(self) -> None:
        for fixture_name in ('idt_01.json', 'nfw_01.json', 'unk_01.json'):
            phases = _phases(fixture_name)
            self.assertEqual(phases, _phases(fixture_name))
            for index, package in enumerate(phases):
                with self.subTest(fixture=fixture_name, phase=package['metadata']['phase']):
                    self.assertIsInstance(package['incidentScenario'], dict)
                    original = deepcopy(package)
                    if index < len(phases) - 1:
                        result = TRANSITIONS[index](package)
                        self.assertEqual(result['incidentScenario'], package['incidentScenario'])
                    self.assertEqual(package, original)

    def test_transitions_deepcopy_nested_scenarios_without_rebuilding(self) -> None:
        for fixture_name in ('idt_01.json', 'unk_01.json'):
            for package, generate in zip(_phases(fixture_name), TRANSITIONS):
                with self.subTest(fixture=fixture_name, transition=generate.__name__):
                    package['incidentScenario']['extension'] = {'items': ['retained']}
                    original = deepcopy(package)
                    with patch(f'{generate.__module__}.build_incident_scenario') as build:
                        result = generate(package)
                    build.assert_not_called()
                    self.assertEqual(package, original)
                    self.assertEqual(result['incidentScenario'], package['incidentScenario'])
                    self.assertIsNot(result['incidentScenario'], package['incidentScenario'])
                    self.assertIsNot(result['incidentScenario']['anrModel'], package['incidentScenario']['anrModel'])
                    result['incidentScenario']['extension']['items'].append('downstream')
                    self.assertEqual(package, original)

    def test_legacy_transitions_use_available_timeline_and_input_anr(self) -> None:
        for fixture_name in ('idt_01.json', 'unk_01.json'):
            phases = _phases(fixture_name)
            for package, generate in zip(phases, TRANSITIONS):
                for value in ('missing', None, {}):
                    with self.subTest(fixture=fixture_name, transition=generate.__name__, value=value):
                        candidate = deepcopy(package)
                        candidate.pop('incidentScenario')
                        if value != 'missing':
                            candidate['incidentScenario'] = value
                        candidate['timeline'] = deepcopy(phases[0]['timeline'])
                        original = deepcopy(candidate)
                        with patch(f'{generate.__module__}.build_incident_scenario', wraps=build_incident_scenario) as build:
                            result = generate(candidate)
                        build.assert_called_once_with(
                            candidate['classification']['detectedType'],
                            candidate['anchors']['primaryAnchor'],
                            candidate['timeline'],
                            input_anr=candidate['inputAnr'],
                        )
                        self.assertEqual(result['incidentScenario'], build_incident_scenario(*build.call_args.args, **build.call_args.kwargs))
                        self.assertEqual(candidate, original)

    def test_legacy_report_coercion_builds_scenario_for_all_four_phases(self) -> None:
        for anr_type in ANR_TYPES:
            for package in _phases_for_type(anr_type):
                with self.subTest(anr_type=anr_type, phase=package['metadata']['phase']):
                    candidate = deepcopy(package)
                    del candidate['incidentScenario']
                    del candidate['inputAnr']
                    original = deepcopy(candidate)
                    reports = [render_analysis_report(candidate)]
                    if candidate['metadata']['phase'] == 'phase7-remediation-draft':
                        reports.append(render_final_delivery(candidate))
                    for report in reports:
                        self.assert_sections(report, anr_type in INPUT_TYPES)
                    self.assertEqual(candidate, original)

    def test_explicit_input_none_is_preserved_in_legacy_scenario_fallback(self) -> None:
        for package in _phases('idt_01.json'):
            package.pop('incidentScenario')
            package['inputAnr'] = None
            original = deepcopy(package)
            phase = package['metadata']['phase']
            index = ['phase3-assisted-analysis', 'phase5-causal-draft', 'phase6-root-cause-report-v1', 'phase7-remediation-draft'].index(phase)
            with self.subTest(phase=phase):
                if index < len(TRANSITIONS):
                    result = TRANSITIONS[index](package)
                    self.assertIsNone(result['inputAnr'])
                    self.assertTrue(result['incidentScenario'])
                self.assert_sections(render_analysis_report(package), False)
                if index == len(TRANSITIONS):
                    self.assert_sections(render_final_delivery(package), False)
                self.assertEqual(package, original)

    def test_null_or_empty_scenario_fields_still_render_public_headings(self) -> None:
        for fixture_name in ('idt_01.json', 'unk_01.json'):
            for package in _phases(fixture_name):
                for value in (None, []):
                    candidate = deepcopy(package)
                    for field in ('anrModel', 'simulationSteps', 'missingParameters', 'evidenceGaps'):
                        candidate['incidentScenario'][field] = deepcopy(value)
                    original = deepcopy(candidate)
                    with self.subTest(fixture=fixture_name, phase=package['metadata']['phase'], value=value):
                        self.assert_sections(render_analysis_report(candidate), candidate['inputAnr'] is not None)
                        if candidate['metadata']['phase'] == 'phase7-remediation-draft':
                            self.assert_sections(render_final_delivery(candidate), candidate['inputAnr'] is not None)
                        self.assertEqual(candidate, original)

    def test_legacy_report_builders_use_available_context_in_every_coerce_branch(self) -> None:
        modules = ('hypothesis', 'root_cause', 'remediation', 'reporter')
        phases = _phases('unk_01.json')
        for package, module in zip(phases, modules):
            with self.subTest(phase=package['metadata']['phase']):
                package.pop('incidentScenario')
                package['timeline'] = deepcopy(phases[0]['timeline'])
                original = deepcopy(package)
                with patch(f'anr_evidence.{module}.build_incident_scenario', wraps=build_incident_scenario) as build:
                    report = render_analysis_report(package)
                build.assert_called_once_with(
                    package['classification']['detectedType'],
                    package['anchors']['primaryAnchor'],
                    package['timeline'],
                    input_anr=None,
                )
                self.assert_sections(report, False)
                self.assertEqual(package, original)

    def test_legacy_delivery_builder_uses_available_timeline(self) -> None:
        phases = _phases('unk_01.json')
        package = phases[-1]
        package.pop('incidentScenario')
        package['timeline'] = deepcopy(phases[0]['timeline'])
        original = deepcopy(package)
        with patch('anr_evidence.delivery.build_incident_scenario', wraps=build_incident_scenario) as build:
            delivery = render_final_delivery(package)
        build.assert_called_once_with(
            package['classification']['detectedType'],
            package['anchors']['primaryAnchor'],
            package['timeline'],
            input_anr=None,
        )
        self.assert_sections(delivery, False)
        self.assertEqual(package, original)

    def test_report_renderers_receive_deepcopied_scenario_and_input_payloads(self) -> None:
        def mutate(scenario, input_anr=None):
            scenario['extension']['items'].append('renderer')
            if input_anr is not None:
                input_anr['extension']['items'].append('renderer')
            return []

        for package in _phases('idt_01.json'):
            with self.subTest(phase=package['metadata']['phase']):
                package['incidentScenario']['extension'] = {'items': ['scenario']}
                package['inputAnr']['extension'] = {'items': ['input']}
                original = deepcopy(package)
                with patch('anr_evidence.reporter.render_incident_sections', side_effect=mutate):
                    render_analysis_report(package)
                if package['metadata']['phase'] == 'phase7-remediation-draft':
                    with patch('anr_evidence.delivery.render_incident_sections', side_effect=mutate):
                        render_final_delivery(package)
                self.assertEqual(package, original)


if __name__ == '__main__':
    unittest.main()
