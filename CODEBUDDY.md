# agent-anr — ANR evidence extraction and analysis

Agent-driven Android ANR (App Not Responding) analysis pipeline: evidence extraction → normalization → assisted analysis → hypothesis → root-cause report → remediation drafts → final delivery.

## ANR Analysis Workflow

When a user asks you to analyze an ANR bugreport (providing a directory, ZIP, TAR, or fixture JSON), follow this workflow:

### Step 1: Generate AI context

```bash
python3 scripts/anr_to_ai.py <path_to_bugreport> [--package <pkg>] [--anr-type <type>]
```

This produces `anr_ai_context/` containing:
- `index.json` — directory index linking to all ANR groups
- `<group-id>/anr_analysis.md` — AI instructions + filtered evidence + inline analysis slots (one per ANR)

### Step 2: Read and analyze the evidence

Read `anr_ai_context/<group-id>/anr_analysis.md` and perform the analysis following the instructions within — the prompt tells you exactly how to structure the output (timeline, blocking point, candidate root-cause chains, evidence quality assessment, remediation suggestions). Write results directly into the `#### AI Analysis — <source>` slots. The comprehensive synthesis must be written back under `#### AI Analysis — Final ANR` in the same file (for example starting with `## 综合分析结论`), not only returned in chat.

### Step 3: Output the analysis report

Output a structured Markdown report covering:
1. Timeline of key events from trace/EventLog/AnrManager/logcat
2. Direct blocking point with supporting evidence
3. Candidate root-cause chains ranked by confidence
4. Evidence quality assessment (gaps, contradictions)
5. Remediation suggestions

### Quick reference: ANR type strategies

| Type | Key signals |
|------|-------------|
| `input_dispatching_timeout` | InputDispatcher timeout, no focused window, main thread blocked on binder/fence/lock |
| `no_focus_window` | Window focus loss, Activity not resumed, surface not ready |
| `unknown` | Broader window (30s), generic signal patterns |

### If the user provides no package name

Run without `--package` and infer the package from the evidence (the `AnrManager` block or `am_anr` line will contain it).

### ANR Domain Knowledge (wiki skills)

When deeper ANR domain expertise is needed during analysis, load the relevant skill from `skills/`:

| Analysis phase | Skill to load |
|---------------|---------------|
| 1. Confirm ANR type and characteristics | `skills/anr-classification.md` |
| 2. Understand triggering mechanism | `skills/anr-principle.md` |
| 3. Follow standard analysis flow | `skills/anr-analysis.md` |
| 4. Identify root cause from trace/log patterns | `skills/anr-root-cause.md` |
| 5. Assess system load (CPU/memory/IO) | `skills/anr-load.md` |
| 6. Look up external references | `skills/anr-reference.md` |

## Project layout

```
anr_evidence/          # Core Python library (CLI via anr_evidence.cli / -m anr_evidence)
scripts/               # Standalone entrypoint scripts
tests/                 # Unit + integration tests (171 tests, discoverable via unittest)
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

# === Test ===
python3 -m unittest discover -s tests -v
```

## Design rules

- Baseline extraction is the hard guarantee — never remove baseline evidence
- Type templates are additive only — never delete evidence sources
- Unknown/ambiguous ANR type falls back safely to baseline extraction
- Critical evidence retained > noise reduced
- All analysis output is conservative: `finalJudgment = false`, `notRootCauseYet = true`, `requiresHumanConfirmation = true`
- AnrManager lines are always CRITICAL — they contain the ANR diagnostic summary
- Use the AnrManager block as a secondary anchor for logcat, cross-referencing with event_log's am_anr timestamp
