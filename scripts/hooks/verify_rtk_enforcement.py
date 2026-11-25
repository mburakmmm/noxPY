#!/usr/bin/env python3
"""Zero-side-effect verifier for strict RTK enforcement across Antigravity & Claude Code."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name}" + (f" -- {detail}" if detail else ""))
    if not cond:
        fails.append(name)


def run(cmd: list[str], inp: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, input=inp, capture_output=True, text=True, cwd=ROOT)


def main() -> int:
    rtk_path = shutil.which("rtk")
    check("rtk binary on PATH", rtk_path is not None, rtk_path or "missing")
    if rtk_path:
        v = run([rtk_path, "--version"])
        check("rtk --version succeeds", v.returncode == 0, v.stdout.strip())

    # 1. Required project docs & plugin files
    for rel in (
        "RTK.md",
        "CLAUDE.md",
        ".agents/hooks.json",
        ".agents/plugins/rtk/plugin.json",
        ".agents/plugins/rtk/hooks.json",
        ".agents/plugins/rtk/rules/AGENTS.md",
        ".claude/settings.json",
        "scripts/hooks/rtk_strict_guard.py",
        "scripts/hooks/session_rules.sh",
        "scripts/hooks/token_audit_hook.py",
    ):
        check(f"exists: {rel}", os.path.exists(os.path.join(ROOT, rel)))

    # 2. Validate JSON configs
    for rel in (".agents/hooks.json", ".agents/plugins/rtk/hooks.json", ".claude/settings.json"):
        p = os.path.join(ROOT, rel)
        try:
            with open(p, encoding="utf-8") as fh:
                json.load(fh)
            check(f"valid JSON: {rel}", True)
        except Exception as exc:
            check(f"valid JSON: {rel}", False, str(exc))

    # 3. Live `rtk hook antigravity` & `rtk hook claude`
    if rtk_path:
        ag_in = json.dumps({
            "hookEventName": "PreToolUse",
            "toolCall": {"id": "t1", "name": "run_command", "args": {"CommandLine": "git status"}},
        })
        r_ag = run([rtk_path, "hook", "antigravity"], ag_in)
        check("rtk hook antigravity rewrites `git status`", '"rtk git status"' in r_ag.stdout, r_ag.stdout.strip())

        cl_in = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git status"}})
        r_cl = run([rtk_path, "hook", "claude"], cl_in)
        check("rtk hook claude rewrites `git status`", "rtk git status" in r_cl.stdout, r_cl.stdout.strip())

    # 4. Synthetic strict guard checks (Antigravity + Claude)
    guard = os.path.join(ROOT, "scripts/hooks/rtk_strict_guard.py")
    ag_cases = [
        ("ALLOW rtk git status", "rtk git status", "allow"),
        ("ALLOW rtk err python3 -m unittest", "rtk err python3 -m unittest", "allow"),
        ("ALLOW rtk proxy shasum -a 256 TODO.md", "rtk proxy shasum -a 256 TODO.md", "allow"),
        ("DENY bare git status", "git status", "deny"),
        ("DENY bare ls -la", "ls -la", "deny"),
        ("DENY bare grep -rn foo .", "grep -rn foo .", "deny"),
        ("DENY raw cat TODO.md", "cat TODO.md", "deny"),
        ("DENY pipe rtk | head", "rtk git log | head -n 5", "deny"),
    ]
    for label, cmd, expected in ag_cases:
        payload = json.dumps({
            "hookEventName": "PreToolUse",
            "toolCall": {"id": "t1", "name": "run_command", "args": {"CommandLine": cmd}},
        })
        r = run(["python3", guard, "--agent", "antigravity"], payload)
        try:
            dec = json.loads(r.stdout).get("decision")
        except Exception:
            dec = f"invalid:{r.stdout}"
        check(f"guard(antigravity) {label}", r.returncode == 0 and dec == expected, f"got={dec}")

    cl_cases = [
        ("ALLOW rtk git status", "rtk git status", False),
        ("DENY bare git status", "git status", True),
        ("DENY raw sed -n", "sed -n '1,10p' TODO.md", True),
        ("DENY pipe rtk | awk", "rtk ls | awk '{print $1}'", True),
    ]
    for label, cmd, expect_deny in cl_cases:
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})
        r = run(["python3", guard, "--agent", "claude"], payload)
        is_deny = '"permissionDecision": "deny"' in r.stdout
        has_upd = '"updatedInput"' in r.stdout
        check(f"guard(claude) {label}", r.returncode == 0 and is_deny == expect_deny and not has_upd, f"deny={is_deny}")

    # 5. SessionStart hooks
    r_ag_s = run(["sh", "scripts/hooks/session_rules.sh", "--agent", "antigravity"], "{}")
    check("session_rules(antigravity) valid injectSteps", r_ag_s.returncode == 0 and '"injectSteps"' in r_ag_s.stdout)

    r_cl_s = run(["sh", "scripts/hooks/session_rules.sh", "--agent", "claude"], '{"hook_event_name":"SubagentStart"}')
    check("session_rules(claude) SubagentStart valid JSON", r_cl_s.returncode == 0 and '"SubagentStart"' in r_cl_s.stdout)

    print(f"\nSUMMARY: {len(fails)} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
