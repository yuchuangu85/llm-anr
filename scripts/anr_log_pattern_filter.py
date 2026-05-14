"""Standalone ANR EventLog filter.

Uses the shared ANR log filtering algorithm from ``anr_evidence.log_filter`` so
large EventLog files are scanned in two phases instead of being fully loaded
into memory.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from anr_evidence.log_filter import (
    DEFAULT_EVENT_LOG_TAGS,
    LogFilterSpec,
    event_log_tags_master_path,
    filter_file_preceding_anchor_window,
    parse_tags_from_markdown,
)


def filter_anr_logs(log_file: str, tag_sources: list[str], package_name: str | None = None) -> list[str]:
    tags = set(DEFAULT_EVENT_LOG_TAGS)
    tags.update(parse_tags_from_markdown(tag_sources))
    print(f"[*] Loaded {len(tags)} event-log tags.")

    result = filter_file_preceding_anchor_window(
        log_file,
        "am_anr",
        LogFilterSpec(
            source_kind="event_log",
            before_seconds=12,
            after_seconds=0,
            include_patterns=frozenset(tags),
            package_name=package_name,
            package_filter_scope="anchor",
        ),
    )

    for warning in result.warnings:
        print(f"[!] {warning['code']}: {warning['message']}")
    if not result.matched_anchor:
        return []

    print(f"[+] Found ANR event: {result.matched_anchor.strip()}")
    print("\n" + "=" * 50)
    print("FILTERED ANR EVENT LOGS")
    print("=" * 50)
    for line in result.lines:
        print(line)
    print("=" * 50)
    return result.lines


def main() -> None:
    parser = argparse.ArgumentParser(description="Filter EventLog for the 12s pre-ANR diagnostic window.")
    parser.add_argument("log_file", help="Path to the event log file")
    parser.add_argument("--tags", nargs="*", default=[str(event_log_tags_master_path())], help="Markdown files containing EventLog tags")
    parser.add_argument("--package", help="Optional package name to require in retained lines", default=None)
    args = parser.parse_args()
    filter_anr_logs(args.log_file, args.tags, args.package)


if __name__ == "__main__":
    main()
