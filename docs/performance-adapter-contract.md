# P1 performance adapter contract

P1 adds a deliberately narrow, **local and fail-closed** observation boundary for a provisioned Perfetto Trace Processor binary and a packaged AOSP WinScope release archive.  It does not add a default dependency, a downloader, a graphical viewer, a root-cause signal, or a cross-clock correlation rule.

## Public API and CLI

```python
from anr_evidence import (
    inspect_winscope_release_artifact,
    probe_performance_capabilities,
    run_perfetto_scheduling_query,
)
```

```bash
# Capability probe only.
python3 scripts/run_performance_adapter.py --config /absolute/path/performance-tools.json

# The only executable P1 query: exact process PID scheduling rows.
python3 scripts/run_performance_adapter.py \
  --config /absolute/path/performance-tools.json \
  --trace /absolute/path/capture.pftrace \
  --target-pid 1234
```

The CLI reads only the supplied JSON and local artifact paths.  It does not modify the default `python -m anr_evidence` workflow.

## Explicit configuration

Every configured path must be absolute.  The adapter does not search `PATH`, accept a URL/share link, fetch a binary, unpack an archive, or launch a GUI.

```json
{
  "schemaVersion": "performance-tool-config-v1",
  "perfetto": {
    "binaryPath": "/absolute/path/to/trace_processor_shell",
    "allowedToolSha256": [
      "lowercase SHA-256 of the exact provisioned executable"
    ],
    "expectedVersion": "opaque full --version stdout after trailing newline removal",
    "timeoutSeconds": 30,
    "maxVersionOutputBytes": 16384,
    "maxQueryOutputBytes": 262144,
    "maxRows": 256
  },
  "winscope": {
    "archivePath": "/absolute/path/to/aosp-winscope.zip",
    "expectedSha256": "lowercase SHA-256 of the exact ZIP",
    "expectedVersion": "17.0.1",
    "expectedReleaseManifestSchemaVersion": 1,
    "expectedManifestSchemaVersion": 1,
    "expectedTarget": {
      "operatingSystem": "darwin",
      "architecture": "arm64"
    },
    "maxManifestBytes": 65536,
    "maxArchiveEntries": 10000
  }
}
```

`--version` text is recorded as opaque audit evidence and matched exactly only because the caller pins it in this profile.  The durable approval is the configured SHA-256 allowlist; the adapter does not infer semantic-version compatibility from a partial version string.

## Perfetto scheduling adapter

`run_perfetto_scheduling_query(...)` accepts no SQL argument.  After all path, executable, SHA-256, exact-version and bounded-output checks pass, it calls the configured executable with a fixed argv equivalent to:

```text
trace_processor_shell query -f - /absolute/path/capture.pftrace
```

The fixed SQL constrains `process.pid` to the validated positive integer and selects only numeric scheduling fields:

```sql
SELECT
  s.ts AS ts,
  COALESCE(s.dur, -1) AS dur,
  COALESCE(s.cpu, -1) AS cpu,
  t.tid AS tid
FROM sched AS s
JOIN thread AS t USING (utid)
JOIN process AS p USING (upid)
WHERE p.pid = <validated PID>
ORDER BY s.ts
LIMIT <configured hard cap>;
```

The parser accepts only the exact CSV header `ts,dur,cpu,tid`, bounded numeric rows, and ordered non-negative timestamps.  A missing tool, mismatched hash/version, non-zero exit, timeout, output limit breach, unknown output schema, non-local trace, or invalid PID produces a structured `blocked` or `unsupported` result with no scheduling rows.

An observed row means only that the configured binary ran this fixed query against that local file.  P1 intentionally retains all of these gaps:

- `clock.domain = perfetto_trace`, `unit = ns`, and `alignmentStatus = unmapped`;
- no EventLog/UTC/bugreport clock conversion;
- no process-name mapping beyond the SQL PID constraint;
- `trace_capture_provenance_unverified` unless a later hash-bound capture-manifest adapter supplies that evidence;
- no integration into causal-confidence or root-cause promotion.

The adapter itself does not download tools.  It cannot prove that an arbitrary externally supplied executable has no network egress; that needs an execution-environment boundary such as a sandbox or CI egress policy.

## WinScope archive adapter

`inspect_winscope_release_artifact(...)` is metadata-only.  It streams the archive SHA-256, opens the ZIP without extracting it, rejects unsafe archive member names and duplicate/missing manifests, bounds manifest reads, then verifies:

- `manifest.json` schema version;
- `release-manifest.json` schema version and release version;
- exact OS/architecture target;
- a safe, present launcher member.

A verified archive is reported as `capabilityMode: "metadata_only"`.  The P1 result explicitly retains `winscope_graphical_query_adapter_not_implemented`: it does not open or render a trace, parse a graphical trace, infer data sources from a filename, launch `winscope-launcher`, or claim that an artifact is compatible with a device capture.

If a future graphical dump adapter is attached to an ANR group, it must accept and project that group’s existing DQ1 `dumpLifecycle` record.  It may add source-local, immutable-reference-backed observations only after an explicit parser/consumer exists; it must not create an adapter-local stale/wrong-PID/wrong-process/association classifier, use archive validity as identity proof, or promote freshness, process lifetime, capture-time alignment, causal confidence, or root cause.  The current metadata-only WinScope adapter is intentionally not such a consumer.

## Result safety invariant

All capability, scheduling, and archive reports include:

```json
{
  "finalJudgment": false,
  "notRootCauseYet": true,
  "requiresHumanConfirmation": true
}
```

They are capability/provenance observations, not a diagnosis.
