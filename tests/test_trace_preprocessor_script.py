from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from anr_evidence import extract_evidence_package, load_package_from_fixture

ROOT = Path(__file__).resolve().parent.parent
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


class TracePreprocessorScriptTests(unittest.TestCase):
    def test_script_preprocesses_fixture_trace(self) -> None:
        completed = subprocess.run(
            [sys.executable, "scripts/anr_preprocessor.py", "tests/fixtures/nfw_01.json"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            env=ENV,
        )
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["metadata"]["phase"], "trace-preprocess-v1")
        self.assertEqual(payload["trace"]["pid"], "100")
        self.assertEqual(payload["trace"]["primaryThread"]["threadName"], "main")
        self.assertIn("threadSummary", payload["trace"])
        self.assertIn("suspiciousThreads", payload["trace"])

    def test_script_preprocesses_phase1_package(self) -> None:
        phase1 = extract_evidence_package(load_package_from_fixture(ROOT / "tests/fixtures/idt_01.json"))
        with tempfile.TemporaryDirectory() as tmpdir:
            phase1_path = Path(tmpdir) / "phase1.json"
            phase1_path.write_text(json.dumps(phase1, ensure_ascii=False), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, "scripts/anr_preprocessor.py", str(phase1_path)],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                env=ENV,
            )
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["metadata"]["sourceOrigin"], "phase1-evidence")
        self.assertEqual(payload["trace"]["primaryThread"]["blockHint"], "input_dispatch_wait")

    def test_script_preprocesses_plain_trace_file(self) -> None:
        content = "\n".join(
            [
                "04-12 10:00:05.100 ----- pid 100 -----",
                "Cmd line: com.demo",
                "binder tid=9 waiting in binder",
                "main tid=1 Native: waiting because no focused window",
            ]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            trace_path = Path(tmpdir) / "trace.txt"
            trace_path.write_text(content, encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, "scripts/anr_preprocessor.py", str(trace_path)],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                env=ENV,
            )
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["metadata"]["sourceOrigin"], "trace-text")
        self.assertEqual(payload["trace"]["processName"], "com.demo")
        self.assertEqual(payload["trace"]["primaryThread"]["blockHint"], "focus_window_wait")


if __name__ == "__main__":
    unittest.main()
