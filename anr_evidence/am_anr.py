"""Helpers for parsing Android EventLog ``am_anr`` anchor lines."""

from __future__ import annotations

import re

_ANR_IN_PACKAGE_RE = re.compile(r"\bANR\s+in\s+([^\s:]+(?::[^\s:]+)?)", re.IGNORECASE)
_AM_ANR_PAYLOAD_RE = re.compile(r"\bam_anr\b\s*:?[\s:]*\[([^\]]+)\]", re.IGNORECASE)


def package_name_from_am_anr_line(line: str) -> str | None:
    """Extract the process/package token from a timestamped ``am_anr`` line.

    Supported common forms include::

        am_anr ANR in com.example.app: Input dispatching timed out
        I am_anr: [0,1234,com.example.app,1,Input dispatching timed out]

    Returns ``None`` when the line is not an ``am_anr`` record or the package
    field cannot be identified conservatively.
    """

    if "am_anr" not in line.lower():
        return None

    anr_in = _ANR_IN_PACKAGE_RE.search(line)
    if anr_in:
        return _clean_package_token(anr_in.group(1))

    payload = _AM_ANR_PAYLOAD_RE.search(line)
    if payload:
        fields = [field.strip() for field in payload.group(1).split(",")]
        if len(fields) >= 3:
            return _clean_package_token(fields[2])

    return None


def _clean_package_token(raw: str) -> str | None:
    token = raw.strip().strip("'\"")
    token = token.rstrip(";,]")
    if not token or token.isdigit():
        return None
    return token
