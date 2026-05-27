# agent-anr

> [中文版](README-zh.md)

`agent-anr` is an Agent-driven Android ANR (App Not Responding) evidence extraction and AI-assisted analysis pipeline. It turns raw traces, EventLog, logcat, AnrManager dumps, meminfo, and kernel logs into auditable per-ANR evidence workspaces, then guides an AI/human analyst through conservative source-by-source analysis.

The project is optimized for **high recall and auditability**, not premature final blame: trigger classification, root-cause pattern hints, and remediation drafts remain evidence-backed candidates until a human confirms them.

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

1. Run `python3 scripts/anr_to_ai.py <path> --package com.example.app` to generate `anr_ai_context/`.
2. Open `anr_ai_context/index.json` and analyze each listed ANR workspace independently.
3. Fill the four analysis slots in `anr_ai_context/<anr-id>/anr_analysis.md` in order: Trace → EventLog → Logcat/AnrManager → Final ANR.
4. Read `anr_ai_context/<anr-id>/logcat.txt` whenever the Logcat/AnrManager slot references it.
5. Write the comprehensive synthesis back into the `Final ANR` slot before replying.
6. Return a final structured report with timeline, direct blocking point, ranked candidate chains, evidence quality, and remediation suggestions.

## Quick start: generate AI analysis workspaces

Recommended entrypoint:

```bash
python3 scripts/anr_to_ai.py <bugreport_dir_or_archive_or_fixture> \
  [--package <package.name>] \
  [--anr-type input_dispatching_timeout|no_focus_window|broadcast_timeout|service_timeout|content_provider_timeout|job_scheduler_timeout|system_watchdog_swt]
```

Default output directory:

```text
anr_ai_context/
  index.json
  anr-<timestamp-or-anchor>/
    anr_analysis.md
    logcat.txt
```

Notes:

- `index.json` is the authoritative directory index for all generated ANR groups.
- `anr_analysis.md` is the human/AI workspace: instructions, filtered evidence summaries, source-specific analysis slots, and the final synthesis slot live together.
- `logcat.txt` contains the filtered logcat lines for that one ANR group.
- Legacy top-level `cache.md`, `ai_prompt.md`, and `summary.json` artifacts are no longer the primary workflow; stale files are cleaned during artifact generation.

## Four-phase AI analysis contract

Each `anr_analysis.md` contains four fixed analysis slots that must be filled in order:

1. `anr-trace-analysis` → Trace-only analysis.
2. `anr-eventlog-analysis` → EventLog / anchor-only analysis.
3. `anr-logcat-analysis` → Logcat + AnrManager + meminfo follow-up analysis.
4. `anr-analysis` → Final cross-source ANR synthesis.

Constraints:

- The first three phases are source-specific; do not jump to a final root cause there.
- Final ANR synthesis can only run after Trace, EventLog, and Logcat/AnrManager slots are complete.
- Final synthesis must be written back to the same `anr_analysis.md`; do not leave it only in chat.
- Final Markdown should include: comprehensive conclusion, timeline, Trace evidence analysis, EventLog evidence analysis, Logcat/AnrManager evidence analysis, direct blocking point, candidate root-cause chains, evidence quality, remediation suggestions, and a fenced JSON tail.
- Conservative JSON fields stay enabled by default:
  - `finalJudgment = false`
  - `notRootCauseYet = true`
  - `requiresHumanConfirmation = true`

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
skills/         ANR domain knowledge and source-specific AI analysis skills
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

Current test suite: 281 tests (1 skipped in the latest local run).

## Common usage

### Quickly generate AI-ready materials

```bash
python3 scripts/anr_to_ai.py bugreport.zip --package com.example.app
```

Then open:

```text
anr_ai_context/index.json
anr_ai_context/<anr-id>/anr_analysis.md
anr_ai_context/<anr-id>/logcat.txt
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
