## Cursor integration

This project rule is a materialized regular file. Its Cursor front matter remains outside the generated guidance block, while shared project instructions and this Cursor-specific fragment are refreshed by the generator.

## Coding conventions

- Python 3.14+, use `from __future__ import annotations`
- Follow PEP 8, type hints for all public APIs
- Tests via `unittest`, discoverable: `python3 -m unittest discover -s tests -v`
- No comments for obvious code; only document non-obvious WHY
- Prefer editing existing files over creating new ones
- Don't add features, refactors, or abstractions beyond what the task requires
