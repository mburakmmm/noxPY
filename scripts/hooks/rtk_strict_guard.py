#!/usr/bin/env python3
"""Strict RTK PreToolUse guard for Antigravity (`run_command`) and Claude Code (`Bash`).

Enforces:
  S1 (RTK_MISSING):         `rtk` binary must be available on PATH.
  S2 (RTK_NO_PIPE_FILTER):  Never pipe `rtk` output through `| head|tail|cut|awk|sed`.
  S3 (RTK_NO_RAW_FILE_SLICE): Never use raw `cat`/`head`/`tail`/`sed -n` to read files.
  S4 (RTK_REQUIRED):        Any command with an `rtk hook check` rewrite must use `rtk ...` directly.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys

PIPE_FILTER_RE = re.compile(r"(?:^|\s)rtk\s+.*\|\s*(head|tail|cut|awk|sed)\b")
RAW_READ_RE = re.compile(r"(?:^|&&|\|\||;)\s*(?:cat|head|tail)\s+|(?:^|&&|\|\||;)\s*sed\s+-n\b")
SCRIPT_PREFIX_RE = re.compile(r"^\s*(?:\./|bash\s+\.?/?(?:experiments|tests|scripts)/|sh\s+\.?/?(?:experiments|tests|scripts)/)")


def extract_command(payload: dict, agent: str) -> str:
    if agent == "antigravity":
        tc = payload.get("toolCall") or {}
        args = tc.get("args") or {}
        return str(args.get("CommandLine") or "")
    ti = payload.get("tool_input") or {}
    return str(ti.get("command") or "")


def emit_decision(agent: str, allow: bool, reason: str = "") -> int:
    if agent == "antigravity":
        if allow:
            sys.stdout.write(json.dumps({"decision": "allow"}))
        else:
            sys.stdout.write(json.dumps({"decision": "deny", "reason": reason}))
        return 0
    if not allow:
        sys.stdout.write(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }
            )
        )
    return 0


def check_command(cmd: str) -> tuple[bool, str]:
    stripped = cmd.strip()
    if not stripped:
        return True, ""

    # S1: rtk must be installed
    rtk_bin = shutil.which("rtk")
    if not rtk_bin:
        return False, "[RTK_MISSING] `rtk` binary not found on PATH. Install/expose `rtk` before running commands."

    # S2: Never pipe rtk output into head/tail/cut/awk/sed
    if PIPE_FILTER_RE.search(stripped):
        return (
            False,
            "[RTK_NO_PIPE_FILTER] Do not pipe `rtk` output through `| head/tail/cut/awk/sed`. "
            "Use `rtk` directly or `rtk proxy <cmd>` when exact raw output is required.",
        )

    # S3: Never use raw cat/head/tail/sed -n (unless explicitly wrapped in rtk read / rtk proxy)
    if not stripped.startswith("rtk ") and RAW_READ_RE.search(stripped):
        return (
            False,
            "[RTK_NO_RAW_FILE_SLICE] Raw `cat`/`head`/`tail`/`sed -n` is blocked by strict RTK policy. "
            "Use `read_file` or `rtk read <file>`.",
        )

    # S4: Require rtk prefix whenever `rtk hook check` reports a rewrite
    if not stripped.startswith("rtk ") and not SCRIPT_PREFIX_RE.match(stripped):
        res = subprocess.run(
            [rtk_bin, "hook", "check", stripped],
            capture_output=True,
            text=True,
        )
        out = (res.stdout or "").strip()
        if out and not out.startswith("No rewrite for:"):
            return (
                False,
                f"[RTK_REQUIRED] Direct bare command blocked by strict RTK policy. Use: `{out}`",
            )

    return True, ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", choices=("antigravity", "claude"), required=True)
    args = parser.parse_args()

    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return emit_decision(args.agent, False, "[RTK_GUARD_ERROR] Malformed hook JSON payload.")

    cmd = extract_command(payload, args.agent)
    allow, reason = check_command(cmd)
    return emit_decision(args.agent, allow, reason)


if __name__ == "__main__":
    raise SystemExit(main())
