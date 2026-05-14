#!/usr/bin/env python3
"""Render replay runs dashboard from a replay index."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from anr_evidence.dashboard import render_replay_dashboard


def main() -> int:
    parser = argparse.ArgumentParser(description="Render replay runs dashboard")
    parser.add_argument('index_path')
    parser.add_argument('--format', default='markdown', choices=['markdown', 'html'])
    parser.add_argument('-o', '--output')
    args = parser.parse_args()

    rendered = render_replay_dashboard(args.index_path, format=args.format)
    if args.output:
        Path(args.output).write_text(rendered, encoding='utf-8')
    else:
        print(rendered)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
