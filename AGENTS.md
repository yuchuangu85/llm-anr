# agent-anr — ANR evidence extraction and analysis

Agent-driven Android ANR (App Not Responding) analysis pipeline: evidence extraction → normalization → assisted analysis → hypothesis → root-cause report → remediation drafts → final delivery.

## ANR Analysis Workflow

When a user asks you to analyze an ANR bugreport (providing a directory, ZIP, TAR, or fixture JSON), follow this workflow:

### Step 1: Generate AI context

```bash
python3 scripts/anr_to_ai.py <path_to_bugreport> [--package <pkg>] [--anr-type <type>]
# Equivalent entrypoint:
python3 -m anr_evidence --build-ai-context <path_to_bugreport> [--package <pkg>] [--anr-type <type>]
```

This produces `anr_ai_context/` containing:
- `index.json` — directory index linking to all ANR groups
- `<group-id>/anr_analysis.md` — AI instructions + filtered evidence + inline analysis slots (one per ANR)
- `<group-id>/logcat.txt` — full filtered logcat referenced from `anr_analysis.md`
- `<group-id>/anr_reconstruction.md` and `anr_reconstruction.json` - independent evidence-driven process reconstruction, generated from logs by default even before AI slots are filled

`index.json` exposes each group's reconstruction paths as `artifactPaths.reconstruction` and `artifactPaths.reconstructionJson`. The four `anr_analysis.md` slots and `logcat.txt` remain unchanged. Reconstruction separates `observed` (log observations), `inferred` (mechanism/AI hypotheses), and `unknown` (gaps), with per-event time, delta T, identity, and original-evidence location. Time order is not causality; never invent an unobserved 5s interval or treat a dump snapshot as continuous blocking. This is evidence reconstruction, not real execution replay; existing replay commands remain regression reruns. See `docs/anr-ai-analysis-guide.md` for details.

(Older runs produced top-level `cache.md`/`ai_prompt.md`/`summary.json`; those artifacts are obsolete and are deleted automatically on regeneration.)

### Step 2: Read and analyze the evidence

Read `anr_ai_context/<group-id>/anr_analysis.md` and perform the analysis following the instructions within — the file tells you exactly how to structure the output (timeline, blocking point, candidate root-cause chains, evidence quality assessment, remediation suggestions).
Write each source-specific conclusion directly into the `#### AI Analysis — <source>` slots in the same file.
The final comprehensive report must be placed under the `#### AI Analysis — 最终 ANR 综合分析` slot (for example starting with `## 综合分析结论` followed by Timeline / Direct blocking point / Candidate root-cause chains / Evidence quality / Remediation suggestions / JSON tail); do not leave the synthesis only in chat.

After filling all four AI slots, refresh the independent reconstruction for each group before delivery:

```bash
python3 scripts/reconstruct_anr.py <context-root|group-directory|anr_analysis.md>
```

A context root processes all its groups; a group directory or analysis file targets one group. This refresh does not re-extract evidence or rewrite AI slots. Optional `--ai-report <JSON>` adds an external AI report only as an unverified candidate explanation: verify its citations and never promote the report itself to an observed fact.

### Step 3: Output the analysis report

Output a structured Markdown report covering:
1. Timeline of key events from trace/EventLog/AnrManager/logcat
2. Direct blocking point with supporting evidence
3. Candidate root-cause chains ranked by confidence
4. Evidence quality assessment (gaps, contradictions)
5. Remediation suggestions

Include the refreshed per-group `anr_reconstruction.md` and `anr_reconstruction.json` paths in the final delivery alongside `anr_analysis.md`.

## Input Dispatch Timeout ANR specialization

Load and follow [the input-dispatch-timeout skill](skills/input-dispatch-timeout-anr-analysis/SKILL.md) when the user requests input-dispatch-timeout analysis / `input_dispatching_timeout`, or the same ANR shows a published event awaiting FINISHED, an outstanding connection waitQueue, or a channel `is not responding. Waited ...ms for ...` reason. Classify generic `Input dispatching timed out` first; a pure no-focused-window wait belongs to the separate skill below, not this ACK-timeout mechanism.

Use it additively after baseline extraction: correlate connection token/native seq, per-entry delivery/deadline, finish/FINISHED/cleanup, the actual expiry versus reason entry, and remaining-queue recovery. A Java finish return, handled=false, or one ACK does not prove the connection has recovered. Fill the four existing analysis slots, preserve conservative flags, and refresh the independent reconstruction before delivery. Load the gesture skill only when its own Launcher/Recents trigger is met.

## No Focused Window ANR specialization

Load and follow [the no-focused-window skill](skills/no-focused-window-anr-analysis/SKILL.md) before interpreting evidence when the user explicitly requests no-focused-window analysis / `no_focus_window`, or the same ANR's reason/logs show `does not have a focused window`, `no focused window`, `no focus window`, `no window has focus`, or a focus-dependent event waiting for a window while a focused application exists.

Use it additively after baseline extraction: distinguish focused application from actual native window focus, reconstruct target lookup / configured timeout / expiry recheck / reset, and verify WMS attribution and recovery. A generic input timeout, a focus change, or a delivered-event ACK timeout alone does not establish this branch. Preserve all baseline evidence and fill the four existing source/final slots, with synthesis under `#### AI Analysis — 最终 ANR 综合分析`.

If the same ANR also meets the Launcher/Quickstep/Recents trigger below, load both skills: use no-focused-window analysis for the focus-wait mechanism and gesture analysis for the upstream Recents/transition chain. Neither trigger alone proves the root cause.

## Launcher / Quickstep Recents Animation ANR specialization

Load and follow [`skills/gesture-anr-analysis/SKILL.md`](skills/gesture-anr-analysis/SKILL.md) completely before interpreting evidence when either condition is true:

1. The user explicitly asks to analyze a Launcher/Quickstep gesture-navigation or Recents-animation ANR.
2. The evidence contains both:
   - an input/focus anchor such as `Input dispatching timed out`, `does not have a focused window`, or an already-drawn target window that remains `NOT_VISIBLE`; and
   - a Launcher/Quickstep Recents signal such as `recents_animation_input_consumer`, `OtherActivityInputConsumer`, `AbsSwipeUpHandler`, `startRecentsAnimation`, `onTasksAppeared`, `finishRecentsAnimation`, or `mRecentsAnimationStartPending`.

Do not load this specialization for a generic input timeout or no-focus ANR that has no gesture/Recents signal. When it triggers, use it as an additive specialization after the baseline extraction workflow: preserve all baseline evidence, correlate `am_anr` with gesture identity, Recents lifecycle, finish state, and window/focus evidence, then write the source-specific and final conclusions back to the generated `anr_analysis.md` slots.

### Quick reference: ANR type strategies

| Type | Key signals |
|------|-------------|
| `input_dispatching_timeout` | InputDispatcher timeout, no focused window, main thread blocked on binder/fence/lock |
| `no_focus_window` | Window focus loss, Activity not resumed, surface not ready |
| `unknown` | Broader window (30s), generic signal patterns |

### If the user provides no package name

Run without `--package` and infer the package from the evidence (the `AnrManager` block or `am_anr` line will contain it).

## Project layout

```
anr_evidence/          # Core Python library (CLI via anr_evidence.cli / -m anr_evidence)
scripts/               # Standalone entrypoint scripts
tests/                 # Unit + integration tests (discoverable via unittest)
docs/                  # Algorithm design docs + gap analysis
wiki/                  # ANR domain reference material
```

## Key commands

```bash
# === Primary workflow: AI context generation ===
python3 scripts/anr_to_ai.py <input> [--package <pkg>] [--anr-type <type>]

# === Deterministic pipeline (Phases 1-8) ===
python3 -m anr_evidence tests/fixtures/nfw_01.json
python3 -m anr_evidence --analyze tests/fixtures/nfw_01.json
python3 -m anr_evidence --report tests/fixtures/nfw_01.json
python3 -m anr_evidence --deliver tests/fixtures/nfw_01.json

# === Multi-Agent AI Analysis (requires API key) ===
python3 -c "
from anr_evidence import run_ai_agent_analysis, ProviderConfig, ProviderKind, AgentConfig
from anr_evidence import load_package_from_fixture
package = load_package_from_fixture('tests/fixtures/nfw_01.json')
provider_config = ProviderConfig(kind=ProviderKind.ANTHROPIC, model='claude-sonnet-4-20250514')
result = run_ai_agent_analysis(
    package,
    provider_config=provider_config,
    agent_config=AgentConfig(provider=provider_config, max_iterations=3, verbose=True),
)
# result.integrated_report contains candidateChains/candidateConclusions/remediationDrafts
"

# === Standalone scripts ===
python3 scripts/anr_preprocessor.py tests/fixtures/nfw_01.json
python3 scripts/anr_log_pattern_filter.py path/to/events.txt --tags docs/event-log-tags-reference.md
python3 scripts/extract_bugreport.py path/to/bugreport.zip -o output_dir/
python3 scripts/compare_replays.py path/to/run_a path/to/run_b
python3 scripts/web_server.py --port 8080

# === Replay ===
python3 -m anr_evidence --replay samples/replay/manifest.json --replay-out /tmp/anr-replay
python3 scripts/run_replay.py samples/replay/manifest.json --out-root samples/replay/runs --label nightly
```

## API — use from Python (within agent session)

```python
from anr_evidence import (
    # AI context
    build_ai_context, AiContextOptions, AiContextResult,
    # Package loading
    load_package_from_archive, load_package_from_directory, load_package_from_fixture,
    # Multi-Agent AI
    run_ai_agent_analysis, ProviderConfig, ProviderKind, AgentConfig,
    # Evidence Slice Schema
    EvidenceSlice, build_evidence_slices, annotate_slices_with_tags,
    # Entity linkage
    EntityMap, build_entity_map, entity_summary_for_ai,
    # Weighting
    ImportanceLevel, EVENT_LOG_TAG_WEIGHTS, filter_by_importance,
    # Context flooding prevention
    TruncationConfig, truncate_evidence,
    # Time normalization
    TimeNormalizedLine, compute_delta_t,
)
```

## Design rules

- Baseline extraction is the hard guarantee — never remove baseline evidence
- Type templates are additive only — never delete evidence sources
- Unknown/ambiguous ANR type falls back safely to baseline extraction
- Critical evidence retained > noise reduced
- All analysis output is conservative: `finalJudgment = false`, `notRootCauseYet = true`, `requiresHumanConfirmation = true`
- AnrManager lines are always CRITICAL — they contain the ANR diagnostic summary
- Use the AnrManager block as a secondary anchor for logcat, cross-referencing with event_log's am_anr timestamp

## Test

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q anr_evidence tests
```
