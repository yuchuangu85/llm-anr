# A1 capability-driven Agent router and bounded re-probe contract

A1 changes only the existing three local specialist roles (`cpu_memory`, `stack_lock`, `io_binder`).  It does not introduce a new remote tool, a shell/search executor, an autonomous download, or a causal-confidence promotion path.

## Router

`route_sub_agents(slices, max_agents=...)` returns one auditable `AgentRoute` per existing role:

- `selected=true` only when the role has at least one supported source and a non-empty focused view;
- `capability_sources` records the source kinds that made it available;
- `reason` is one of `source_capability_available`, `no_supported_source`, `no_relevant_focused_evidence`, or `routing_budget_exhausted`;
- selection order is the stable existing `SUB_AGENT_SPECS` order, and `max_routed_sub_agents` is a hard per-iteration cap.

A routing decision is not a diagnosis.  Every manager prompt includes the selected/skipped audit trail, and `AiAgentResult.routing_decisions` retains it for every iteration.

## Re-probe request semantics

The manager wire format remains:

```json
{
  "beforeSeconds": 3,
  "afterSeconds": 3,
  "sourceKinds": ["logcat"],
  "additionalTags": ["binder_reply"],
  "reason": "verify the binder reply observation"
}
```

`additionalTags` are now executed, not merely logged:

1. select only requested source kinds and the requested anchor-relative time window;
2. normalize each ASCII evidence tag case-insensitively, treating `_`, `.`, `:`, `/`, `-`, and spaces as equivalent separators;
3. retain records whose tag or content contains one normalized requested tag;
4. additionally retain only a scoped EventLog `am_anr` or AnrManager identity anchor from that same view.

The probe does **not** retain every `critical` line.  Broad classifier criticality must not silently defeat a tag narrowing request.  A tag hit without an anchor remains an observation, not causal proof.

At the schema boundary, `sourceKinds` must be nonempty and unique; `additionalTags` allow at most eight, must be ASCII evidence tags of at most 64 characters, and cannot contain separator/case-equivalent duplicates.

## Hard budgets

`AgentConfig` adds conservative defaults:

| Field | Default | Hard range / effect |
| --- | ---: | --- |
| `max_routed_sub_agents` | 3 | 1–3 per iteration |
| `max_re_probe_requests` | 2 | 0–2 per run |
| `max_probe_window_seconds` | 30 | 1–300 seconds in either direction |
| `max_probe_source_kinds` | 4 | 1–number of supported source kinds |
| `max_probe_tags` | 8 | 0–8 |
| `max_probe_results` | 128 | 1–1024; over-budget probe is rejected, not silently truncated |
| `max_probe_wall_seconds` | 3.0 | 0.01–30 seconds for local rebuild/filter work |
| `max_total_tokens` | 49,152 | 1–262,144 reported-token ceiling for the whole run |

Before an iteration, the runner reserves `(selected specialists + manager) × ProviderConfig.max_tokens`; it does not start that iteration when the reservation exceeds the total-token cap.  Provider-reported prompt/output usage is checked again after each response.  The latter is an audit/stop check because a provider can report usage only after a request returns.

Provider request-size, response-size, retry, HTTP-attempt, timeout, outbound-policy, evidence-reference, and manager-report validation boundaries remain independently enforced.

## Stop conditions

A manager report asking for a re-probe is **not accepted** when any of these occurs:

- final iteration reached;
- probe-count, window, source, tag, result, wall-time, or token budget is exceeded;
- no qualifying source/window/tag evidence exists;
- store-reference binding fails after re-filtering;
- truncation retains no probe evidence.

`AiAgentResult.probe_outcomes` records the status, reason, elapsed time, source/result counts, and normalized tags.  The result continues to require `finalJudgment=false`, `notRootCauseYet=true`, and `requiresHumanConfirmation=true`.
