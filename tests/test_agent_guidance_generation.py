from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
GENERATOR = ROOT / "scripts" / "generate_agent_guidance.py"
GUIDANCE_DIR = ROOT / "agent_guidance"
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


class AgentGuidanceGenerationTests(unittest.TestCase):
    def _run(self, root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(GENERATOR), "--root", str(root), *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=check,
            env=ENV,
        )

    def _copy_guidance_sources(self, root: Path) -> None:
        shutil.copytree(GUIDANCE_DIR, root / "agent_guidance")

    def _initialize(self, root: Path) -> None:
        self._copy_guidance_sources(root)
        (root / ".claude").mkdir()
        settings = root / ".claude" / "settings.local.json"
        settings.write_text('{"permissions":{"allow":["Bash(local-only)"]}}\n', encoding="utf-8")
        self._run(root)

    def test_checked_in_guidance_is_consistent_and_regular_files(self) -> None:
        completed = self._run(ROOT, "--check")
        self.assertEqual(completed.returncode, 0, completed.stderr)

        targets = {
            "AGENTS.md": "codex.md",
            "CLAUDE.md": "claude.md",
            "GEMINI.md": "gemini.md",
            "CODEBUDDY.md": "codebuddy.md",
            "HERMES.md": "hermes.md",
            ".cursor/rules/project.mdc": "cursor.md",
        }
        common = (GUIDANCE_DIR / "project.md").read_text(encoding="utf-8")
        for relative, fragment_name in targets.items():
            target = ROOT / relative
            self.assertTrue(target.is_file(), relative)
            self.assertFalse(target.is_symlink(), relative)
            tool_fragment = (GUIDANCE_DIR / "tools" / fragment_name).read_text(encoding="utf-8")
            content = target.read_text(encoding="utf-8")
            if relative == "CLAUDE.md":
                self.assertIn("@agent_guidance/project.md", content)
                self.assertIn("@agent_guidance/tools/claude.md", content)
            else:
                self.assertIn(common, content, relative)
                self.assertIn(tool_fragment, content, relative)
        cursor = (ROOT / ".cursor/rules/project.mdc").read_text(encoding="utf-8")
        self.assertTrue(cursor.startswith(
            "---\ndescription: ANR evidence extraction and analysis pipeline — project layout, workflows, and conventions\nglobs: **/*\nalwaysApply: true\n---\n"
        ))
        self.assertIn("## Coding conventions", cursor)

    def test_regeneration_preserves_omx_runtime_blocks_and_local_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._initialize(root)
            agents = root / "AGENTS.md"
            runtime = (
                "\n<!-- OMX:RUNTIME:START -->\n"
                "runtime-owned=do-not-rewrite\n"
                "<!-- OMX:RUNTIME:END -->\n"
                "<!-- OMX:TEAM:WORKER:START -->\n"
                "team-owned=do-not-rewrite\n"
                "<!-- OMX:TEAM:WORKER:END -->\n"
            )
            agents.write_text(agents.read_text(encoding="utf-8") + runtime, encoding="utf-8")
            settings = root / ".claude" / "settings.local.json"
            settings_before = settings.read_bytes()

            project = root / "agent_guidance" / "project.md"
            project.write_text(project.read_text(encoding="utf-8") + "\nShared drift repair sentinel.\n", encoding="utf-8")
            self._run(root)

            rendered = agents.read_text(encoding="utf-8")
            self.assertIn("Shared drift repair sentinel.", rendered)
            self.assertTrue(rendered.endswith(runtime))
            self.assertEqual(settings.read_bytes(), settings_before)

    def test_marker_bearing_legacy_file_fails_closed_without_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._copy_guidance_sources(root)
            agents = root / "AGENTS.md"
            original = (
                "legacy file\n"
                "<!-- OMX:RUNTIME:START -->\n"
                "runtime-owned\n"
                "<!-- OMX:RUNTIME:END -->\n"
            )
            agents.write_text(original, encoding="utf-8")

            completed = self._run(root, check=False)
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("OMX", completed.stderr)
            self.assertEqual(agents.read_text(encoding="utf-8"), original)

    def test_malformed_omx_marker_fails_closed_without_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._initialize(root)
            agents = root / "AGENTS.md"
            original = agents.read_text(encoding="utf-8") + "\n<!-- OMX:RUNTIME:START -->\n"
            agents.write_text(original, encoding="utf-8")

            completed = self._run(root, check=False)
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("unclosed OMX", completed.stderr)
            self.assertEqual(agents.read_text(encoding="utf-8"), original)

    def test_check_detects_managed_block_drift_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._initialize(root)
            agents = root / "AGENTS.md"
            drifted = agents.read_text(encoding="utf-8").replace(
                "Agent-driven Android ANR", "Drifted Android ANR", 1
            )
            agents.write_text(drifted, encoding="utf-8")

            completed = self._run(root, "--check", check=False)
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("out of date", completed.stderr)
            self.assertEqual(agents.read_text(encoding="utf-8"), drifted)

    def test_symlink_target_is_rejected_without_following_it(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlink unsupported by platform")
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._copy_guidance_sources(root)
            outside = root / "outside.md"
            outside.write_text("outside content\n", encoding="utf-8")
            (root / "AGENTS.md").symlink_to(outside)

            completed = self._run(root, check=False)
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("symlink", completed.stderr)
            self.assertEqual(outside.read_text(encoding="utf-8"), "outside content\n")


if __name__ == "__main__":
    unittest.main()
