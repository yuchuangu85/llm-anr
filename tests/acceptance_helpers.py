"""Small complete report fixture; conclusions intentionally remain unknown."""

import json


def report_payload(group_id="anr-test", reference=None):
    refs = [reference] if reference else []
    candidate = {
        "summary": "机制尚需核验，缺少完整计时和对端证据。",
        "supportingEventIds": refs, "uncertainties": ["有效阈值和连续阻塞区间未知。"],
        "certainty": "inferred", "validation": "unverified", "executed": False,
        "requiresHumanConfirmation": True,
    }
    return {
        "groupId": group_id, "anrType": "no_focus_window",
        "primaryRootCauseHintId": None, "primaryRootCauseDescription": "证据不足，根因未知。",
        "supportingHintIds": [], "blockingThread": {"tid": None, "name": None, "frame": None},
        "ownerThread": {"tid": None, "name": None, "frame": None},
        "sourceAnalyses": {key: {
            "summary": "尚无足够保留证据，无法确定本来源的阻塞机制。",
            "keyEvidence": refs, "gaps": ["缺少完整采集和对端数据。"],
        } for key in ("trace", "eventLog", "logcat")},
        "candidateChains": [], "inputEventAssessments": [dict(candidate)],
        "anrModel": [dict(candidate)], "simulationSteps": [dict(candidate)],
        "remediationSuggestions": ["补采同一事故的日志、Trace 与窗口信息再核验。"],
        "evidenceGaps": ["缺少 Trace 或有效等待起点，无法定责。"],
        "finalJudgment": False, "notRootCauseYet": True, "requiresHumanConfirmation": True,
    }


def final_analysis(payload):
    from anr_evidence.report_validation import FINAL_REPORT_SECTIONS

    return "\n\n".join(
        "## " + title + "\n证据不足，当前无法确定；需补采同一事故日志后核验。"
        for title in FINAL_REPORT_SECTIONS
    ) + "\n\n## 结构化 JSON 尾部\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```\n"


def fill_workspace(group, *, reference=None):
    from pathlib import Path
    from anr_evidence.ai_context import _merge_analyses

    analyses = {slot: "本来源无法确定直接阻塞点；缺少连续证据，需要补采后核验。" for slot in ("trace", "eventlog", "logcat-anrmanager")}
    analyses["final-anr"] = final_analysis(report_payload(group["id"], reference))
    path = Path(group["artifactPaths"]["analysis"])
    path.write_text(_merge_analyses(path.read_text(encoding="utf-8"), analyses), encoding="utf-8")
    return analyses
