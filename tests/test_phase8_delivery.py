from __future__ import annotations

import unittest

from anr_evidence import (
    analyze_normalized_package,
    extract_evidence_package,
    generate_causal_draft,
    generate_remediation_drafts,
    generate_root_cause_report,
    normalize_evidence_package,
    render_final_delivery,
)
from tests.helpers import load_fixture


class Phase8DeliveryTests(unittest.TestCase):
    def test_final_delivery_contains_expected_sections(self) -> None:
        phase7 = generate_remediation_drafts(
            generate_root_cause_report(
                generate_causal_draft(
                    analyze_normalized_package(
                        normalize_evidence_package(
                            extract_evidence_package(load_fixture('nfw_01.json'))
                        )
                    )
                )
            )
        )
        delivery = render_final_delivery(phase7)
        self.assertIn('# ANR 分析交付稿', delivery)
        self.assertIn('## 一、执行摘要', delivery)
        self.assertIn('## 四、最高优先级候选结论', delivery)
        self.assertIn('## 五、主线程关键信息', delivery)
        self.assertIn('Thread: `main`', delivery)
        self.assertIn('Block Hint: `focus_window_wait`', delivery)
        self.assertIn('## 六、修复建议草稿（需人工确认）', delivery)
        self.assertIn('## 七、全局限制', delivery)
        self.assertIn('requiresHumanConfirmation', delivery)


if __name__ == '__main__':
    unittest.main()
