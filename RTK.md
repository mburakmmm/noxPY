# RTK Strict Command Policy (NoxPy)

Every shell command in this repository MUST route through **RTK** (`rtk`) strictly:

1. **Mandatory RTK commands** (never run bare equivalents when RTK supports them):
   - Files & search: `rtk ls`, `rtk tree`, `rtk find`, `rtk grep`, `rtk rg`, `rtk read`, `rtk wc`
   - Git & diffs: `rtk git ...`, `rtk diff ...`, `rtk gh ...`
   - Builds & tests: `rtk err <cmd>`, `rtk test <cmd>`, `rtk pytest ...`
   - Network / JSON / Env: `rtk curl ...`, `rtk json ...`, `rtk env`
2. **No post-filtering of RTK output**: Never pipe `rtk ...` into `| head`, `| tail`, `| cut`, `| awk`, or `| sed`.
3. **No raw file-slicing commands**: Never read files via raw `cat`, `head`, `tail`, or `sed -n`. Use the agent `read_file` tool (bounded line range) or `rtk read`.
4. **Exact raw output escape hatch**: Only when byte-exact raw stdout is strictly required (cryptographic hashes, binary symbol audits `nm`/`otool`, or reproducible evidence logs), use `rtk proxy <cmd>` (or `rtk bypass <cmd>` if RTK condensed output is unusable).
5. **Strict guard enforcement**: `scripts/hooks/rtk_strict_guard.py` hard-denies violations (`RTK_MISSING`, `RTK_NO_PIPE_FILTER`, `RTK_NO_RAW_FILE_SLICE`, `RTK_REQUIRED`) in both Antigravity (`.agents/hooks.json`) and Claude Code (`.claude/settings.json`).
