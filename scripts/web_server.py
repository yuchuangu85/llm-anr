#!/usr/bin/env python3
"""Local Web UI for ANR AI context analysis.

No third-party web framework is required. The UI supports:
- entering a local fixture/directory/archive path on the machine running server;
- uploading a single fixture/archive file;
- viewing pipeline events, grouped evidence, cache.md, and ai_prompt.md.
"""

from __future__ import annotations

import argparse
import html
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
import sys
import tempfile
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from anr_evidence import AiContextOptions, build_ai_context
from anr_evidence.extractor import load_package_from_archive, load_package_from_directory, load_package_from_fixture

UPLOAD_ROOT = Path(tempfile.gettempdir()) / "agent-anr-web-uploads"
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)


def load_input(path: Path) -> dict:
    if path.is_dir():
        return load_package_from_directory(path)
    suffixes = [suffix.lower() for suffix in path.suffixes]
    is_archive = bool(suffixes) and (suffixes[-1] == ".zip" or any(suffix in {".tar", ".gz", ".tgz", ".bz2", ".xz"} for suffix in suffixes))
    if is_archive:
        return load_package_from_archive(path)
    return load_package_from_fixture(path)


class AnrWebHandler(BaseHTTPRequestHandler):
    server_version = "ANRWeb/0.2"

    def do_GET(self) -> None:  # noqa: N802
        self._send_html(render_page())

    def do_POST(self) -> None:  # noqa: N802
        try:
            form = self._read_form()
            input_path = self._resolve_input_path(form)
            package = load_input(input_path)
            result = build_ai_context(
                package,
                AiContextOptions(
                    anr_type=_first(form, "anr_type") or None,
                    package_name=_first(form, "package_name") or None,
                    event_before_seconds=_int_or_none(_first(form, "event_before_seconds")),
                    logcat_before_seconds=_int_or_none(_first(form, "logcat_before_seconds")),
                    logcat_after_seconds=_int_or_none(_first(form, "logcat_after_seconds")),
                    group_tolerance_seconds=_int_or_none(_first(form, "group_tolerance_seconds")),
                ),
            )
            self._send_html(render_page(result=result, input_path=input_path))
        except Exception as exc:  # local diagnostic UI should surface the error
            self._send_html(render_page(error=str(exc)), status=HTTPStatus.BAD_REQUEST)

    def _read_form(self) -> dict[str, list[str] | bytes]:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        content_type = self.headers.get("Content-Type", "")
        if content_type.startswith("application/x-www-form-urlencoded"):
            return {key: values for key, values in parse_qs(body.decode("utf-8", errors="replace")).items()}
        if content_type.startswith("multipart/form-data"):
            return _parse_multipart(body, content_type)
        return {}

    def _resolve_input_path(self, form: dict[str, list[str] | bytes]) -> Path:
        uploaded = form.get("upload_file")
        filename = _first(form, "upload_filename")
        if isinstance(uploaded, bytes) and uploaded and filename:
            safe_name = _safe_upload_filename(filename)
            upload_path = UPLOAD_ROOT / safe_name
            upload_path.write_bytes(uploaded)
            return upload_path
        raw_path = _first(form, "input_path")
        if not raw_path:
            raise ValueError("Please enter a local input path or upload a fixture/archive file.")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = ROOT / path
        if not path.exists():
            raise FileNotFoundError(f"Input path does not exist: {path}")
        return path

    def _send_html(self, content: str, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}")


def render_page(*, result=None, input_path: Path | None = None, error: str | None = None) -> str:
    result_html = render_result(result, input_path)
    error_html = render_error(error)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>ANR Workbench</title>
  <style>
    :root {{
      --bg: #f4f7fb;
      --panel: #ffffff;
      --panel-2: #f8fafc;
      --text: #172033;
      --muted: #667085;
      --line: #d9e2ef;
      --primary: #2563eb;
      --primary-2: #1d4ed8;
      --ok: #15803d;
      --warn: #b45309;
      --err: #b42318;
      --shadow: 0 16px 40px rgba(15, 23, 42, .08);
      --radius: 18px;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--text); font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    .hero {{ padding: 34px 28px 28px; background: linear-gradient(135deg, #0f172a 0%, #1d4ed8 58%, #38bdf8 100%); color: white; }}
    .hero-inner, .shell {{ max-width: 1280px; margin: 0 auto; }}
    .eyebrow {{ text-transform: uppercase; letter-spacing: .14em; font-size: 12px; opacity: .76; }}
    h1 {{ margin: 8px 0 10px; font-size: clamp(30px, 5vw, 52px); line-height: 1.05; }}
    .hero p {{ max-width: 860px; margin: 0; color: rgba(255,255,255,.82); font-size: 16px; }}
    .shell {{ padding: 24px 20px 48px; }}
    .layout {{ display: grid; grid-template-columns: minmax(320px, 420px) minmax(0, 1fr); gap: 20px; align-items: start; }}
    .card {{ background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow); overflow: hidden; }}
    .card-head {{ padding: 18px 20px; border-bottom: 1px solid var(--line); background: linear-gradient(180deg, #fff, #fbfdff); }}
    .card-head h2, .card-head h3 {{ margin: 0; }}
    .card-body {{ padding: 20px; }}
    .muted {{ color: var(--muted); }}
    form label {{ display: block; margin: 14px 0 6px; font-weight: 700; font-size: 13px; }}
    input, select {{ width: 100%; padding: 11px 12px; border: 1px solid #ccd6e3; border-radius: 12px; font-size: 14px; background: white; }}
    input:focus, select:focus, textarea:focus {{ outline: 3px solid rgba(37, 99, 235, .16); border-color: var(--primary); }}
    .field-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
    .btn-row {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 18px; }}
    button, .button {{ border: 0; border-radius: 12px; background: var(--primary); color: white; padding: 11px 14px; font-weight: 800; cursor: pointer; text-decoration: none; display: inline-flex; align-items: center; gap: 8px; }}
    button:hover, .button:hover {{ background: var(--primary-2); }}
    .secondary {{ background: #e8eef8; color: #1e3a8a; }}
    .secondary:hover {{ background: #dbeafe; }}
    .quick {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }}
    .pill {{ border-radius: 999px; padding: 6px 10px; background: #eef2ff; color: #334155; font-size: 12px; font-weight: 700; }}
    .summary-grid {{ display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); gap: 12px; margin-bottom: 18px; }}
    .metric {{ background: var(--panel); border: 1px solid var(--line); border-radius: 16px; padding: 14px; }}
    .metric b {{ display: block; font-size: 24px; margin-top: 4px; }}
    .alert {{ border-radius: 16px; padding: 14px 16px; margin-bottom: 18px; border: 1px solid #fecaca; color: var(--err); background: #fff1f2; white-space: pre-wrap; }}
    .timeline {{ display: grid; gap: 10px; }}
    .event {{ display: grid; grid-template-columns: 36px 170px minmax(0, 1fr); gap: 12px; align-items: start; padding: 10px; border: 1px solid var(--line); border-radius: 14px; background: var(--panel-2); }}
    .dot {{ width: 28px; height: 28px; border-radius: 50%; display: grid; place-items: center; background: #dcfce7; color: var(--ok); font-weight: 900; }}
    .event code, code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; background: #edf2f7; border-radius: 7px; padding: 2px 5px; }}
    .group-card {{ border: 1px solid var(--line); border-radius: 16px; margin: 14px 0; overflow: hidden; background: white; }}
    .group-title {{ display: flex; justify-content: space-between; gap: 12px; padding: 14px 16px; background: #f8fafc; border-bottom: 1px solid var(--line); }}
    .badge {{ border-radius: 999px; padding: 5px 9px; font-size: 12px; font-weight: 800; }}
    .badge.ok {{ background: #dcfce7; color: var(--ok); }}
    .badge.warn {{ background: #fef3c7; color: var(--warn); }}
    .source-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; padding: 14px; }}
    details.source {{ border: 1px solid var(--line); border-radius: 14px; overflow: hidden; }}
    details.source summary {{ cursor: pointer; padding: 10px 12px; font-weight: 800; background: #f8fafc; }}
    pre, textarea {{ width: 100%; margin: 0; background: #0b1020; color: #dbeafe; border: 0; padding: 14px; overflow: auto; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; line-height: 1.55; }}
    pre {{ max-height: 340px; }}
    textarea {{ min-height: 360px; resize: vertical; border-radius: 0 0 var(--radius) var(--radius); }}
    .tabs {{ display: flex; gap: 8px; padding: 12px 12px 0; background: white; }}
    .tab {{ background: #eef2ff; color: #1e3a8a; }}
    .tab.active {{ background: var(--primary); color: white; }}
    .tab-panel {{ display: none; }}
    .tab-panel.active {{ display: block; }}
    .copybar {{ display: flex; justify-content: flex-end; padding: 10px 12px; background: white; border-top: 1px solid var(--line); }}
    @media (max-width: 980px) {{ .layout {{ grid-template-columns: 1fr; }} .summary-grid, .source-grid {{ grid-template-columns: 1fr; }} .event {{ grid-template-columns: 30px 1fr; }} .event-detail {{ grid-column: 2; }} }}
  </style>
</head>
<body>
  <header class="hero">
    <div class="hero-inner">
      <div class="eyebrow">Local ANR Analysis Workbench</div>
      <h1>ANR 证据过滤与 AI Context 生成</h1>
      <p>手动选择 bugreport / fixture / archive，按 ANR 类型策略执行 trace、EventLog、logcat 过滤，展示过程、分组证据、完整性与可复制 Prompt。</p>
    </div>
  </header>
  <main class="shell">
    {render_error(error)}
    <div class="layout">
      <aside class="card">
        <div class="card-head">
          <h2>输入与策略</h2>
          <div class="muted">浏览器选择的是运行此服务机器上的路径；上传支持 fixture JSON 或 archive。</div>
        </div>
        <div class="card-body">
          <form method="post" enctype="multipart/form-data">
            <label>本地路径</label>
            <input id="input_path" name="input_path" value="tests/fixtures/nfw_01.json" placeholder="tests/fixtures/nfw_01.json 或 /path/to/bugreport.zip" />
            <div class="quick">
              <button type="button" class="secondary" onclick="fillSample('tests/fixtures/nfw_01.json','no_focus_window')">No Focus 示例</button>
              <button type="button" class="secondary" onclick="fillSample('tests/fixtures/idt_01.json','input_dispatching_timeout')">Input Timeout 示例</button>
            </div>
            <label>或上传文件</label>
            <input type="file" name="upload_file" />
            <label>ANR 类型策略</label>
            <select id="anr_type" name="anr_type">
              <option value="">自动识别</option>
              <option value="input_dispatching_timeout">Input dispatching timeout</option>
              <option value="no_focus_window">No focus window</option>
              <option value="broadcast_timeout">Broadcast timeout</option>
              <option value="service_timeout">Service timeout</option>
              <option value="content_provider_timeout">ContentProvider timeout</option>
              <option value="job_scheduler_timeout">JobScheduler timeout</option>
              <option value="system_watchdog_swt">System Watchdog/SWT</option>
              <option value="unknown">Unknown / future type</option>
            </select>
            <label>包名过滤（可选）</label>
            <input name="package_name" placeholder="com.example.app" />
            <div class="field-row">
              <div><label>Event 前置秒</label><input name="event_before_seconds" type="number" placeholder="策略默认" /></div>
              <div><label>分组容差秒</label><input name="group_tolerance_seconds" type="number" placeholder="策略默认" /></div>
              <div><label>Logcat 前置秒</label><input name="logcat_before_seconds" type="number" placeholder="策略默认" /></div>
              <div><label>Logcat 后置秒</label><input name="logcat_after_seconds" type="number" placeholder="策略默认" /></div>
            </div>
            <div class="btn-row">
              <button type="submit">开始分析</button>
              <button type="reset" class="secondary">重置</button>
            </div>
          </form>
        </div>
      </aside>
      <div>
        {result_html or render_empty_state()}
      </div>
    </div>
  </main>
  <script>
    function fillSample(path, type) {{
      document.getElementById('input_path').value = path;
      document.getElementById('anr_type').value = type;
    }}
    function showTab(name) {{
      document.querySelectorAll('.tab').forEach(el => el.classList.toggle('active', el.dataset.tab === name));
      document.querySelectorAll('.tab-panel').forEach(el => el.classList.toggle('active', el.id === 'panel-' + name));
    }}
    async function copyText(id) {{
      const el = document.getElementById(id);
      await navigator.clipboard.writeText(el.value || el.textContent);
      const btn = document.querySelector('[data-copy="' + id + '"]');
      if (btn) {{ const old = btn.textContent; btn.textContent = '已复制'; setTimeout(() => btn.textContent = old, 1200); }}
    }}
  </script>
</body>
</html>"""


def render_empty_state() -> str:
    return """
      <section class="card">
        <div class="card-head"><h2>等待输入</h2></div>
        <div class="card-body">
          <p class="muted">点击左侧示例或输入本地 bugreport 路径后开始分析。完成后这里会显示：</p>
          <div class="quick">
            <span class="pill">过程时间线</span>
            <span class="pill">ANR 分组</span>
            <span class="pill">Trace / EventLog / Logcat 证据</span>
            <span class="pill">cache.md</span>
            <span class="pill">ai_prompt.md</span>
          </div>
        </div>
      </section>
    """


def render_error(error: str | None) -> str:
    if not error:
        return ""
    return f'<section class="alert"><strong>错误：</strong>{html.escape(error)}</section>'


def render_result(result, input_path: Path | None) -> str:
    if result is None:
        return ""
    summary = result.summary()
    complete_count = sum(1 for group in result.groups if group.get("completeness", {}).get("complete"))
    metrics = f"""
      <div class="summary-grid">
        <div class="metric"><span class="muted">Strategy</span><b>{html.escape(summary['strategy']['anrType'])}</b></div>
        <div class="metric"><span class="muted">Groups</span><b>{summary['groupCount']}</b></div>
        <div class="metric"><span class="muted">Complete</span><b>{complete_count}/{summary['groupCount']}</b></div>
        <div class="metric"><span class="muted">Events</span><b>{len(result.events)}</b></div>
      </div>
    """
    return f"""
      {metrics}
      <section class="card">
        <div class="card-head">
          <h2>分析结果</h2>
          <div class="muted">Input: <code>{html.escape(str(input_path))}</code></div>
        </div>
        <div class="card-body">
          <div class="tabs">
            <button class="tab active" data-tab="timeline" onclick="showTab('timeline')">过程</button>
            <button class="tab" data-tab="groups" onclick="showTab('groups')">分组证据</button>
            <button class="tab" data-tab="cache" onclick="showTab('cache')">cache.md</button>
            <button class="tab" data-tab="prompt" onclick="showTab('prompt')">ai_prompt.md</button>
          </div>
          <div id="panel-timeline" class="tab-panel active">{render_timeline(result.events)}</div>
          <div id="panel-groups" class="tab-panel">{''.join(render_group(group) for group in result.groups)}</div>
          <div id="panel-cache" class="tab-panel">{render_text_panel('cacheText', result.cache_markdown)}</div>
          <div id="panel-prompt" class="tab-panel">{render_text_panel('promptText', result.ai_prompt_markdown)}</div>
        </div>
      </section>
    """


def render_timeline(events: list[dict]) -> str:
    rows = []
    for index, event in enumerate(events, start=1):
        detail = json.dumps(event.get("details", {}), ensure_ascii=False)
        rows.append(
            f'<div class="event"><div class="dot">{index}</div>'
            f'<div><strong>{html.escape(event.get("step", ""))}</strong><br><span class="badge ok">{html.escape(event.get("status", ""))}</span></div>'
            f'<div class="event-detail"><code>{html.escape(detail)}</code></div></div>'
        )
    return f'<div class="timeline">{"".join(rows)}</div>'


def render_group(group: dict) -> str:
    anchor = group.get("anchor") or {}
    completeness = group.get("completeness", {})
    counts = completeness.get("retainedLineCounts", {})
    badge_class = "ok" if completeness.get("complete") else "warn"
    trace_text = "\n".join(group.get("trace", {}).get("lines", []))
    event_text = "\n".join(group.get("eventLog", {}).get("lines", []))
    logcat_text = "\n".join(group.get("logcat", {}).get("lines", []))
    return f"""
      <article class="group-card">
        <div class="group-title">
          <div><strong>{html.escape(group.get('id', 'unknown'))}</strong><br><span class="muted">{html.escape(json.dumps(anchor, ensure_ascii=False))}</span></div>
          <span class="badge {badge_class}">complete={html.escape(str(completeness.get('complete')))}</span>
        </div>
        <div class="card-body">
          <div class="quick">
            <span class="pill">trace {counts.get('trace', 0)} lines</span>
            <span class="pill">event {counts.get('event_log', 0)} lines</span>
            <span class="pill">logcat {counts.get('logcat', 0)} lines</span>
          </div>
          <div class="source-grid">
            <details class="source" open><summary>Trace</summary><pre>{html.escape(trace_text)}</pre></details>
            <details class="source" open><summary>EventLog</summary><pre>{html.escape(event_text)}</pre></details>
            <details class="source" open><summary>Logcat</summary><pre>{html.escape(logcat_text)}</pre></details>
          </div>
        </div>
      </article>
    """


def render_text_panel(element_id: str, text: str) -> str:
    return f"""
      <div class="copybar"><button class="secondary" data-copy="{element_id}" onclick="copyText('{element_id}')">复制</button></div>
      <textarea id="{element_id}" readonly>{html.escape(text)}</textarea>
    """


def _first(form: dict[str, list[str] | bytes], key: str) -> str:
    value = form.get(key)
    if isinstance(value, list):
        return value[0].strip() if value else ""
    return ""


def _safe_upload_filename(filename: str) -> str:
    """Return a browser-upload filename safe on Windows and POSIX hosts."""

    name = PurePosixPath(filename.replace("\\", "/")).name
    return name or "upload.bin"


def _int_or_none(raw: str) -> int | None:
    return int(raw) if raw else None


def _parse_multipart(body: bytes, content_type: str) -> dict[str, list[str] | bytes]:
    boundary_token = "boundary="
    if boundary_token not in content_type:
        return {}
    boundary = ("--" + content_type.split(boundary_token, 1)[1].strip().strip('"')).encode()
    form: dict[str, list[str] | bytes] = {}
    for part in body.split(boundary):
        part = part.strip(b"\r\n")
        if not part or part == b"--" or b"\r\n\r\n" not in part:
            continue
        raw_headers, data = part.split(b"\r\n\r\n", 1)
        headers = raw_headers.decode("utf-8", errors="replace")
        disposition = next((line for line in headers.split("\r\n") if line.lower().startswith("content-disposition")), "")
        name = _disposition_value(disposition, "name")
        filename = _disposition_value(disposition, "filename")
        data = data.rstrip(b"\r\n")
        if not name:
            continue
        if filename:
            form[name] = data
            form[f"{name.removesuffix('_file')}_filename"] = [filename]
        else:
            form[name] = [data.decode("utf-8", errors="replace")]
    if "upload_filename" not in form and "upload_file_filename" in form:
        form["upload_filename"] = form["upload_file_filename"]
    return form


def _disposition_value(disposition: str, key: str) -> str:
    marker = f'{key}="'
    if marker not in disposition:
        return ""
    return disposition.split(marker, 1)[1].split('"', 1)[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local ANR AI context Web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), AnrWebHandler)
    print(f"ANR Web UI running at http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping ANR Web UI")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
