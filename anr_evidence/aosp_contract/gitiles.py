"""Fail-closed retrieval of byte-level provenance from Android Gitiles."""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_OFFICIAL_ROOT = "https://android.googlesource.com"
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_TAG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_REPOSITORY_RE = re.compile(r"platform/[A-Za-z0-9][A-Za-z0-9._/-]*")
_PATH_SEGMENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+\-]*")


class GitilesProvenanceError(ValueError):
    """An official source byte record could not be retrieved and verified."""


@dataclass(frozen=True)
class GitilesSourceRecord:
    """A locally hashed source file pinned to an immutable Gitiles revision."""

    release_tag: str
    repository: str
    revision: str
    path: str
    symbol: str
    url: str
    sha256: str
    byte_length: int


def _require_repository(repository: str) -> str:
    if (
        not isinstance(repository, str)
        or not _REPOSITORY_RE.fullmatch(repository)
        or "//" in repository
        or any(part in {"", ".", ".."} for part in repository.split("/"))
    ):
        raise GitilesProvenanceError("repository must be a safe platform/... Gitiles project path")
    return repository


def _require_tag(release_tag: str) -> str:
    if not isinstance(release_tag, str) or not _TAG_RE.fullmatch(release_tag):
        raise GitilesProvenanceError("release_tag must be a safe Git tag name")
    return release_tag


def _require_revision(revision: str) -> str:
    if not isinstance(revision, str) or not _COMMIT_RE.fullmatch(revision):
        raise GitilesProvenanceError("revision must be an immutable lowercase 40-character commit")
    return revision


def _require_path(path: str) -> str:
    if (
        not isinstance(path, str)
        or not path
        or path.startswith("/")
        or any(not _PATH_SEGMENT_RE.fullmatch(part) for part in path.split("/"))
    ):
        raise GitilesProvenanceError("path must be a safe relative source path")
    return path


def _require_symbol(symbol: str) -> str:
    if not isinstance(symbol, str) or not symbol.strip() or any(ord(char) < 32 for char in symbol):
        raise GitilesProvenanceError("symbol must be a non-empty printable string")
    return symbol


def tag_ref_url(repository: str, release_tag: str) -> str:
    """Return the canonical Gitiles JSON URL used to resolve a release tag."""
    return f"{_OFFICIAL_ROOT}/{_require_repository(repository)}/+/refs/tags/{_require_tag(release_tag)}?format=JSON"


def immutable_file_url(repository: str, revision: str, path: str) -> str:
    """Return the canonical, query-free Gitiles URL for an immutable source file."""
    return f"{_OFFICIAL_ROOT}/{_require_repository(repository)}/+/{_require_revision(revision)}/{_require_path(path)}"


def _json_from_gitiles(payload: bytes) -> Any:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise GitilesProvenanceError("Gitiles JSON response is not UTF-8") from error
    if text.startswith(")]}'"):
        text = text[4:]
        if text.startswith("\r\n"):
            text = text[2:]
        elif text.startswith("\n"):
            text = text[1:]
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise GitilesProvenanceError("Gitiles tag response is not valid JSON") from error


def _one_revision(values: list[Any], label: str) -> str | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    if any(not isinstance(value, str) or not _COMMIT_RE.fullmatch(value) for value in present):
        raise GitilesProvenanceError(f"Gitiles {label} is not an immutable commit")
    if len(set(present)) != 1:
        raise GitilesProvenanceError(f"Gitiles {label} has conflicting immutable revisions")
    return present[0]


def revision_from_tag_json(payload: bytes, release_tag: str) -> str:
    """Extract one immutable commit from a Gitiles tag-ref JSON response.

    Gitiles deployments expose either a direct dereferenced commit object or a
    refs mapping. Annotated tags use ``peeled``; lightweight tags use ``value``.
    Multiple present representations must agree.
    """
    tag = _require_tag(release_tag)
    data = _json_from_gitiles(payload)
    if not isinstance(data, dict):
        raise GitilesProvenanceError("Gitiles tag response must be a JSON object")

    direct = _one_revision([data.get(key) for key in ("id", "commit", "revision")], "direct revision")
    ref = data.get(f"refs/tags/{tag}")
    if ref is not None and not isinstance(ref, dict):
        raise GitilesProvenanceError("Gitiles tag ref entry must be a JSON object")
    referenced = None
    if ref is not None:
        # A peeled target is the commit; do not treat the annotated tag object
        # in value as an interchangeable commit.
        referenced = _one_revision(
            [ref.get("peeled")] if ref.get("peeled") is not None else [ref.get("value")],
            "tag revision",
        )
    revision = _one_revision([direct, referenced], "resolved revision")
    if revision is None:
        raise GitilesProvenanceError("Gitiles tag response does not provide an immutable revision")
    return revision


def _fetch(url: str, timeout: float) -> bytes:
    try:
        request = Request(url, headers={"Accept": "application/json, text/plain"})
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except (HTTPError, URLError, OSError, TimeoutError) as error:
        raise GitilesProvenanceError(f"Gitiles request failed: {error}") from error


def fetch_source_record(
    *,
    release_tag: str,
    repository: str,
    path: str,
    symbol: str,
    timeout: float = 20.0,
) -> GitilesSourceRecord:
    """Retrieve, decode, and hash exactly one official Gitiles source file."""
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise GitilesProvenanceError("timeout must be a positive number")
    repository = _require_repository(repository)
    release_tag = _require_tag(release_tag)
    path = _require_path(path)
    symbol = _require_symbol(symbol)
    revision = revision_from_tag_json(_fetch(tag_ref_url(repository, release_tag), float(timeout)), release_tag)
    url = immutable_file_url(repository, revision, path)
    encoded = _fetch(f"{url}?format=TEXT", float(timeout)).strip()
    try:
        source_bytes = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise GitilesProvenanceError("Gitiles TEXT response is not strict Base64") from error
    return GitilesSourceRecord(
        release_tag=release_tag,
        repository=repository,
        revision=revision,
        path=path,
        symbol=symbol,
        url=url,
        sha256=hashlib.sha256(source_bytes).hexdigest(),
        byte_length=len(source_bytes),
    )
