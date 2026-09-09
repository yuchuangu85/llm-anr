from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shlex
import stat
import tempfile
import unittest
import zipfile

from anr_evidence.performance_adapters import (
    inspect_winscope_release_artifact,
    probe_performance_capabilities,
    run_perfetto_scheduling_query,
)


VERSION = "Perfetto vtest-1 (fixture)"
CSV_ROWS = '"ts","dur","cpu","tid"\n10,5,2,101\n20,-1,-1,102\n'


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def shell_quote(value: str | Path) -> str:
    return shlex.quote(str(value))


def fake_trace_processor(
    directory: Path,
    *,
    version: str = VERSION,
    query_output: str = CSV_ROWS,
    query_exit: int = 0,
    query_sleep_seconds: int = 0,
    query_marker: Path | None = None,
    sql_capture: Path | None = None,
    oversized_output: bool = False,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    marker_statement = f"printf query > {shell_quote(query_marker)}\n" if query_marker else ""
    capture_statement = f"cat > {shell_quote(sql_capture)}\n" if sql_capture else "cat > /dev/null\n"
    if oversized_output:
        output_statement = "i=0; while [ \"$i\" -lt 200 ]; do printf '0123456789'; i=$((i + 1)); done\n"
    else:
        output_statement = f"cat <<'P1_OUTPUT'\n{query_output}P1_OUTPUT\n"
    sleep_statement = f"sleep {query_sleep_seconds}\n" if query_sleep_seconds else ""
    script = directory / "trace_processor_shell"
    script.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then\n"
        f"  printf '%s\\n' {shell_quote(version)}\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1\" = \"query\" ]; then\n"
        f"  {marker_statement}"
        f"  {capture_statement}"
        f"  {sleep_statement}"
        f"  {output_statement}"
        f"  exit {query_exit}\n"
        "fi\n"
        "exit 64\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def perfetto_config(binary: Path, **overrides: object) -> dict[str, object]:
    tool_config: dict[str, object] = {
        "binaryPath": str(binary),
        "allowedToolSha256": [sha256(binary)],
        "expectedVersion": VERSION,
        "timeoutSeconds": 2,
        "maxVersionOutputBytes": 1024,
        "maxQueryOutputBytes": 1024,
        "maxRows": 4,
    }
    tool_config.update(overrides)
    return {"schemaVersion": "performance-tool-config-v1", "perfetto": tool_config}


def winscope_zip(
    path: Path,
    *,
    manifest: dict[str, object] | None = None,
    release: dict[str, object] | None = None,
    include_release: bool = True,
    extra: dict[str, str] | None = None,
) -> None:
    manifest = manifest if manifest is not None else {"schemaVersion": 1, "capabilities": ["layers"]}
    release = release if release is not None else {
        "schemaVersion": 1,
        "version": "17.0.1",
        "launcher": "bin/darwin-arm64/winscope-launcher",
        "sourceCommit": "a" * 40,
        "target": {"operatingSystem": "darwin", "architecture": "arm64"},
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        if include_release:
            archive.writestr("release-manifest.json", json.dumps(release))
        if isinstance(release.get("launcher"), str):
            archive.writestr(str(release["launcher"]), "fixture launcher")
        for name, content in (extra or {}).items():
            archive.writestr(name, content)


def winscope_config(archive: Path, **overrides: object) -> dict[str, object]:
    winscope: dict[str, object] = {
        "archivePath": str(archive),
        "expectedSha256": sha256(archive),
        "expectedVersion": "17.0.1",
        "expectedReleaseManifestSchemaVersion": 1,
        "expectedManifestSchemaVersion": 1,
        "expectedTarget": {"operatingSystem": "darwin", "architecture": "arm64"},
    }
    winscope.update(overrides)
    return {"schemaVersion": "performance-tool-config-v1", "winscope": winscope}


class PerfettoCapabilityTests(unittest.TestCase):
    def test_missing_or_relative_configuration_fails_closed_without_query(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            marker = directory / "query.marker"
            binary = fake_trace_processor(directory, query_marker=marker)
            missing = probe_performance_capabilities({"schemaVersion": "performance-tool-config-v1"})
            self.assertEqual(missing["tools"]["perfettoTraceProcessor"]["status"], "unconfigured")
            self.assertIn("perfetto_not_configured", missing["gaps"])

            relative = perfetto_config(binary)
            relative["perfetto"]["binaryPath"] = binary.name  # type: ignore[index]
            report = probe_performance_capabilities(relative)
            self.assertEqual(report["tools"]["perfettoTraceProcessor"]["status"], "invalid_config")
            self.assertIn("perfetto_path_not_absolute", report["gaps"])

            missing = perfetto_config(binary)
            missing["perfetto"]["binaryPath"] = str(directory / "not-present")  # type: ignore[index]
            missing_report = probe_performance_capabilities(missing)
            self.assertEqual(missing_report["tools"]["perfettoTraceProcessor"]["status"], "unavailable")
            self.assertIn("perfetto_path_unavailable", missing_report["gaps"])

            non_executable = directory / "not-executable"
            non_executable.write_text("not an executable", encoding="utf-8")
            no_exec = perfetto_config(binary)
            no_exec["perfetto"]["binaryPath"] = str(non_executable)  # type: ignore[index]
            no_exec["perfetto"]["allowedToolSha256"] = [sha256(non_executable)]  # type: ignore[index]
            no_exec_report = probe_performance_capabilities(no_exec)
            self.assertEqual(no_exec_report["tools"]["perfettoTraceProcessor"]["status"], "unavailable")
            self.assertIn("perfetto_path_not_executable", no_exec_report["gaps"])
            self.assertFalse(marker.exists())

    def test_explicit_allowlisted_tool_and_exact_version_are_available(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            binary = fake_trace_processor(Path(temporary))
            report = probe_performance_capabilities(perfetto_config(binary))
            tool = report["tools"]["perfettoTraceProcessor"]
            self.assertEqual(tool["status"], "available")
            self.assertEqual(tool["binarySha256"], sha256(binary))
            self.assertEqual(tool["versionOutput"], VERSION)
            self.assertFalse(report["finalJudgment"])
            self.assertTrue(report["notRootCauseYet"])
            self.assertTrue(report["requiresHumanConfirmation"])

    def test_unallowlisted_or_version_mismatched_tool_never_executes_query(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            marker = directory / "query.marker"
            binary = fake_trace_processor(directory, query_marker=marker)
            trace = directory / "trace.pftrace"
            trace.write_bytes(b"fixture")

            not_allowed = perfetto_config(binary, allowedToolSha256=["0" * 64])
            unapproved = run_perfetto_scheduling_query(trace, not_allowed, target_pid=123)
            self.assertEqual(unapproved["status"], "blocked")
            self.assertIn("perfetto_tool_sha256_not_allowlisted", unapproved["gaps"])
            self.assertFalse(marker.exists())

            mismatch = perfetto_config(binary, expectedVersion="other version")
            blocked = run_perfetto_scheduling_query(trace, mismatch, target_pid=123)
            self.assertEqual(blocked["status"], "blocked")
            self.assertIn("perfetto_version_output_not_allowlisted", blocked["gaps"])
            self.assertFalse(marker.exists())

    def test_fixed_query_parses_bounded_numeric_rows_and_preserves_clock_gap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            capture = directory / "query.sql"
            binary = fake_trace_processor(directory, sql_capture=capture)
            trace = directory / "trace.pftrace"
            trace.write_bytes(b"fixture")
            report = run_perfetto_scheduling_query(trace, perfetto_config(binary), target_pid=4242)

            self.assertEqual(report["status"], "observed")
            self.assertEqual(
                report["schedulingIntervals"],
                [{"ts": 10, "dur": 5, "cpu": 2, "tid": 101}, {"ts": 20, "dur": -1, "cpu": -1, "tid": 102}],
            )
            self.assertEqual(report["entities"][0], {"kind": "thread", "tid": 101, "processPid": 4242, "mappingStatus": "pid_constrained_by_fixed_query"})
            self.assertNotIn("processName", report["entities"][0])
            self.assertEqual(report["clock"], {"domain": "perfetto_trace", "unit": "ns", "alignmentStatus": "unmapped"})
            self.assertIn("clock_mapping_unavailable", report["gaps"])
            self.assertIn("trace_capture_provenance_unverified", report["gaps"])
            sql = capture.read_text(encoding="utf-8")
            self.assertIn("FROM sched AS s", sql)
            self.assertIn("WHERE p.pid = 4242", sql)
            self.assertIn("LIMIT 4", sql)
            self.assertNotIn("SELECT *", sql)

    def test_invalid_pid_and_unknown_output_schema_never_yield_schedule_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            trace = directory / "trace.pftrace"
            trace.write_bytes(b"fixture")
            binary = fake_trace_processor(directory, query_output='"unexpected"\n1\n')
            invalid_pid = run_perfetto_scheduling_query(trace, perfetto_config(binary), target_pid=0)
            self.assertEqual(invalid_pid["status"], "blocked")
            self.assertIn("target_pid_invalid", invalid_pid["gaps"])

            unsupported = run_perfetto_scheduling_query(trace, perfetto_config(binary), target_pid=1)
            self.assertEqual(unsupported["status"], "unsupported")
            self.assertEqual(unsupported["schedulingIntervals"], [])
            self.assertIn("perfetto_query_output_schema_unsupported", unsupported["gaps"])

    def test_nonzero_timeout_and_oversized_output_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            trace = directory / "trace.pftrace"
            trace.write_bytes(b"fixture")

            nonzero = fake_trace_processor(directory / "nonzero", query_exit=7)
            nonzero_report = run_perfetto_scheduling_query(trace, perfetto_config(nonzero), target_pid=1)
            self.assertEqual(nonzero_report["status"], "blocked")
            self.assertIn("perfetto_query_failed", nonzero_report["gaps"])

            sleeping = fake_trace_processor(directory / "sleeping", query_sleep_seconds=2)
            timeout_report = run_perfetto_scheduling_query(
                trace, perfetto_config(sleeping, timeoutSeconds=1), target_pid=1
            )
            self.assertEqual(timeout_report["status"], "blocked")
            self.assertIn("perfetto_query_timeout", timeout_report["gaps"])

            noisy = fake_trace_processor(directory / "noisy", oversized_output=True)
            noisy_report = run_perfetto_scheduling_query(
                trace, perfetto_config(noisy, maxQueryOutputBytes=256), target_pid=1
            )
            self.assertEqual(noisy_report["status"], "blocked")
            self.assertEqual(noisy_report["schedulingIntervals"], [])
            self.assertIn("perfetto_query_output_exceeded_limit", noisy_report["gaps"])


class WinScopeArtifactTests(unittest.TestCase):
    def test_matching_packaged_release_is_metadata_only_capability(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive = directory / "winscope.zip"
            winscope_zip(archive)
            report = inspect_winscope_release_artifact(winscope_config(archive))
            self.assertEqual(report["status"], "available")
            self.assertEqual(report["capabilityMode"], "metadata_only")
            self.assertEqual(report["release"]["version"], "17.0.1")
            self.assertIn("winscope_graphical_query_adapter_not_implemented", report["gaps"])
            self.assertFalse((directory / "manifest.json").exists())
            self.assertFalse((directory / "release-manifest.json").exists())

    def test_hash_manifest_schema_version_and_traversal_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            valid = directory / "valid.zip"
            winscope_zip(valid)
            wrong_hash = inspect_winscope_release_artifact(winscope_config(valid, expectedSha256="0" * 64))
            self.assertEqual(wrong_hash["status"], "artifact_hash_mismatch")

            missing = directory / "missing-release.zip"
            winscope_zip(missing, include_release=False)
            missing_report = inspect_winscope_release_artifact(winscope_config(missing))
            self.assertEqual(missing_report["status"], "unsupported")
            self.assertIn("winscope_release-manifest.json_missing_or_duplicate", missing_report["gaps"])

            schema = directory / "bad-schema.zip"
            winscope_zip(schema, manifest={"schemaVersion": 2})
            schema_report = inspect_winscope_release_artifact(winscope_config(schema))
            self.assertEqual(schema_report["status"], "unsupported_schema")
            self.assertIn("winscope_manifest_schema_unsupported", schema_report["gaps"])

            version = directory / "bad-version.zip"
            winscope_zip(version, release={
                "schemaVersion": 1,
                "version": "18.0.0",
                "launcher": "bin/darwin-arm64/winscope-launcher",
                "target": {"operatingSystem": "darwin", "architecture": "arm64"},
            })
            version_report = inspect_winscope_release_artifact(winscope_config(version))
            self.assertEqual(version_report["status"], "version_mismatch")

            traversal = directory / "traversal.zip"
            winscope_zip(traversal, extra={"../outside": "must not extract"})
            traversal_report = inspect_winscope_release_artifact(winscope_config(traversal))
            self.assertEqual(traversal_report["status"], "unsupported")
            self.assertIn("winscope_archive_path_unsafe", traversal_report["gaps"])

    def test_probe_reports_winscope_metadata_capability_without_graphical_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "winscope.zip"
            winscope_zip(archive)
            report = probe_performance_capabilities(winscope_config(archive))
            self.assertEqual(report["tools"]["winscope"]["status"], "available")
            self.assertEqual(report["tools"]["winscope"]["capabilityMode"], "metadata_only")
            self.assertIn("winscope_graphical_query_adapter_not_implemented", report["gaps"])


if __name__ == "__main__":
    unittest.main()
