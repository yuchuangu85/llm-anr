#!/usr/bin/env python3
"""Compare two archived replay sessions with optional threshold evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from anr_evidence.replay import compare_replay_sessions, evaluate_replay_diff


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare two ANR replay sessions")
    parser.add_argument('baseline_session')
    parser.add_argument('candidate_session')
    parser.add_argument('--max-elapsed-delta-ms', type=float)
    parser.add_argument('--max-artifact-bytes-delta', type=int)
    parser.add_argument('--allow-phase-changes', action='store_true')
    parser.add_argument('--allow-case-count-changes', action='store_true')
    parser.add_argument('--allow-rule-regressions', action='store_true')
    args = parser.parse_args()

    diff = compare_replay_sessions(args.baseline_session, args.candidate_session)
    evaluation = evaluate_replay_diff(
        diff,
        max_elapsed_ms_delta=args.max_elapsed_delta_ms,
        max_artifact_bytes_delta=args.max_artifact_bytes_delta,
        allow_phase_changes=args.allow_phase_changes,
        allow_case_count_changes=args.allow_case_count_changes,
        allow_rule_regressions=args.allow_rule_regressions,
    )
    payload = {'diff': diff, 'evaluation': evaluation}
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if evaluation['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
