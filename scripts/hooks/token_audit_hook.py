#!/usr/bin/env python3
"""PostToolUse audit logger for Antigravity and Claude Code.

Records command usage and RTK compliance into `.agents/rtk-audit/YYYY-MM-DD.jsonl`.
Never blocks tool completion.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", choices=("antigravity", "claude"), required=True)
    args = parser.parse_args()

    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    if args.agent == "antigravity":
        tc = payload.get("toolCall") or {}
        tool = str(tc.get("name") or "")
        targs = tc.get("args") or {}
        cmd = str(targs.get("CommandLine") or "")
        root = str((payload.get("workspacePaths") or [payload.get("cwd") or "."])[0])
        err = payload.get("error")
    else:
        tool = str(payload.get("tool_name") or "")
        ti = payload.get("tool_input") or {}
        cmd = str(ti.get("command") or "")
        root = os.environ.get("CLAUDE_PROJECT_DIR") or str(payload.get("cwd") or ".")
        err = None

    audit_dir = os.path.join(root, ".agents", "rtk-audit")
    try:
        os.makedirs(audit_dir, exist_ok=True)
        rec = {
            "t": int(time.time()),
            "agent": args.agent,
            "tool": tool,
            "rtk": cmd.strip().startswith("rtk "),
            "cmd": cmd.strip()[:160],
        }
        if err:
            rec["error"] = str(err)[:160]
        log_path = os.path.join(audit_dir, time.strftime("%Y-%m-%d") + ".jsonl")
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass

    if args.agent == "antigravity":
        sys.stdout.write("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
