## Claude Code integration

`CLAUDE.md` uses project-local native `@agent_guidance/...` imports for the shared and Claude-specific fragments. `.claude/settings.local.json` is a local permission configuration, not project guidance; generation never reads or rewrites it.
