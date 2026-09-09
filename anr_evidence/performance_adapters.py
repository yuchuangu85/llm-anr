"""Fail-closed local adapters for Perfetto and packaged AOSP WinScope.

The adapters deliberately do not discover tools on ``PATH``, download binaries,
launch a graphical viewer, or infer trace provenance from a filename.  A caller
must explicitly configure every external artifact and pin its identity.  The
only executable operation in the first P1 slice is one version-pinned,
numeric-only Perfetto scheduling query for a caller-supplied positive PID.

This module produces observations, not causal conclusions.  In particular,
Perfetto trace timestamps remain in the trace clock domain until a separate,
verified clock-alignment adapter is added.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import stat
import subprocess
import time
from typing import Any, Mapping, Sequence
import zipfile


_CONFIG_SCHEMA_VERSION = "performance-tool-config-v1"
_CAPABILITY_SCHEMA_VERSION = "performance-capability-v1"
_PERFETTO_ADAPTER_SCHEMA_VERSION = "perfetto-scheduling-adapter-v1"
_WINSCOPE_ADAPTER_SCHEMA_VERSION = "winscope-artifact-adapter-v1"
_PERFETTO_PROFILE_ID = "perfetto-scheduling-v1"
_PERFETTO_QUERY_ID = "perfetto.scheduling.by_pid.v1"
_HEX_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_INTEGER_RE = re.compile(r"-?[0-9]+")
_DEFAULT_TIMEOUT_SECONDS = 30
_DEFAULT_MAX_VERSION_OUTPUT_BYTES = 16 * 1024
_DEFAULT_MAX_QUERY_OUTPUT_BYTES = 256 * 1024
_DEFAULT_MAX_ROWS = 256
_DEFAULT_MAX_MANIFEST_BYTES = 64 * 1024
_DEFAULT_MAX_ARCHIVE_ENTRIES = 10_000
_MIN_OUTPUT_BYTES = 256
_MAX_OUTPUT_BYTES = 4 * 1024 * 1024
_MAX_TIMEOUT_SECONDS = 120
_MAX_ROWS = 1_024
_MAX_MANIFEST_BYTES = 1024 * 1024


@dataclass(frozen=True)
class _BoundedCommandResult:
    """Captured subprocess outcome, with output capped during collection."""

    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    output_exceeded: bool = False
    launch_error: str | None = None


def _safety_fields() -> dict[str, bool]:
    return {
        "finalJudgment": False,
        "notRootCauseYet": True,
        "requiresHumanConfirmation": True,
    }


def _capability_base() -> dict[str, Any]:
    return {
        "schemaVersion": _CAPABILITY_SCHEMA_VERSION,
        "tools": {
            "perfettoTraceProcessor": {
                "status": "unconfigured",
                "configured": False,
                "profileId": _PERFETTO_PROFILE_ID,
                "queryId": _PERFETTO_QUERY_ID,
            },
            "winscope": {
                "status": "unconfigured",
                "configured": False,
                "capabilityMode": "metadata_only",
            },
        },
        "gaps": [],
        "downloadPolicy": "adapter_enforced; process_egress_not_independently_proven",
        **_safety_fields(),
    }


def _query_base(target_pid: object) -> dict[str, Any]:
    return {
        "schemaVersion": _PERFETTO_ADAPTER_SCHEMA_VERSION,
        "status": "blocked",
        "queryId": _PERFETTO_QUERY_ID,
        "profileId": _PERFETTO_PROFILE_ID,
        "target": {"kind": "process", "pid": str(target_pid) if target_pid is not None else None},
        "clock": {
            "domain": "perfetto_trace",
            "unit": "ns",
            "alignmentStatus": "unmapped",
        },
        "traceArtifact": {},
        "tool": {},
        "entities": [],
        "schedulingIntervals": [],
        "gaps": [],
        **_safety_fields(),
    }


def _winscope_base() -> dict[str, Any]:
    return {
        "schemaVersion": _WINSCOPE_ADAPTER_SCHEMA_VERSION,
        "status": "blocked",
        "capabilityMode": "metadata_only",
        "release": {},
        "gaps": ["winscope_graphical_query_adapter_not_implemented"],
        "downloadPolicy": "adapter_enforced; process_egress_not_independently_proven",
        **_safety_fields(),
    }


def _append_gap(value: dict[str, Any], gap: str) -> None:
    gaps = value.setdefault("gaps", [])
    if gap not in gaps:
        gaps.append(gap)


def _is_lower_hex_sha256(value: object) -> bool:
    return isinstance(value, str) and _HEX_SHA256_RE.fullmatch(value) is not None


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _bounded_int(config: Mapping[str, Any], name: str, default: int, *, minimum: int, maximum: int) -> tuple[int | None, str | None]:
    value = config.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum or value > maximum:
        return None, f"invalid_{name}"
    return value, None


def _absolute_configured_path(value: object) -> tuple[Path | None, str | None]:
    """Validate a configured path without treating a missing artifact as bad syntax."""
    if not isinstance(value, str) or not value:
        return None, "path_missing"
    candidate = Path(value)
    if not candidate.is_absolute():
        return None, "path_not_absolute"
    try:
        return candidate.resolve(strict=False), None
    except OSError:
        return None, "path_invalid"


def _absolute_regular_path(value: object, *, executable: bool = False) -> tuple[Path | None, str | None]:
    candidate, config_error = _absolute_configured_path(value)
    if config_error:
        return None, config_error
    assert candidate is not None
    try:
        resolved = candidate.resolve(strict=True)
        mode = resolved.stat().st_mode
    except OSError:
        return None, "path_unavailable"
    if not stat.S_ISREG(mode):
        return None, "path_not_regular_file"
    if executable and not os.access(resolved, os.X_OK):
        return None, "path_not_executable"
    return resolved, None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decode_utf8(value: bytes) -> str | None:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _normalise_version_output(value: bytes) -> str | None:
    decoded = _decode_utf8(value)
    if decoded is None:
        return None
    return decoded.rstrip("\r\n")


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass


def _run_bounded_command(
    argv: Sequence[str],
    *,
    input_bytes: bytes = b"",
    timeout_seconds: int,
    max_output_bytes: int,
) -> _BoundedCommandResult:
    """Run a fixed argv with bounded stdout+stderr collection and no shell.

    Fixed P1 SQL is shorter than a pipe buffer, so writing it before collecting
    output cannot block on a caller-controlled payload.  Output is read with a
    selector and the child is terminated as soon as its combined output exceeds
    the declared cap; this avoids a post-hoc unbounded ``communicate`` buffer.
    """
    try:
        process = subprocess.Popen(
            list(argv),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
        )
    except OSError as error:
        return _BoundedCommandResult(None, b"", b"", launch_error=type(error).__name__)

    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    try:
        if input_bytes:
            process.stdin.write(input_bytes)
            process.stdin.flush()
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass

    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    stdout = bytearray()
    stderr = bytearray()
    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    output_exceeded = False

    while selector.get_map():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            _terminate_process(process)
            break
        events = selector.select(remaining)
        if not events:
            if process.poll() is not None:
                # EOF notifications follow shortly; continue until descriptors
                # close so all output below the bound is accounted for.
                continue
            continue
        for key, _ in events:
            try:
                data = os.read(key.fileobj.fileno(), 64 * 1024)
            except OSError:
                data = b""
            if not data:
                try:
                    selector.unregister(key.fileobj)
                except KeyError:
                    pass
                continue
            target = stdout if key.data == "stdout" else stderr
            allowed = max_output_bytes - len(stdout) - len(stderr)
            if allowed <= 0 or len(data) > allowed:
                if allowed > 0:
                    target.extend(data[:allowed])
                output_exceeded = True
                _terminate_process(process)
                break
            target.extend(data)
        if output_exceeded:
            break

    selector.close()
    if timed_out or output_exceeded:
        _terminate_process(process)
    try:
        returncode = process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        _terminate_process(process)
        returncode = process.poll()
    for stream in (process.stdout, process.stderr):
        try:
            stream.close()
        except OSError:
            pass
    return _BoundedCommandResult(returncode, bytes(stdout), bytes(stderr), timed_out, output_exceeded)


def _perfetto_config(config: object) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(config, Mapping):
        return None, ["performance_tool_config_missing"]
    if config.get("schemaVersion") != _CONFIG_SCHEMA_VERSION:
        return None, ["performance_tool_config_schema_unsupported"]
    raw = config.get("perfetto")
    if raw is None:
        return None, ["perfetto_not_configured"]
    if not isinstance(raw, Mapping):
        return None, ["perfetto_config_invalid"]

    path, path_error = _absolute_configured_path(raw.get("binaryPath"))
    allowed = raw.get("allowedToolSha256")
    expected_version = raw.get("expectedVersion")
    if path_error:
        return None, [f"perfetto_{path_error}"]
    if not isinstance(allowed, list) or not allowed or any(not _is_lower_hex_sha256(item) for item in allowed):
        return None, ["perfetto_allowedToolSha256_invalid"]
    if len(set(allowed)) != len(allowed):
        return None, ["perfetto_allowedToolSha256_duplicate"]
    if not isinstance(expected_version, str) or not expected_version or len(expected_version.encode("utf-8")) > _DEFAULT_MAX_VERSION_OUTPUT_BYTES:
        return None, ["perfetto_expectedVersion_invalid"]

    timeout_seconds, timeout_error = _bounded_int(
        raw, "timeoutSeconds", _DEFAULT_TIMEOUT_SECONDS, minimum=1, maximum=_MAX_TIMEOUT_SECONDS
    )
    max_version_output, version_output_error = _bounded_int(
        raw,
        "maxVersionOutputBytes",
        _DEFAULT_MAX_VERSION_OUTPUT_BYTES,
        minimum=_MIN_OUTPUT_BYTES,
        maximum=_MAX_OUTPUT_BYTES,
    )
    max_query_output, query_output_error = _bounded_int(
        raw,
        "maxQueryOutputBytes",
        _DEFAULT_MAX_QUERY_OUTPUT_BYTES,
        minimum=_MIN_OUTPUT_BYTES,
        maximum=_MAX_OUTPUT_BYTES,
    )
    max_rows, rows_error = _bounded_int(raw, "maxRows", _DEFAULT_MAX_ROWS, minimum=1, maximum=_MAX_ROWS)
    errors = [item for item in (timeout_error, version_output_error, query_output_error, rows_error) if item]
    if errors:
        return None, [f"perfetto_{item}" for item in errors]
    assert path is not None
    assert timeout_seconds is not None
    assert max_version_output is not None
    assert max_query_output is not None
    assert max_rows is not None
    return {
        "binaryPath": path,
        "allowedToolSha256": tuple(allowed),
        "expectedVersion": expected_version,
        "timeoutSeconds": timeout_seconds,
        "maxVersionOutputBytes": max_version_output,
        "maxQueryOutputBytes": max_query_output,
        "maxRows": max_rows,
    }, []


def _probe_perfetto(config: object) -> tuple[dict[str, Any], list[str], dict[str, Any] | None]:
    result: dict[str, Any] = {
        "status": "unconfigured",
        "configured": False,
        "profileId": _PERFETTO_PROFILE_ID,
        "queryId": _PERFETTO_QUERY_ID,
    }
    parsed, errors = _perfetto_config(config)
    if parsed is None:
        result["status"] = "invalid_config" if errors != ["perfetto_not_configured"] else "unconfigured"
        return result, errors, None

    path = parsed["binaryPath"]
    result.update({"configured": True, "binaryPath": str(path)})
    actual_path, path_error = _absolute_regular_path(str(path), executable=True)
    if path_error:
        result["status"] = "unavailable"
        return result, [f"perfetto_{path_error}"], None
    assert actual_path is not None
    path = actual_path
    result["binaryPath"] = str(path)
    parsed["binaryPath"] = path
    try:
        binary_sha256 = _sha256_file(path)
    except OSError:
        result["status"] = "unavailable"
        return result, ["perfetto_binary_hash_unavailable"], None
    result["binarySha256"] = binary_sha256
    if binary_sha256 not in parsed["allowedToolSha256"]:
        result["status"] = "tool_unapproved"
        return result, ["perfetto_tool_sha256_not_allowlisted"], None

    command = _run_bounded_command(
        [str(path), "--version"],
        timeout_seconds=parsed["timeoutSeconds"],
        max_output_bytes=parsed["maxVersionOutputBytes"],
    )
    if command.launch_error:
        result["status"] = "unavailable"
        return result, ["perfetto_version_command_unavailable"], None
    if command.timed_out:
        result["status"] = "unavailable"
        return result, ["perfetto_version_timeout"], None
    if command.output_exceeded:
        result["status"] = "invalid_config"
        return result, ["perfetto_version_output_exceeded_limit"], None
    if command.returncode != 0:
        result["status"] = "unavailable"
        return result, ["perfetto_version_command_failed"], None
    version = _normalise_version_output(command.stdout)
    if version is None or not version:
        result["status"] = "invalid_config"
        return result, ["perfetto_version_output_not_utf8"], None
    result["versionOutput"] = version
    result["versionOutputSha256"] = hashlib.sha256(command.stdout).hexdigest()
    if version != parsed["expectedVersion"]:
        result["status"] = "version_mismatch"
        return result, ["perfetto_version_output_not_allowlisted"], None

    result["status"] = "available"
    return result, [], parsed


def _winscope_config(config: object) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(config, Mapping):
        return None, ["performance_tool_config_missing"]
    if config.get("schemaVersion") != _CONFIG_SCHEMA_VERSION:
        return None, ["performance_tool_config_schema_unsupported"]
    raw = config.get("winscope")
    if raw is None:
        return None, ["winscope_not_configured"]
    if not isinstance(raw, Mapping):
        return None, ["winscope_config_invalid"]
    path, path_error = _absolute_configured_path(raw.get("archivePath"))
    expected_hash = raw.get("expectedSha256")
    expected_version = raw.get("expectedVersion")
    expected_release_schema = raw.get("expectedReleaseManifestSchemaVersion")
    expected_manifest_schema = raw.get("expectedManifestSchemaVersion")
    expected_target = raw.get("expectedTarget")
    if path_error:
        return None, [f"winscope_{path_error}"]
    if not _is_lower_hex_sha256(expected_hash):
        return None, ["winscope_expectedSha256_invalid"]
    if not isinstance(expected_version, str) or not expected_version:
        return None, ["winscope_expectedVersion_invalid"]
    if not _is_positive_int(expected_release_schema):
        return None, ["winscope_expectedReleaseManifestSchemaVersion_invalid"]
    if not _is_positive_int(expected_manifest_schema):
        return None, ["winscope_expectedManifestSchemaVersion_invalid"]
    if not isinstance(expected_target, Mapping) or set(expected_target) != {"operatingSystem", "architecture"} or not all(
        isinstance(expected_target.get(field), str) and expected_target[field] for field in ("operatingSystem", "architecture")
    ):
        return None, ["winscope_expectedTarget_invalid"]
    max_manifest, manifest_error = _bounded_int(
        raw,
        "maxManifestBytes",
        _DEFAULT_MAX_MANIFEST_BYTES,
        minimum=128,
        maximum=_MAX_MANIFEST_BYTES,
    )
    max_entries, entry_error = _bounded_int(
        raw,
        "maxArchiveEntries",
        _DEFAULT_MAX_ARCHIVE_ENTRIES,
        minimum=2,
        maximum=100_000,
    )
    errors = [item for item in (manifest_error, entry_error) if item]
    if errors:
        return None, [f"winscope_{item}" for item in errors]
    assert path is not None
    assert max_manifest is not None
    assert max_entries is not None
    return {
        "archivePath": path,
        "expectedSha256": expected_hash,
        "expectedVersion": expected_version,
        "expectedReleaseManifestSchemaVersion": expected_release_schema,
        "expectedManifestSchemaVersion": expected_manifest_schema,
        "expectedTarget": dict(expected_target),
        "maxManifestBytes": max_manifest,
        "maxArchiveEntries": max_entries,
    }, []


def _zip_name_is_safe(name: str) -> bool:
    normalised = name.replace("\\", "/")
    path = PurePosixPath(normalised)
    return not path.is_absolute() and ".." not in path.parts and not normalised.startswith("/")


def _read_zip_json(archive: zipfile.ZipFile, names: Mapping[str, list[zipfile.ZipInfo]], name: str, *, max_bytes: int) -> tuple[dict[str, Any] | None, str | None]:
    matches = names.get(name, [])
    if len(matches) != 1:
        return None, f"winscope_{name}_missing_or_duplicate"
    info = matches[0]
    if info.is_dir() or info.file_size > max_bytes:
        return None, f"winscope_{name}_invalid_size"
    try:
        raw = archive.read(info)
        if len(raw) > max_bytes:
            return None, f"winscope_{name}_invalid_size"
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile):
        return None, f"winscope_{name}_invalid_json"
    if not isinstance(value, dict):
        return None, f"winscope_{name}_invalid_json"
    return value, None


def inspect_winscope_release_artifact(config: object) -> dict[str, Any]:
    """Verify a configured packaged WinScope artifact without extracting or running it."""
    result = _winscope_base()
    parsed, errors = _winscope_config(config)
    if parsed is None:
        result["status"] = "invalid_config" if errors != ["winscope_not_configured"] else "unconfigured"
        result["gaps"] = [*errors, *result["gaps"]]
        return result

    archive_path = parsed["archivePath"]
    result["archivePath"] = str(archive_path)
    actual_archive_path, path_error = _absolute_regular_path(str(archive_path))
    if path_error:
        result["status"] = "unavailable"
        _append_gap(result, f"winscope_{path_error}")
        return result
    assert actual_archive_path is not None
    archive_path = actual_archive_path
    result["archivePath"] = str(archive_path)
    try:
        archive_sha256 = _sha256_file(archive_path)
    except OSError:
        result["status"] = "unavailable"
        _append_gap(result, "winscope_archive_hash_unavailable")
        return result
    result["archiveSha256"] = archive_sha256
    if archive_sha256 != parsed["expectedSha256"]:
        result["status"] = "artifact_hash_mismatch"
        _append_gap(result, "winscope_archive_sha256_mismatch")
        return result

    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            if len(infos) > parsed["maxArchiveEntries"]:
                result["status"] = "unsupported"
                _append_gap(result, "winscope_archive_entry_limit_exceeded")
                return result
            if any(not _zip_name_is_safe(info.filename) for info in infos):
                result["status"] = "unsupported"
                _append_gap(result, "winscope_archive_path_unsafe")
                return result
            names: dict[str, list[zipfile.ZipInfo]] = {}
            for info in infos:
                names.setdefault(info.filename, []).append(info)
            manifest, manifest_error = _read_zip_json(archive, names, "manifest.json", max_bytes=parsed["maxManifestBytes"])
            release, release_error = _read_zip_json(
                archive, names, "release-manifest.json", max_bytes=parsed["maxManifestBytes"]
            )
    except (OSError, zipfile.BadZipFile):
        result["status"] = "unsupported"
        _append_gap(result, "winscope_archive_not_valid_zip")
        return result

    if manifest_error:
        result["status"] = "unsupported"
        _append_gap(result, manifest_error)
        return result
    if release_error:
        result["status"] = "unsupported"
        _append_gap(result, release_error)
        return result
    assert manifest is not None
    assert release is not None

    if manifest.get("schemaVersion") != parsed["expectedManifestSchemaVersion"]:
        result["status"] = "unsupported_schema"
        _append_gap(result, "winscope_manifest_schema_unsupported")
        return result
    if release.get("schemaVersion") != parsed["expectedReleaseManifestSchemaVersion"]:
        result["status"] = "unsupported_schema"
        _append_gap(result, "winscope_release_manifest_schema_unsupported")
        return result
    if release.get("version") != parsed["expectedVersion"]:
        result["status"] = "version_mismatch"
        _append_gap(result, "winscope_release_version_mismatch")
        return result
    target = release.get("target")
    if target != parsed["expectedTarget"]:
        result["status"] = "unsupported"
        _append_gap(result, "winscope_release_target_mismatch")
        return result
    launcher = release.get("launcher")
    if not isinstance(launcher, str) or not _zip_name_is_safe(launcher) or launcher not in names or len(names[launcher]) != 1:
        result["status"] = "unsupported"
        _append_gap(result, "winscope_release_launcher_invalid")
        return result

    result["status"] = "available"
    result["release"] = {
        "schemaVersion": release["schemaVersion"],
        "version": release["version"],
        "target": target,
        "launcher": launcher,
        "sourceCommit": release.get("sourceCommit") if isinstance(release.get("sourceCommit"), str) else None,
        "manifestSchemaVersion": manifest["schemaVersion"],
    }
    return result


def probe_performance_capabilities(config: object) -> dict[str, Any]:
    """Probe only explicitly configured local Perfetto and WinScope artifacts."""
    result = _capability_base()
    perfetto, perfetto_gaps, _ = _probe_perfetto(config)
    winscope = inspect_winscope_release_artifact(config)
    result["tools"]["perfettoTraceProcessor"] = perfetto
    result["tools"]["winscope"] = {
        "status": winscope["status"],
        "configured": winscope["status"] != "unconfigured",
        "capabilityMode": "metadata_only",
        **({"archivePath": winscope["archivePath"]} if "archivePath" in winscope else {}),
        **({"archiveSha256": winscope["archiveSha256"]} if "archiveSha256" in winscope else {}),
        **({"release": winscope["release"]} if winscope.get("status") == "available" else {}),
    }
    result["gaps"] = list(dict.fromkeys([*perfetto_gaps, *winscope["gaps"]]))
    return result


def _scheduling_sql(target_pid: int, max_rows: int) -> str:
    return f"""SELECT
  s.ts AS ts,
  COALESCE(s.dur, -1) AS dur,
  COALESCE(s.cpu, -1) AS cpu,
  t.tid AS tid
FROM sched AS s
JOIN thread AS t USING (utid)
JOIN process AS p USING (upid)
WHERE p.pid = {target_pid}
ORDER BY s.ts
LIMIT {max_rows};
"""


def _parse_scheduling_csv(stdout: bytes, *, max_rows: int) -> tuple[list[dict[str, int]] | None, str | None]:
    decoded = _decode_utf8(stdout)
    if decoded is None:
        return None, "perfetto_query_output_not_utf8"
    try:
        rows = list(csv.reader(io.StringIO(decoded, newline="")))
    except csv.Error:
        return None, "perfetto_query_output_invalid_csv"
    if not rows or rows[0] != ["ts", "dur", "cpu", "tid"]:
        return None, "perfetto_query_output_schema_unsupported"
    data_rows = rows[1:]
    if len(data_rows) > max_rows:
        return None, "perfetto_query_output_row_limit_exceeded"
    intervals: list[dict[str, int]] = []
    previous_ts = -1
    for row in data_rows:
        if len(row) != 4 or any(_INTEGER_RE.fullmatch(field) is None for field in row):
            return None, "perfetto_query_output_schema_unsupported"
        ts, duration, cpu, tid = (int(field) for field in row)
        if ts < 0 or duration < -1 or cpu < -1 or tid <= 0 or ts < previous_ts:
            return None, "perfetto_query_output_value_unsupported"
        previous_ts = ts
        intervals.append({"ts": ts, "dur": duration, "cpu": cpu, "tid": tid})
    return intervals, None


def run_perfetto_scheduling_query(trace_path: str | Path, config: object, *, target_pid: int) -> dict[str, Any]:
    """Execute the sole P1 scheduling query for an exact positive PID.

    The query template, output columns, row cap, invocation style, and tool
    identity are all fixed by this module/configuration.  No caller SQL is
    accepted and results cannot promote a root-cause judgment.
    """
    result = _query_base(target_pid)
    if not _is_positive_int(target_pid):
        _append_gap(result, "target_pid_invalid")
        return result
    trace, trace_error = _absolute_regular_path(str(trace_path))
    if trace_error:
        _append_gap(result, f"trace_{trace_error}")
        return result
    assert trace is not None
    try:
        trace_sha256 = _sha256_file(trace)
        trace_bytes = trace.stat().st_size
    except OSError:
        _append_gap(result, "trace_identity_unavailable")
        return result
    result["target"] = {"kind": "process", "pid": target_pid}
    result["traceArtifact"] = {
        "path": str(trace),
        "sha256": trace_sha256,
        "bytes": trace_bytes,
        "provenanceStatus": "unverified_generic_local_trace",
    }
    _append_gap(result, "trace_capture_provenance_unverified")
    _append_gap(result, "clock_mapping_unavailable")
    _append_gap(result, "process_name_mapping_unavailable")

    capability, capability_gaps, parsed = _probe_perfetto(config)
    result["tool"] = capability
    if parsed is None:
        for gap in capability_gaps:
            _append_gap(result, gap)
        return result
    if capability["status"] != "available":
        for gap in capability_gaps:
            _append_gap(result, gap)
        _append_gap(result, "perfetto_capability_not_available")
        return result

    sql = _scheduling_sql(target_pid, parsed["maxRows"])
    result["querySha256"] = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    result["queryTemplateSha256"] = hashlib.sha256(
        _scheduling_sql(0, parsed["maxRows"]).replace("p.pid = 0", "p.pid = {target_pid}").encode("utf-8")
    ).hexdigest()
    command = _run_bounded_command(
        [str(parsed["binaryPath"]), "query", "-f", "-", str(trace)],
        input_bytes=sql.encode("utf-8"),
        timeout_seconds=parsed["timeoutSeconds"],
        max_output_bytes=parsed["maxQueryOutputBytes"],
    )
    if command.launch_error:
        _append_gap(result, "perfetto_query_command_unavailable")
        return result
    if command.timed_out:
        _append_gap(result, "perfetto_query_timeout")
        return result
    if command.output_exceeded:
        _append_gap(result, "perfetto_query_output_exceeded_limit")
        return result
    if command.returncode != 0:
        _append_gap(result, "perfetto_query_failed")
        return result
    intervals, parse_error = _parse_scheduling_csv(command.stdout, max_rows=parsed["maxRows"])
    if parse_error:
        result["status"] = "unsupported"
        _append_gap(result, parse_error)
        return result
    assert intervals is not None
    result["schedulingIntervals"] = intervals
    result["entities"] = [
        {
            "kind": "thread",
            "tid": tid,
            "processPid": target_pid,
            "mappingStatus": "pid_constrained_by_fixed_query",
        }
        for tid in sorted({interval["tid"] for interval in intervals})
    ]
    result["status"] = "observed"
    return result


__all__ = [
    "inspect_winscope_release_artifact",
    "probe_performance_capabilities",
    "run_perfetto_scheduling_query",
]
