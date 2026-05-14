# ANR Intelligent Analysis System: Architectural Specification (v2.0)

## 1. System Mission
To transform massive, unstructured, and multi-source Android log dumps (Trace, EventLog, Kernel, Logcat) into high-fidelity, actionable root-cause diagnostic reports using an Agent-driven, hierarchical reasoning engine.

##  2. Core Architecture (The "Analyze-by-Evidence" Pipeline)

The system operates on a four-layer decoupled architecture, moving from **Raw Data** $\rightarrow$ **Evidence Slices** $\rightarrow$ **Hypothesis Generation** $\rightarrow$ **Causal Conclusion**.

```mermaid
flowchart TD
    subgraph Input_Layer [Input Layer: Raw Data]
        A[Unstructured Logs<br>Trace/EventLog/Kernel/Logcat]
    end

    subgraph Preprocessing_Layer [Preprocessing Layer: Structural Transformation]
        direction TB
        B1[Python-based Extraction] --> B2[Temporal Alignment Engine<br>Normalize Timestamps to ΔT]
        B2 --> B3[Entity Linkage Engine<br>Bind PID/TID/UID/Package]
        B3 --> B4[Summary Digest Generator<br>Generate Metadata Statistics]
    end

    subgraph Filtering_Layer [Filtering Layer: Semantic Reduction]
        C1[Adaptive Windowing<br>Context-aware Time Window] --> C2[Semantic Weighting<br>Prioritize High-Signal Tags]
        C2 --> C3[Structured Evidence Slices<br>JSONL Format]
    end

    subgraph Reasoning_Layer [Reasoning Layer: Agentic Intelligence]
        direction LR
        D1[Manager Agent<br>Orchestrator] 
        D1 <--> D2[Sub-Agent: CPU/Memory]
        D1 <--> D3[Sub-Agent: Stack/Lock]
        D1 <--> D4[Sub-Agent: I/O/Binder]
        D1 --> D5[Hypothesis Verifier<br>Re-probing/Iterative Scans]
    end

    subgraph Output_Layer [Output Layer: Final Synthesis]
        E[Root-Cause Evidence Graph<br>Markdown Report]
    end

    A --> B1
    B4 --> D1
    B3 --> C1
    C3 --> D1
    D5 --> E
```

---

## 3. Key Design Principles (The "Optimized" Core)

### 3.1 Data Transformation: From "Text" to "Evidence Slices"
The system does **not** pass raw text to the LLM. It passes **Structured Evidence Slices**.
*   **Temporal Alignment ($\Delta T$):** All timestamps (EventLog, Kernel, etc.) are normalized relative to the $T_{anr}$ (the time of the `am_anr` event). All logs are expressed as `timestamp_iso` and `offset_from_anr: +/- Xs`.
*   **Entity Linkage:** The preprocessing layer proactively links disparate identifiers. A thread ID (TID) found in a `trace` is explicitly mapped to its parent Process ID (PID) and Package Name in the metadata, ensuring the Agent does not lose context across files.
*   **The Summary Digest:** Every analysis run generates a "Digest" (a low-token metadata summary). This includes:
    *   *Search Statistics*: Number of errors found, frequency of process deaths.
    *   *Environmental Snapshot*: System-wide pressure (Memory, CPU, I/O).
    *   *Entity Map*: A registry of all active PIDs/UIDs involved in the window.

### 3.2 Intelligent Filtering: Semantic & Adaptive
To prevent "Context Flooding," the filtering layer uses:
*   **Adaptive Windowing**: The time window is not fixed at 12s. For `InputDispatching` ANRs, the window expands to 30s to capture the preceding event stream.
*   **Semantic Sensitivity Weighting**: Tags are assigned a priority weight.
    *   **Level 1 (Critical)**: `am_anr`, `am_crash` (Primary triggers).
    *   **Level 2 (Warning)**: `am_kill`, `binder_transaction_timeout` (Potential causes).
    *   **Level 3 (Contextual)**: `battery_level`, `wm_task_moved` (Environmental background).
    *   *Result*: The Agent receives a high-density stream of high-signal events.

### 3.3 Reasoning Engine: Hypothesis-Driven Verification
The Analysis Layer uses an **Iterative Reasoning Loop** to avoid premature convergence.
1.  **Initial Hypothesis**: Sub-agents analyze the "Summary Digest" and "Evidence Slices" to propose a theory (e.g., "Binder deadlock in PID 1234").
2.  **Iterative Re-sampling (The Probe)**: The Manager Agent can trigger a `re-probe` command. The script is re-run with a new, highly localized time window or a specific focus on a single thread/component.
3.  **Final Synthesis**: The `root-cause-reporter` compiles the causal chain: `[Trigger] $\rightarrow$ [Intermediate Symptom] $\rightarrow$ [Root Cause]`.

---

## 4. Data Schema (The "Contract" between Script and AI)

All analysis-ready data must conform to the **Evidence Slice Schema (ESS)**:

```json
{
  "metadata": {
    "anr_timestamp": "2026-05-01T09:00:15.000Z",
    "target_package": "com.example.app",
    "digest": {
      "total_events_analyzed": 450,
      "critical_findings": ["am_proc_died", "binder_error"],
      "system_pressure": "High (Memory)"
    }
    "entity_map": {
      "pid_1234": { "package": "com.example.app", "uid": 10002 }
    }
  },
  "evidence_slices": [
    {
      "source": "eventlog",
      "timestamp_iso": "2026-05-01T09:00:03.000Z",
      "delta_t_seconds": -12.0,
      "tag": "am_proc_died",
      "content": "am_proc_died: [0,1234,com.example.app,...]",
      "importance": "CRITICAL"
    },
    {
      "source": "kernel",
      "timestamp_iso": "2026-05-01T09:00:10.000Z",
      "delta_t_seconds": -5.0,
      "tag": "oom_killer",
      "content": "Out of memory: Kill process 1234...",
      "importance": "WARNING"
    }
  ]
}
```

## 5. Implementation Roadmap
*   [x] **Architecture Design (v2.0)**
*   [ ] **Phase 1**: Implement `Temporal Alignment` & `Summary Digest` in `anr_preprocessor.py`.
*   [ ] **Phase 2**: Implement `Semantic Weighting` in `anr_log_pattern_filter.py`.
*   [ ] **Phase 3**: Implement `Hypothesis-driven` Agent loop with Re-probing capability.
