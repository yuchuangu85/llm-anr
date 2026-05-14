"""Shared timestamp and sharded-file selection helpers."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any

from ...log_filter import parse_log_timestamp
from ...path_utils import path_name


def parse_raw_timestamp(raw_timestamp: str | None) -> datetime | None:
    if not raw_timestamp:
        return None
    return parse_log_timestamp(raw_timestamp)


def parse_shard_filename_timestamp(filename: str) -> datetime | None:
    """Parse a timestamp from vendor sharded log filenames.

    Expected format: ``..._MM_DD_HH_MM_SS.txt``.  The filename has no year, so
    the shared Android log timestamp default year is used for comparisons.
    """

    m = re.search(r"_(\d{2})_(\d{2})_(\d{2})_(\d{2})_(\d{2})\.txt$", filename)
    if not m:
        return None
    try:
        return datetime(2026, int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5]))
    except ValueError:
        return None


def select_preceding_entries_for_anchor(entries: list[dict[str, Any]], anchor_dt: datetime | None) -> list[dict[str, Any]]:
    """Select the target sharded log file by trace ANR time.

    Traverse timestamped files in order, find the first file whose filename
    timestamp is greater than the trace ANR time, and return the immediately
    preceding same-type file.  If no predecessor or timestamped filenames exist,
    keep the original entries unchanged.  If no later file exists, return the
    latest file before the anchor.
    """

    if anchor_dt is None:
        return entries

    timestamped: list[tuple[datetime, dict[str, Any]]] = []
    for entry in entries:
        timestamp = parse_shard_filename_timestamp(path_name(entry.get("path", "")))
        if timestamp is not None:
            timestamped.append((timestamp, entry))
    if not timestamped:
        return entries

    timestamped.sort(key=lambda item: (item[0], item[1].get("path", "")))
    previous: dict[str, Any] | None = None
    for timestamp, entry in timestamped:
        if timestamp > anchor_dt:
            return [previous] if previous is not None else entries
        previous = entry
    return [previous] if previous is not None else entries
