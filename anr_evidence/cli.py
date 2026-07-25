"""CLI for the ANR evidence extraction + normalization + assisted analysis pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .ai_context import AiContextOptions, build_ai_context_artifacts
from .delivery import render_final_delivery
from .extractor import ArchiveLoadError, load_package_from_archive, load_package_from_directory, load_package_from_fixture
from .pipeline import payload_phase, run_until
from .replay import run_replay_manifest
from .reporter import render_analysis_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract or transform ANR evidence packages")
    parser.add_argument("input", help="Fixture JSON path, phase package JSON path, raw log package directory, or replay manifest")
    parser.add_argument("-o", "--output", help="Write output to a file instead of stdout")
    parser.add_argument("--normalize", action="store_true", help="Normalize to the Phase 2 schema. If the input is raw/fixture data, Phase 1 extraction runs first.")
    parser.add_argument("--analyze", action="store_true", help="Run Phase 3 assisted analysis. Raw/fixture inputs will flow through Phase 1 + Phase 2 first.")
    parser.add_argument("--hypothesize", action="store_true", help="Generate deterministic candidate causal-chain drafts. Raw/fixture inputs will flow through Phase 1 + Phase 2 + Phase 3 first.")
    parser.add_argument("--report", action="store_true", help="Render a Markdown draft report. Raw/fixture inputs will flow through Phase 1 + Phase 2 + Phase 3 + Phase 5/6/7 first.")
    parser.add_argument("--root-cause", action="store_true", help="Generate a conservative root-cause report v1 from candidate causal chains.")
    parser.add_argument("--remediate", action="store_true", help="Generate gated remediation drafts from conservative root-cause conclusions.")
    parser.add_argument("--deliver", action="store_true", help="Render the final delivery markdown template from Phase 7 remediation drafts.")
    parser.add_argument("--replay", action="store_true", help="Run a replay manifest and emit per-case artifacts plus a JSON summary.")
    parser.add_argument("--replay-out", help="Output directory for replay artifacts.")
    parser.add_argument("--build-ai-context", action="store_true", help="Build per-ANR anr_analysis.md workspaces plus an index.json under the output directory.")
    parser.add_argument("--out-dir", help="Output directory for --build-ai-context artifacts.")
    parser.add_argument("--event-before-seconds", type=int, default=None, help="EventLog seconds before am_anr retained in AI context cache. Defaults to the ANR type strategy.")
    parser.add_argument("--logcat-before-seconds", type=int, default=None, help="Logcat seconds before ANR anchor retained in AI context cache. Defaults to the ANR type strategy.")
    parser.add_argument("--logcat-after-seconds", type=int, default=None, help="Logcat seconds after ANR anchor retained in AI context cache. Defaults to the ANR type strategy.")
    parser.add_argument("--group-tolerance-seconds", type=int, default=None, help="Seconds used to collapse nearby anchors into the same ANR group. Defaults to the ANR type strategy.")
    parser.add_argument("--package", dest="package_name", help="Optional package name filter for AI context EventLog/logcat extraction.")
    parser.add_argument("--anr-type", help="Optional ANR type strategy override for --build-ai-context.")
    args = parser.parse_args()

    input_path = Path(args.input)
    if args.replay:
        result = run_replay_manifest(input_path, args.replay_out)
    else:
        try:
            if input_path.is_dir():
                payload = load_package_from_directory(input_path, package_name=args.package_name)
            elif _is_archive_path(input_path):
                payload = load_package_from_archive(input_path, package_name=args.package_name)
            else:
                payload = load_package_from_fixture(input_path)
        except ArchiveLoadError as exc:
            raise SystemExit(str(exc)) from exc

        if args.build_ai_context:
            result = build_ai_context_artifacts(
                payload,
                AiContextOptions(
                    out_dir=args.out_dir or "anr_ai_context",
                    event_before_seconds=args.event_before_seconds,
                    logcat_before_seconds=args.logcat_before_seconds,
                    logcat_after_seconds=args.logcat_after_seconds,
                    group_tolerance_seconds=args.group_tolerance_seconds,
                    package_name=args.package_name,
                    anr_type=args.anr_type,
                ),
            )
        else:
            result = _transform_payload(
                payload,
                normalize=args.normalize,
                analyze=args.analyze,
                hypothesize=args.hypothesize,
                report=args.report,
                root_cause=args.root_cause,
                remediate=args.remediate,
                deliver=args.deliver,
            )

    rendered = result if isinstance(result, str) else json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


def _transform_payload(payload: dict, *, normalize: bool, analyze: bool, hypothesize: bool, report: bool, root_cause: bool, remediate: bool, deliver: bool) -> dict | str:
    phase_kind = payload_phase(payload)
    operation = next((name for name, enabled in (
        ("deliver", deliver),
        ("report", report),
        ("remediate", remediate),
        ("root-cause", root_cause),
        ("hypothesize", hypothesize),
        ("analyze", analyze),
        ("normalize", normalize),
    ) if enabled), None)

    allowed_from_phase = {
        1: {"normalize", "analyze", "hypothesize", "root-cause", "remediate", "report", "deliver"},
        2: {"analyze", "hypothesize", "root-cause", "remediate", "report", "deliver"},
        3: {"hypothesize", "root-cause", "remediate", "report", "deliver"},
        5: {"root-cause", "remediate", "report", "deliver"},
        6: {"remediate", "report", "deliver"},
        7: {"report", "deliver"},
    }
    if phase_kind and operation not in allowed_from_phase[phase_kind]:
        raise SystemExit(_phase_guidance(phase_kind))

    target_phase = {
        None: 1,
        "normalize": 2,
        "analyze": 3,
        "hypothesize": 5,
        "root-cause": 6,
        "remediate": 7,
        "report": 7,
        "deliver": 7,
    }[operation]
    result = run_until(payload, target_phase)
    if operation == "report":
        return render_analysis_report(result)
    if operation == "deliver":
        return render_final_delivery(result)
    return result


def _phase_guidance(phase: int) -> str:
    return {
        1: "Input is already a Phase 1 evidence package. Use --normalize, --analyze, --hypothesize, --root-cause, --remediate, --deliver, or --report.",
        2: "Input is already a Phase 2 normalized package. Use --analyze, --hypothesize, --root-cause, --remediate, --deliver, or --report.",
        3: "Input is already a Phase 3 assisted analysis package. Use --hypothesize, --root-cause, --remediate, --deliver, or --report.",
        5: "Input is already a Phase 5 causal draft package. Use --root-cause, --remediate, --deliver, or --report.",
        6: "Input is already a Phase 6 root-cause report package. Use --remediate, --deliver, or --report.",
        7: "Input is already a Phase 7 remediation draft package. Use --deliver or --report.",
    }[phase]


def _is_archive_path(path: Path) -> bool:
    suffixes = [suffix.lower() for suffix in path.suffixes]
    if not suffixes:
        return False
    if suffixes[-1] == ".zip":
        return True
    return any(suffix in {".tar", ".gz", ".tgz", ".bz2", ".xz"} for suffix in suffixes)


if __name__ == "__main__":
    raise SystemExit(main())
