"""Replay runs dashboard rendering utilities."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


class DashboardError(ValueError):
    """Raised when replay dashboard input is invalid."""



def render_replay_dashboard(index_or_path: dict[str, Any] | str | Path, *, format: str = "markdown") -> str:
    index = _load_index(index_or_path)
    if format == "markdown":
        return _render_markdown(index)
    if format == "html":
        return _render_html(index)
    raise DashboardError(f"Unsupported dashboard format `{format}`. Use `markdown` or `html`.")



def _load_index(index_or_path: dict[str, Any] | str | Path) -> dict[str, Any]:
    if isinstance(index_or_path, dict):
        index = index_or_path
    else:
        index = json.loads(Path(index_or_path).read_text(encoding="utf-8"))
    if not isinstance(index, dict) or "sessions" not in index:
        raise DashboardError("Replay dashboard requires a replay index with `sessions`.")
    return index



def _render_markdown(index: dict[str, Any]) -> str:
    lines = [
        "# Replay Runs Dashboard",
        "",
        f"- Runs Root: `{index.get('runsRoot')}`",
        f"- Session Count: `{index.get('sessionCount')}`",
        f"- Total Case Count: `{index.get('totalCaseCount')}`",
        "",
        "## Aggregated Rule Coverage",
        "",
    ]
    for key, count in index.get("aggregatedRuleTotals", {}).items():
        lines.append(f"- `{key}`: {count}")
    lines.extend([
        "",
        "## Sessions",
        "",
        "| Session Dir | Case Count | Total Elapsed (ms) | Total Artifact Bytes | Result Phases | main | lock | binder | render | stw | cpu | input |",
        "| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for session in index.get("sessions", []):
        rules = session.get("ruleTotals", {})
        lines.append(
            f"| `{session.get('sessionDir')}` | {session.get('caseCount')} | {session.get('totalElapsedMs')} | {session.get('totalArtifactBytes')} | {', '.join(session.get('resultPhases', []))} | "
            f"{rules.get('mainThreadCaptured', 0)} | "
            f"{rules.get('lockContentionDetected', 0)} | "
            f"{rules.get('binderWaitChainDetected', 0)} | "
            f"{rules.get('renderWaitChainDetected', 0)} | "
            f"{rules.get('stwPauseDetected', 0)} | "
            f"{(rules.get('schedulerPressureDetected', 0) + rules.get('cpuBusyExecutionDetected', 0))} | "
            f"{(rules.get('inputWaitDetected', 0) + rules.get('crossSourceInputConsistency', 0))} |"
        )
    lines.append("")
    return "\n".join(lines)



def _render_html(index: dict[str, Any]) -> str:
    rows = []
    for session in index.get("sessions", []):
        rules = session.get("ruleTotals", {})
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(str(session.get('sessionDir')))}</code></td>"
            f"<td>{session.get('caseCount')}</td>"
            f"<td>{session.get('totalElapsedMs')}</td>"
            f"<td>{session.get('totalArtifactBytes')}</td>"
            f"<td>{html.escape(', '.join(session.get('resultPhases', [])))}</td>"
            f"<td>{rules.get('mainThreadCaptured', 0)}</td>"
            f"<td>{rules.get('lockContentionDetected', 0)}</td>"
            f"<td>{rules.get('binderWaitChainDetected', 0)}</td>"
            f"<td>{rules.get('renderWaitChainDetected', 0)}</td>"
            f"<td>{rules.get('stwPauseDetected', 0)}</td>"
            f"<td>{rules.get('schedulerPressureDetected', 0) + rules.get('cpuBusyExecutionDetected', 0)}</td>"
            f"<td>{rules.get('inputWaitDetected', 0) + rules.get('crossSourceInputConsistency', 0)}</td>"
            "</tr>"
        )
    rule_items = "\n    ".join(
        f"<li><strong>{html.escape(str(key))}:</strong> {value}</li>"
        for key, value in index.get("aggregatedRuleTotals", {}).items()
    )
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>Replay Runs Dashboard</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 24px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ccc; padding: 8px; text-align: left; }}
    th {{ background: #f5f5f5; }}
    code {{ font-family: ui-monospace, SFMono-Regular, monospace; }}
  </style>
</head>
<body>
  <h1>Replay Runs Dashboard</h1>
  <ul>
    <li><strong>Runs Root:</strong> <code>{runs_root}</code></li>
    <li><strong>Session Count:</strong> {session_count}</li>
    <li><strong>Total Case Count:</strong> {total_case_count}</li>
  </ul>
  <h2>Aggregated Rule Coverage</h2>
  <ul>
    {rule_items}
  </ul>
  <h2>Sessions</h2>
  <table>
    <thead>
      <tr>
        <th>Session Dir</th>
        <th>Case Count</th>
        <th>Total Elapsed (ms)</th>
        <th>Total Artifact Bytes</th>
        <th>Result Phases</th>
        <th>Main</th>
        <th>Lock</th>
        <th>Binder</th>
        <th>Render</th>
        <th>STW</th>
        <th>CPU</th>
        <th>Input</th>
      </tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
</body>
</html>
""".format(
        runs_root=html.escape(str(index.get("runsRoot"))),
        session_count=index.get("sessionCount"),
        total_case_count=index.get("totalCaseCount"),
        rule_items=rule_items,
        rows="\n      ".join(rows),
    )
