# agent-anr

> [中文版](README-zh.md)

`agent-anr` is an Agent-driven Android ANR (App Not Responding) evidence extraction and AI-assisted analysis pipeline. Its core goal isn't to render a definitive verdict — instead, it transforms raw materials (traces, EventLog, logcat, AnrManager, meminfo, kernel logs) into an auditable evidence package, then produces conservative candidate root-cause reports through a fixed set of steps.

## Core capabilities

- Auto-discovers ANR evidence sources from fixture JSON, bugreport directories, and ZIP/TAR archives.
- Groups evidence independently by ANR anchor — multiple ANRs in the same log produce isolated contexts so evidence never bleeds across.
- High-recall filtering: traces, EventLog pre-`am_anr` window, logcat trigger window, AnrManager dump flow, meminfo follow-up, kernel log.
- Structured trace parsing: main thread, thread states, schedstat, lock waits, Binder waits, Render/GPU waits, deadlock detection, trace hints.
- Conservative analysis pipeline: normalize → analyze → hypothesize → root-cause → remediate → deliver.
- AI-ready `anr_analysis.md`: four fixed-phase analysis slots for structured human/LLM fill-in.

## Quick start: generating AI analysis context

Recommended entrypoint:

```bash
python3 scripts/anr_to_ai.py <bugreport_dir_or_archive_or_fixture> \
  [--package <package.name>] \
  [--anr-type no_focus_window|input_dispatching_timeout]
```

Default output directory is `anr_ai_context/`:

- Top-level `index.json` — directory index linking to all ANR groups
- One directory per ANR: `anr_ai_context/<anr-id>/anr_analysis.md`

`anr_analysis.md` is the only human/AI workspace file: AI instructions, filtered evidence, and inline analysis slots all live in one file for easy cross-reference back to source.

## Four-phase AI analysis workflow

`anr_analysis.md` contains four fixed analysis slots that must be filled in order:

1. `anr-trace-analysis` → `#### AI Analysis — Trace`
2. `anr-eventlog-analysis` → `#### AI Analysis — EventLog`
3. `anr-logcat-analysis` → `#### AI Analysis — Logcat/AnrManager`
4. `anr-analysis` → `#### AI Analysis — Final ANR`

Constraints:

- The first three phases perform source-specific analysis only — no premature root-cause conclusions.
- Final ANR integrates across all sources and can only run after the first three are complete.
- The comprehensive synthesis must be written back to the `#### AI Analysis — Final ANR` slot in the same `anr_analysis.md` file. Structure: `## Comprehensive Analysis Conclusion`, then Timeline, Direct blocking point, Candidate root-cause chains, Evidence quality, Remediation suggestions, and a JSON tail. Do not output the final synthesis in chat alone.
- All final outputs preserve conservative fields:
  - `finalJudgment = false`
  - `notRootCauseYet = true`
  - `requiresHumanConfirmation = true`

## Supported ANR type strategies

| ANR type | Key signals |
|---|---|
| `input_dispatching_timeout` | InputDispatcher timeout, slow dispatch, main thread Binder/lock/IO/render wait, CPU/IO pressure |
| `no_focus_window` | Focus/window/surface lifecycle, Activity resume, relayout, finishDrawing, no focused window |
| `unknown` | Falls back to safe baseline, preserves critical evidence, never drops sources |

> Type templates are additive-only: when the type is unknown or ambiguous, only the conservative baseline is added — no critical evidence is ever removed.

## Deterministic pipeline

Beyond AI context generation, you can run the deterministic Phase 1–8 pipeline:

```bash
# Phase 1: Evidence extraction
python3 -m anr_evidence tests/fixtures/nfw_01.json

# Phase 2: Normalization
python3 -m anr_evidence --normalize tests/fixtures/nfw_01.json

# Phase 3: Assisted analysis (non-final)
python3 -m anr_evidence --analyze tests/fixtures/nfw_01.json

# Phase 4: Candidate causal chains
python3 -m anr_evidence --hypothesize tests/fixtures/nfw_01.json

# Phase 5: Conservative root-cause report v1
python3 -m anr_evidence --root-cause tests/fixtures/nfw_01.json

# Phase 6: Gated remediation drafts
python3 -m anr_evidence --remediate tests/fixtures/nfw_01.json

# Phase 7: Final delivery markdown template
python3 -m anr_evidence --deliver tests/fixtures/nfw_01.json
```

All phases follow the conservative principle: output candidate chains, supporting evidence, gaps, and human confirmation requirements — never treating a single snapshot as the final verdict.

## Source-specific filters

Individual log filters can be run standalone for debugging or reuse:

```bash
python3 scripts/anr_trace_filter.py <trace_file_or_package>
python3 scripts/anr_event_log_filter.py <event_log_file>
python3 scripts/anr_logcat_filter.py <logcat_file>
python3 scripts/anr_meminfo_filter.py <meminfo_file>
python3 scripts/anr_filter_workflow.py <bugreport_dir_or_archive_or_fixture>
```

Trace preprocessing entrypoint:

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

The directory loader recognizes common vendor/system paths, including:

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
print(result.ai_prompt_markdown[:1000])
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
tests/          Unit, integration, and regression tests
docs/           Design notes, operation log, and planning docs
skills/         ANR domain knowledge & AI analysis skill docs
```

## Design principles

- **Baseline extraction is the hard guarantee** — critical evidence is always preserved.
- **Type templates are additive-only** — different ANR types only add focus areas; they never remove baseline sources.
- **Unknown / ambiguous ANR type** safely falls back to baseline.
- **AnrManager lines are always CRITICAL** — they contain the system ANR diagnostic summary.
- **Multi-ANR input must be split by anchor** — cross-ANR reuse of AnrManager, meminfo, trace, or logcat evidence is forbidden.
- **The direct blocking point is not the final root cause** — output candidate chains only when upstream evidence is absent.
- **All automated/AI analysis output defaults to requiring human confirmation.**

## Verification

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q anr_evidence tests scripts
```

Current test suite: ~270+ tests.

## Common usage

### Quickly generate AI-ready analysis materials

```bash
python3 scripts/anr_to_ai.py bugreport.zip --package com.example.app
```

Then open:

```text
anr_ai_context/<anr-id>/anr_analysis.md
```

### No package name available

Omit `--package` — the tool infers it from `am_anr`, the AnrManager block, or the trace `Cmd line`.

```bash
python3 scripts/anr_to_ai.py bugreport.zip
```

### Multiple ANRs in a single log

Open `anr_ai_context/index.json`, then analyze each `<anr-id>/anr_analysis.md` individually.

### Deterministic report only (no AI)

```bash
python3 -m anr_evidence --deliver tests/fixtures/nfw_01.json > delivery.md
```

### Interactive AI Agent usage

cd into the agent-anr directory, launch your AI coding agent (Claude Code / Codex CLI / Hermes / etc.), then type a natural language command:

```text
Analyze the ANR cause for package com.example.app in <log_directory_path>
```

The agent will automatically:

1. Run `python3 scripts/anr_to_ai.py <path> --package com.example.app` to generate the AI context
2. Read and analyze the evidence across all four phases (Trace → EventLog → Logcat/AnrManager → Final ANR)
3. Write the comprehensive synthesis back into `anr_ai_context/<anr-id>/anr_analysis.md`
4. Output a final structured report with timeline, direct blocking point, candidate root-cause chains, evidence quality assessment, and remediation suggestions
