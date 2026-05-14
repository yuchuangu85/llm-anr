from __future__ import annotations

import unittest

from anr_evidence import (
    analyze_normalized_package,
    extract_evidence_package,
    generate_causal_draft,
    normalize_evidence_package,
    render_analysis_report,
)
from tests.helpers import load_fixture


class Phase4ReportTests(unittest.TestCase):
    def test_report_contains_expected_sections(self) -> None:
        phase3 = analyze_normalized_package(normalize_evidence_package(extract_evidence_package(load_fixture('nfw_01.json'))))
        report = render_analysis_report(phase3)
        self.assertIn('# ANR 辅助分析报告草稿', report)
        self.assertIn('## 1. 基本信息', report)
        self.assertIn('## 5. 关键发现（辅助分析）', report)
        self.assertIn('## 6. 保守版候选结论', report)
        self.assertIn('## 7. 候选因果链草稿', report)
        self.assertIn('## 8. 修复建议草稿（需人工确认）', report)
        self.assertIn('## 9. 证据时间线', report)
        self.assertIn('不是最终根因裁决', report)
        self.assertIn('不包含最终修复结论', report)
        self.assertIn('notRootCauseYet', report)
        self.assertIn('Evidence Snippets', report)
        self.assertIn('Rank:', report)
        self.assertIn('Score:', report)
        self.assertIn('Trace Insights', report)
        self.assertIn('Main Thread:', report)
        self.assertIn('thread=`main`', report)
        self.assertIn('Top Suspicious Trace Records', report)
        self.assertIn('dominantBlockHint=`focus_window_wait`', report)
        self.assertIn('suspiciousThreads=`1`', report)

    def test_report_preserves_fallback_and_coverage_notes(self) -> None:
        ambiguous = analyze_normalized_package(normalize_evidence_package(extract_evidence_package(load_fixture('amb_01.json'))))
        report = render_analysis_report(ambiguous)
        self.assertIn('Fallback Mode', report)
        self.assertIn('ambiguous_type', report)

        partial = analyze_normalized_package(normalize_evidence_package(extract_evidence_package(load_fixture('miss_trace_01.json'))))
        report = render_analysis_report(partial)
        self.assertIn('Missing Sources', report)
        self.assertIn('trace', report)

    def test_report_accepts_phase5_causal_draft_input(self) -> None:
        phase5 = generate_causal_draft(
            analyze_normalized_package(
                normalize_evidence_package(
                    extract_evidence_package(load_fixture('idt_01.json'))
                )
            )
        )
        report = render_analysis_report(phase5)
        self.assertIn('## 6. 保守版候选结论', report)
        self.assertIn('## 7. 候选因果链草稿', report)
        self.assertIn('输入分发阻塞链路候选', report)
        self.assertIn('Evidence Snippets', report)
        self.assertIn('[#1/score=', report)
        self.assertIn('Top Conclusion', report)
        self.assertIn('requiresHumanConfirmation', report)


if __name__ == '__main__':
    unittest.main()
