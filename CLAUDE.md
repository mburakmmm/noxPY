# NoxPy Agent Instructions

@AGENTS.md
@RTK.md

## Upstream Boundary & Workflow

- **NEVER modify `/Users/melihburakmemis/Documents/nox-lang`** — upstream `nox-lang` is strictly read-only and managed exclusively by the user.
- Follow the **Requirement -> Upstream Solution -> Progress** loop: complete NoxPy-side work, record blockers + reproducers in `UPSTREAM_REQUIREMENTS.md`, and advance phases only when upstream criteria are genuinely verified.

## Project Tooling & Hook Verification

- Strict RTK hooks are configured for both Google Antigravity (`.agents/hooks.json` + `.agents/plugins/rtk/`) and Claude Code (`.claude/settings.json`).
- Verify hook configuration and live strict enforcement at any time with:
  ```sh
  rtk err python3 scripts/hooks/verify_rtk_enforcement.py
  ```
- Maintain `UPSTREAM_REQUIREMENTS.md` and `TODO.md` strictly grounded in executed evidence.
