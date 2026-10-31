# agent-anr

> [中文版](README-zh.md)

`agent-anr` is an Agent-driven Android ANR (App Not Responding) evidence extraction and AI-assisted analysis pipeline. It turns raw traces, EventLog, logcat, AnrManager dumps, meminfo, and kernel logs into auditable per-ANR evidence workspaces, then guides an AI/human analyst through conservative source-by-source analysis.

The project is optimized for **high recall and auditability**, not premature final blame: trigger classification, root-cause pattern hints, and remediation drafts remain evidence-backed candidates until a human confirms them.

## Requirements

- Python 3.12 or newer. CI verifies Python 3.12 and 3.13.
- Runtime dependencies: Python standard library only.

## Current capabilities

### Evidence extraction and routing

- Loads fixture JSON, pre-extracted directories, bugreport directories, ZIP archives, and TAR variants.
- Discovers common vendor/system layouts, including `data/anr/`, `FS/data/anr/`, `System_log/`, `event-log/`, `logs/`, `android-logs/`, `dropbox/`, `meminfo.txt`, `last_kmsg`, `lastkmsg`, and `console_ramoops`.
- Uses package-scoped `am_anr` as the primary anchor when available; strict package matching prevents unrelated ANRs from becoming anchors.
- Preserves trace-selected ANR time for unanchored fallback contexts so fallback output still carries a useful timestamp.
- Splits multiple ANRs in the same input into independent `anr-*` workspaces; AnrManager, meminfo, trace, EventLog, logcat, and kernel evidence must not bleed across anchors.
- Keeps baseline evidence additive: type strategies and hints can add focus, windows, and keywords, but cannot remove critical sources.

### Source-specific analysis support

- **Trace**: main thread state, schedstat, Java/ART/Linux state mapping, lock waits, owner/peer threads, Binder waits, render/fence waits, native poll interpretation, deadlock detection, and trace hints.
- **EventLog**: `am_anr` anchored pre-ANR windows, AM/WM/Input/process/memory tag preservation, ΔT interpretation, and package-filtered anchor discovery without filtering out contextual system lines.
- **Logcat / AnrManager**: complete AnrManager dump flow extraction, including `startAnrDump`, stack dump, `ANR in`, reason, load, PSI, CPU windows, `TOTAL`, top CPU processes, DropBox, and dump completion lines.
- **Meminfo**: optional follow-up after AnrManager; correlates target package and high-load process PSS/RSS snapshots nearest to the ANR anchor.
- **Kernel log**: retained as baseline context for scheduler, Binder, hung task, low memory, OOM/LMK, and pressure signals.
- **Context control**: filtered logcat is written to per-ANR `logcat.txt` instead of being inlined into the main analysis file, reducing prompt bloat while keeping evidence reviewable.

### Classification and candidate hints

`triggerType` and `rootCausePatternHints[]` are separate by design:

- `triggerType` describes the ANR trigger mechanism.
- `rootCausePatternHints[]` describes candidate root-cause patterns that enrich analysis but never replace trigger classification.

Supported trigger strategies:

| Trigger type | Focus |
|---|---|
| `input_dispatching_timeout` | InputDispatcher timeout, slow dispatch, focused window state, main-thread Binder/lock/IO/render waits, CPU/IO pressure |
| `no_focus_window` | Focus/window/surface lifecycle, Activity resume, relayout, finishDrawing, no focused window |
| `broadcast_timeout` | BroadcastQueue timeout, `Broadcast of Intent`, receiver `onReceive`, `goAsync`, `finish()` |
| `service_timeout` | `Timeout executing service`, foreground service start, service lifecycle, cold start |
| `content_provider_timeout` | Provider publish timeout, provider not responding, provider cold start, query/Binder waits |
| `job_scheduler_timeout` | JobService `onStartJob` / `onStopJob`, JobScheduler dispatch, service lifecycle |
| `system_watchdog_swt` | system_server Watchdog/SWT, monitored handlers, system locks, Binder threads |
| `unknown` | Safe fallback for unknown/future ANR types; preserves baseline evidence and known hints |

Supported root-cause pattern hints:

| Hint | Meaning |
|---|---|
| `deadlock` | Deadlock, self-lock, lock-owner blocked chain, long monitor/mutex contention |
| `memory_leak_oom_pressure` | Memory leak / memory growth / OOM / LMK / PSI memory pressure candidate |
| `high_load_anr` | High CPU, IO wait, load, scheduler pressure, or target/system process overload candidate |

## Interactive AI Agent usage

From the repository root, launch your AI coding agent (Claude Code / Codex CLI / Hermes / etc.) and issue a natural-language request:

```text
Analyze the ANR cause for package com.example.app in <log_directory_path>
```

The agent should:

1. Run `python3 scripts/anr_to_ai.py <path> --package com.example.app`; by default it generates `anr_ai_context/` under the input log directory (`--out-dir` overrides this).
2. Read the `docs/anr-ai-analysis-guide.md` path printed by the command; the guide is no longer duplicated in every `anr_analysis.md`.
3. Open `anr_ai_context/index.json` under the input log directory and analyze each listed ANR workspace independently. Before interpreting evidence, load any applicable [specialized analysis skills](#specialized-anr-analysis-skills) based on the request and that group's reason/signals.
4. Fill the four analysis slots in `anr_ai_context/<anr-id>/anr_analysis.md` under the input log directory in order: Trace → EventLog → Logcat/AnrManager → Final ANR.
5. Read `anr_ai_context/<anr-id>/logcat.txt` in the same workspace whenever the Logcat/AnrManager slot references it.
6. Write the comprehensive synthesis back into the `Final ANR` slot (`#### AI Analysis — 最终 ANR 综合分析`) before replying.
7. After all four slots are filled, run `python3 scripts/reconstruct_anr.py <context-root|group-directory|anr_analysis.md>` to refresh each group's independent reconstruction without re-extracting evidence or rewriting the AI slots.
8. Return a final structured report with timeline, direct blocking point, ranked candidate chains, evidence quality, and remediation suggestions, plus paths to each group's refreshed `anr_reconstruction.md` and `anr_reconstruction.json` alongside `anr_analysis.md`.

## Quick start: generate AI analysis workspaces

Recommended entrypoint:

```bash
python3 scripts/anr_to_ai.py <bugreport_dir_or_archive_or_fixture> \
  [--package <package.name>] \
  [--anr-type input_dispatching_timeout|no_focus_window|broadcast_timeout|service_timeout|content_provider_timeout|job_scheduler_timeout|system_watchdog_swt]

# Equivalent entrypoint (same default artifacts):
python3 -m anr_evidence --build-ai-context <bugreport_dir_or_archive_or_fixture> [--package <package.name>]
```

Default output directory (when the input is a log directory):

```text
<input-log-directory>/anr_ai_context/
  index.json
  anr-<timestamp-or-anchor>/
    anr_analysis.md
    logcat.txt
    anr_reconstruction.md
    anr_reconstruction.json
```

Notes:

- The standalone script writes inside directory inputs or beside archive/fixture inputs by default. The module entrypoint defaults to `./anr_ai_context`; use `--out-dir <directory>` with either entrypoint to select an explicit destination.
- `index.json` is the authoritative directory index for all generated ANR groups.
- `anr_analysis.md` is the human/AI workspace: instructions, filtered evidence summaries, source-specific analysis slots, and the final synthesis slot live together.
- `logcat.txt` contains the filtered logcat lines for that one ANR group.
- `anr_reconstruction.md` and `anr_reconstruction.json` are independent evidence-driven process reconstruction artifacts, generated from logs by default even when AI slots are empty. `index.json` exposes each group's paths as `artifactPaths.reconstruction` and `artifactPaths.reconstructionJson`; the four existing analysis slots and `logcat.txt` remain unchanged.
- Legacy top-level `cache.md`, `ai_prompt.md`, and `summary.json` artifacts are no longer the primary workflow; stale files are cleaned during artifact generation.

Reconstruction separates `observed` log observations, `inferred` mechanism/AI hypotheses, and `unknown` gaps, with per-event time, ΔT, identity, and original-evidence location. Time order does not establish causality: do not invent an unobserved 5s wait or treat a dump snapshot as continuous blocking. This is evidence reconstruction, not real execution replay; existing `replay` semantics remain regression reruns.

After filling all four AI slots, refresh the independent artifacts before delivery:

```bash
python3 scripts/reconstruct_anr.py <context-root|group-directory|anr_analysis.md>
# Equivalent module entrypoint:
python3 -m anr_evidence --reconstruct <context-root|group-directory|anr_analysis.md>
# Optional external AI report, treated only as an unverified candidate explanation:
python3 scripts/reconstruct_anr.py <context-root|group-directory|anr_analysis.md> --ai-report <JSON>
```

A context root processes all its groups; a group directory or analysis file selects one group. The command does not re-extract evidence or rewrite AI slots. Verify external report citations against raw evidence; the report itself must never become an `observed` fact.

Reconstruction and regeneration safeguards:

- Multi-group external AI reports must explicitly map candidates to groups with `{"groups": [{"groupId": "<target-group-id>", "candidateChains": [...]}]}`. References must use an existing event's `eventId` or a unique exact full `rawLine` from that group's `anr_reconstruction.json`; `references_resolved` only confirms that references exist, not that the explanation or causal chain is verified.
- Refresh checks the workspace evidence digest. If filtered evidence or anchor metadata was edited, rerun extraction before refreshing; editing only AI slots does not require re-extraction.
- Regeneration preserves filled AI slots. When the same input's evidence changes, `analysisRevision` marks preserved analysis as `stale` or `unknown`, not revalidated. Reusing an output directory for different inputs checks `inputIdentity` and creates separate groups, preserving old directories and manual analysis; the current index lists only the current input's groups.
- Wall-clock and uptime/boottime evidence are not automatically calibrated into one clock. Unmapped clock domains remain separate; neither timestamp proximity nor a single trace snapshot proves a continuous timeout interval.

Optional per-group evidence and relative-time exports:

```bash
python3 scripts/anr_to_ai.py <input> --build-ess --delta-t-normalized
# Also writes evidence.jsonl and time_normalized.json; no cross-clock calibration.
```

See [the analysis guide](docs/anr-ai-analysis-guide.md) for the evidence boundaries and final delivery requirements.

## Four-phase AI analysis contract

Each `anr_analysis.md` contains four fixed analysis slots that must be filled in order:

1. [`anr-trace-analysis`](skills/anr-trace-analysis.md) → Trace-only analysis in `#### AI Analysis — Trace 堆栈`.
2. [`anr-eventlog-analysis`](skills/anr-eventlog-analysis.md) → EventLog / anchor-only analysis in `#### AI Analysis — EventLog 事件日志`.
3. [`anr-logcat-analysis`](skills/anr-logcat-analysis.md) → Logcat + AnrManager + meminfo follow-up analysis in `#### AI Analysis — Logcat / AnrManager`.
4. [`anr-analysis`](skills/anr-analysis.md) → Final cross-source ANR synthesis in `#### AI Analysis — 最终 ANR 综合分析`.

Constraints:

- The first three phases are source-specific; do not jump to a final root cause there.
- Final ANR synthesis can only run after Trace, EventLog, and Logcat/AnrManager slots are complete.
- Final synthesis must be written back to the same `anr_analysis.md`; do not leave it only in chat.
- Final Markdown should include: comprehensive conclusion, timeline, Trace evidence analysis, EventLog evidence analysis, Logcat/AnrManager evidence analysis, direct blocking point, candidate root-cause chains, evidence quality, remediation suggestions, and a fenced JSON tail.
- Conservative JSON fields stay enabled by default:
  - `finalJudgment = false`
  - `notRootCauseYet = true`
  - `requiresHumanConfirmation = true`

## Specialized ANR analysis skills

These repository-local skills guide the AI agent after baseline extraction; they are not additional CLI trigger types or automatic root-cause verdicts. Read the applicable skill completely before interpreting evidence. A generic `Input dispatching timed out` reason must first be classified as a pre-dispatch focus wait, a published-event completion wait, or an unresolved branch.

### Input dispatch timeout: published-event completion / ACK wait

Use [`input-dispatch-timeout-anr-analysis`](skills/input-dispatch-timeout-anr-analysis/SKILL.md) when explicitly requested (including `input_dispatching_timeout`), or when the same ANR shows a published event awaiting FINISHED, an outstanding connection `waitQueue` / `AnrTracker` record, or a channel `is not responding. Waited ...ms for ...` reason pointing to an unfinished dispatch.

- Correlate the connection/input-channel token and native dispatch seq through publication, per-entry delivery/deadline, receiver handling, finish, FINISHED transport, Dispatcher cleanup, and remaining-queue recovery.
- The earliest-expiring entry can differ from the oldest sent entry described by the reason. Do not infer the configured timeout from `Waited N ms` alone.
- Java finish returning, `handled=false`, or one ACK does not by itself prove the connection recovered. A pure no-focused-window wait belongs to the separate skill below, not this ACK-timeout mechanism.

### No focused window: pre-dispatch target wait

Use [`no-focused-window-anr-analysis`](skills/no-focused-window-anr-analysis/SKILL.md) when explicitly requested (including `no_focus_window`), when the same ANR reports `does not have a focused window`, `no focused window`, `no focus window`, or `no window has focus`, or when a focus-dependent event is waiting for a window while a focused application exists.

- Distinguish the focused application from actual native input-window focus; Activity resume, drawing completion, or a WMS focus request alone does not establish it.
- Reconstruct first target lookup, the effective configured timeout, expiry recheck, reset/recovery, and WMS attribution for the same display/app/window identities.
- This is a wait before target dispatch, not an already-delivered event missing an ACK, and does not alone prove a blocked main thread. Without the wait start and timeout evidence, do not invent a start at `am_anr - 5s`.

### Launcher / Quickstep / Recents animation

Use [`gesture-anr-analysis`](skills/gesture-anr-analysis/SKILL.md) for an explicit Launcher/Quickstep gesture-navigation or Recents-animation ANR request, or when the same ANR contains **both** an input/focus anchor (input timeout, no focused window, or an already-drawn target still `NOT_VISIBLE`) **and** a Launcher/Recents signal such as `recents_animation_input_consumer`, `OtherActivityInputConsumer`, `AbsSwipeUpHandler`, `startRecentsAnimation`, `onTasksAppeared`, `finishRecentsAnimation`, or `mRecentsAnimationStartPending`.

- Correlate `am_anr` with the same gesture `logId` / handler hash, Recents start/callback/finish state, transition visibility, and window focus; support key conclusions with original log excerpts and source locations.
- Framework callback logs do not prove a specific handler received the callback; missing `trackEvent(NO_OP)` text does not prove an event never ran. Temporal proximity only establishes a candidate, not a root cause.
- Do not load this skill for a generic input/no-focus ANR without gesture/Recents signals. It analyzes already-captured logs; missing device dumps remain evidence gaps, not assumed results.

Combine the gesture skill with the ACK-timeout or no-focused-window skill only when each one's trigger is met. If both input branches are evidenced, keep their events and timeout origins separate. All specializations preserve baseline sources, write into the original four slots (no fifth slot), retain conservative flags, and require reconstruction refresh before delivery.

## Deterministic Phase 1-8 pipeline

You can also run the deterministic evidence pipeline without an AI agent:

```bash
# Phase 1: evidence extraction
python3 -m anr_evidence tests/fixtures/nfw_01.json

# Phase 2: normalization
python3 -m anr_evidence --normalize tests/fixtures/nfw_01.json

# Phase 3: assisted non-final analysis
python3 -m anr_evidence --analyze tests/fixtures/nfw_01.json

# Phase 4/5: candidate causal chains
python3 -m anr_evidence --hypothesize tests/fixtures/nfw_01.json

# Phase 6: conservative root-cause candidate report
python3 -m anr_evidence --root-cause tests/fixtures/nfw_01.json

# Phase 7: gated remediation drafts
python3 -m anr_evidence --remediate tests/fixtures/nfw_01.json

# Phase 8: final delivery markdown template
python3 -m anr_evidence --deliver tests/fixtures/nfw_01.json
```

All deterministic phases preserve candidate semantics: they output evidence, gaps, and hypotheses, not an irreversible final verdict.

## Source-specific CLI tools

Standalone filters are useful for debugging, reuse, or validating one source at a time:

```bash
python3 scripts/anr_trace_filter.py <trace_file_or_package>
python3 scripts/anr_event_log_filter.py <event_log_file>
python3 scripts/anr_logcat_filter.py <logcat_file>
python3 scripts/anr_meminfo_filter.py <meminfo_or_bugreport_dir> --package <package.name>
python3 scripts/anr_filter_workflow.py <bugreport_dir_or_archive_or_fixture>
```

Trace preprocessing:

```bash
python3 scripts/anr_preprocessor.py tests/fixtures/nfw_01.json
```

## Input formats

The CLI accepts:

- Fixture JSON: `tests/fixtures/*.json`
- Bugreport directories
- Pre-extracted log directories
- `.zip`
- `.tar`
- `.tar.gz` / `.tgz`
- `.tar.bz2`
- `.tar.xz`

Common recognized paths include:

- `FS/data/anr/traces.txt`
- `data/anr/traces.txt`
- `dropbox/system_app_anr@*.txt`
- `event-log/events.txt`
- `events_log.txt`
- `logs/logcat_*.txt`
- `android-logs/log-main`
- `System_log/meminfo.txt`
- `last_kmsg` / `lastkmsg` / `console_ramoops`

## Python API examples

Build AI context in memory:

```python
from anr_evidence import AiContextOptions, build_ai_context, load_package_from_path

package = load_package_from_path("tests/fixtures/nfw_01.json")
result = build_ai_context(
    package,
    AiContextOptions(
        out_dir="anr_ai_context",
        anr_type="no_focus_window",
    ),
)

print(result.summary())
print(result.groups[0]["rootCausePatternHints"])
```

Generate on-disk AI workspaces:

```python
from anr_evidence import AiContextOptions, build_ai_context_artifacts, load_package_from_path

package = load_package_from_path("tests/fixtures/nfw_01.json")
index = build_ai_context_artifacts(
    package,
    AiContextOptions(out_dir="anr_ai_context", package_name="com.example.app"),
)
print(index["artifactPaths"]["index"])
```

Multi-agent AI analysis entrypoint (requires configured provider/API key):

```python
from anr_evidence import (
    AgentConfig,
    ProviderConfig,
    ProviderKind,
    load_package_from_fixture,
    run_ai_agent_analysis,
)

package = load_package_from_fixture("tests/fixtures/nfw_01.json")
provider = ProviderConfig(kind=ProviderKind.ANTHROPIC, model="claude-sonnet-4-20250514")
result = run_ai_agent_analysis(
    package,
    provider_config=provider,
    agent_config=AgentConfig(provider=provider, max_iterations=3, verbose=True),
)

print(result.integrated_report)
```

## Project structure

```text
anr_evidence/   Core Python package and CLI
scripts/        Standalone entrypoint scripts
tests/          Unit, integration, regression, fixture, and eval tests
docs/           Design notes, operation log, and planning docs
skills/         ANR domain knowledge, source-specific analysis, and specialized ANR skills
```

## Design principles

- **Baseline extraction is the hard guarantee** — critical evidence is always preserved.
- **Type strategies are additive-only** — ANR types add windows, keywords, anchors, and analysis focus; they never remove baseline sources.
- **Trigger type and root-cause hints are separate** — hints enrich analysis but never become final cause by themselves.
- **Unknown / ambiguous ANR type** safely falls back to baseline while keeping any known candidate hints.
- **EventLog `am_anr` is the primary anchor** when present; strict package matching avoids cross-ANR contamination.
- **AnrManager lines are CRITICAL** and must preserve the full dump flow, not only contiguous or narrow-window lines.
- **Meminfo is optional but anchor-aware** and should be used immediately after AnrManager load attribution when present.
- **Direct blocking point is not necessarily the root cause**; upstream causes require cross-source support.
- **All automated/AI analysis output defaults to requiring human confirmation.**

## Verification

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q anr_evidence tests scripts
```

The suite includes regression coverage for per-ANR isolation, specialized skill routing, reconstruction, and untrusted AI references. Use the test runner's summary for current totals and skips; local tests do not establish a real-device root cause.

## Common usage

### Quickly generate AI-ready materials

```bash
python3 scripts/anr_to_ai.py bugreport.zip --package com.example.app
```

Then open these files under the input log directory:

```text
<input-log-directory>/anr_ai_context/index.json
<input-log-directory>/anr_ai_context/<anr-id>/anr_analysis.md
<input-log-directory>/anr_ai_context/<anr-id>/logcat.txt
<input-log-directory>/anr_ai_context/<anr-id>/anr_reconstruction.md
<input-log-directory>/anr_ai_context/<anr-id>/anr_reconstruction.json
```

### No package name available

Omit `--package`; the tool infers anchors from `am_anr`, AnrManager blocks, trace `Cmd line`, or safe fallback evidence.

```bash
python3 scripts/anr_to_ai.py bugreport.zip
```

### Multiple ANRs in one log package

Open `anr_ai_context/index.json`, then analyze each `<anr-id>/anr_analysis.md` independently. Do not merge conclusions across ANR groups unless evidence explicitly links them.

### Deterministic report only, no AI

```bash
python3 -m anr_evidence --deliver tests/fixtures/nfw_01.json > delivery.md
```
