"""Small cross-platform path helpers.

The ANR pipeline handles paths from two different worlds:

* local filesystem paths, whose separator depends on the host OS; and
* archive member names / fixture entries, which may contain either ``/`` or
  ``\\`` regardless of the host that is currently running the code.

Use these helpers anywhere path text is inspected semantically instead of
letting ``pathlib.Path`` interpret Windows-style text on POSIX hosts (or vice
versa).
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath


def normalize_path_text(path: str | Path) -> str:
    """Return *path* with separators normalized to POSIX-style ``/``."""

    return str(path).replace("\\", "/")


def path_name(path: str | Path) -> str:
    """Return the final path component for either Windows or POSIX text."""

    return PurePosixPath(normalize_path_text(path)).name
